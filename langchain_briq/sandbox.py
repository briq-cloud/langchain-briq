"""A Deep Agents sandbox backend that runs in a briq sandbox."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable, Mapping
from types import TracebackType

from briq_cloud import Briq, BriqError, Sandbox
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

#: The sizes briq offers. Anything else is refused before a request is sent.
SIZES = ("small", "medium", "large", "xlarge")

#: Seconds a command may run when the caller sets no timeout.
DEFAULT_TIMEOUT = 30 * 60

#: The API runs a command for 1 second to 24 hours.
_MIN_TIMEOUT = 1
_MAX_TIMEOUT = 24 * 60 * 60

#: A command longer than this many bytes is written to a file and run from there: the API takes
#: 8,192 bytes of command per call.
_MAX_INLINE_COMMAND = 6_000

#: Exit status of a command that outlived its timeout (the one of `timeout(1)`).
_TIMED_OUT_EXIT_CODE = 124


class BriqSandbox(BaseSandbox):
    """A Deep Agents backend over one briq sandbox.

    Make a new sandbox, or wrap one that exists:

    ```python
    from langchain_briq import BriqSandbox

    backend = BriqSandbox.create(size="medium")  # reads BRIQ_API_KEY
    try:
        agent = create_deep_agent(model=model, backend=backend)
        ...
    finally:
        backend.destroy()
    ```

    `execute` runs a shell command, `upload_files` and `download_files` move bytes. The file tools
    of Deep Agents (`read`, `write`, `edit`, `ls`, `glob`, `grep`) come from `BaseSandbox` and need
    `python3` in the sandbox.
    """

    def __init__(
        self,
        sandbox: Sandbox,
        *,
        default_timeout: int = DEFAULT_TIMEOUT,
        client: Briq | None = None,
    ) -> None:
        """Wrap an existing `briq_cloud.Sandbox`.

        Args:
            sandbox: The sandbox to run in.
            default_timeout: Seconds a command may run when `execute` gets no `timeout`.
            client: A client this backend closes with `destroy` or `close`. Leave it out when you
                manage the client yourself.
        """
        self._sandbox = sandbox
        self._default_timeout = default_timeout
        self._owned_client = client

    @classmethod
    def create(
        cls,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Briq | None = None,
        name: str | None = None,
        size: str | None = None,
        image: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        sleep: str | None = None,
        wait_timeout: float = 120.0,
        default_timeout: int = DEFAULT_TIMEOUT,
    ) -> BriqSandbox:
        """Make a sandbox and wait until it runs.

        Args:
            api_key: A briq API key. Defaults to `BRIQ_API_KEY`.
            base_url: The API origin. Defaults to `BRIQ_API_URL`, then the public API.
            client: A `briq_cloud.Briq` client of your own. You close it.
            name: A name for the sandbox.
            size: `small`, `medium`, `large` or `xlarge`.
            image: An OCI image with a shell and `python3`.
            env: Variables of the sandbox. They show in its status.
            secret_env: Variables kept write-only. A `BriqError` with code `unsupported` when
                the API cannot keep them so.
            sleep: Idle time before it sleeps: `"30m"`, `"3h"`, `"7d"` or `"never"`.
            wait_timeout: Seconds to wait until it runs.
            default_timeout: Seconds a command may run when `execute` gets no `timeout`.

        Raises:
            BriqError: With code `unsupported` for another `size`, and for the API's own refusals.
        """
        if size is not None and size not in SIZES:
            msg = f"Unsupported size {size!r}: use one of {', '.join(SIZES)}"
            raise BriqError("unsupported", msg)
        owned = client is None
        briq = client or Briq(api_key, base_url=base_url)
        try:
            sandbox = briq.create_sandbox(
                name=name,
                size=size,
                image=image,
                env=env,
                secret_env=secret_env,
                sleep=sleep,
                wait_timeout=wait_timeout,
            )
        except BaseException:
            if owned:
                briq.close()
            raise
        return cls(sandbox, default_timeout=default_timeout, client=briq if owned else None)

    @classmethod
    def connect(
        cls,
        sandbox_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Briq | None = None,
        default_timeout: int = DEFAULT_TIMEOUT,
    ) -> BriqSandbox:
        """Reconnect to a sandbox by id. A sleeping sandbox wakes on the next call."""
        owned = client is None
        briq = client or Briq(api_key, base_url=base_url)
        try:
            sandbox = briq.sandbox(sandbox_id)
        except BaseException:
            if owned:
                briq.close()
            raise
        return cls(sandbox, default_timeout=default_timeout, client=briq if owned else None)

    @property
    def id(self) -> str:
        """The sandbox's id: pass it to `connect` to reach the sandbox again."""
        return self._sandbox.id

    @property
    def sandbox(self) -> Sandbox:
        """The `briq_cloud.Sandbox` underneath, for what this backend does not wrap."""
        return self._sandbox

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Run a shell command and return its output and exit code.

        stderr follows stdout, inside `<stderr>` tags. A command that outlives `timeout` is
        stopped and ends with exit code 124.
        """
        if not command.strip():
            return ExecuteResponse(output="", exit_code=0, truncated=False)
        seconds = self._default_timeout if timeout is None else timeout
        seconds = min(max(seconds, _MIN_TIMEOUT), _MAX_TIMEOUT)
        if len(command.encode("utf-8", "replace")) > _MAX_INLINE_COMMAND:
            return self._execute_script(command, seconds)
        return self._run(command, seconds)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Write files, creating the directories above them. One response per file, in order."""
        responses: list[FileUploadResponse] = []
        for path, content in files:
            if not _is_absolute(path):
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            try:
                self._sandbox.write_file(path, content)
            except BriqError as error:
                normalized = _file_error(error)
                if normalized is None:
                    raise
                responses.append(FileUploadResponse(path=path, error=normalized))
            else:
                responses.append(FileUploadResponse(path=path))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Read files. One response per path, in order; a missing file is an error code."""
        responses: list[FileDownloadResponse] = []
        for path in paths:
            if not _is_absolute(path):
                responses.append(FileDownloadResponse(path=path, error="invalid_path"))
                continue
            try:
                content = self._sandbox.read_file(path)
            except BriqError as error:
                normalized = _file_error(error)
                if normalized is None:
                    raise
                responses.append(FileDownloadResponse(path=path, error=normalized))
            else:
                responses.append(FileDownloadResponse(path=path, content=content))
        return responses

    def destroy(self) -> None:
        """Destroy the sandbox and its disk, then close the client this backend made."""
        try:
            self._sandbox.destroy()
        finally:
            self.close()

    def close(self) -> None:
        """Close the client this backend made. The sandbox keeps running."""
        if self._owned_client is not None:
            self._owned_client.close()
            self._owned_client = None

    def __enter__(self) -> BriqSandbox:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Destroy the sandbox when the block ends."""
        try:
            self.destroy()
        except BriqError:
            if exc is None:
                raise

    def _run(self, command: str, seconds: float) -> ExecuteResponse:
        result = self._sandbox.exec(command, timeout=seconds)
        output = result.stdout
        if result.stderr:
            output = _append(output, f"<stderr>{result.stderr.rstrip(chr(10))}</stderr>")
        if result.timed_out or result.exit_code is None:
            note = f"Command timed out after {seconds:g} seconds"
            output = _append(output, note)
            return ExecuteResponse(output=output, exit_code=_TIMED_OUT_EXIT_CODE, truncated=False)
        return ExecuteResponse(output=output, exit_code=result.exit_code, truncated=False)

    def _execute_script(self, command: str, seconds: float) -> ExecuteResponse:
        """Run a command too long for one call from a script file, then delete the file."""
        path = f"/tmp/.langchain-briq-{uuid.uuid4().hex}.sh"
        self._sandbox.write_file(path, command.encode("utf-8"))
        try:
            return self._run(f"/bin/sh {path}", seconds)
        finally:
            _quietly(self._sandbox.remove, path)


def _append(output: str, line: str) -> str:
    """`line` on a line of its own after `output`."""
    if not output or output.endswith("\n"):
        return output + line
    return f"{output}\n{line}"


def _is_absolute(path: str) -> bool:
    return path.startswith("/") and "\0" not in path


def _quietly(call: Callable[..., object], *args: object) -> None:
    with contextlib.suppress(BriqError):
        call(*args)


def _file_error(error: BriqError) -> str | None:
    """The `FileOperationError` for a failed file call, or `None` when it is not the file's fault.

    `None` covers what the model cannot fix (credentials, limits, a sandbox that is gone): the
    caller raises it.
    """
    said = error.message.lower()
    if "permission denied" in said:
        return "permission_denied"
    if "is a directory" in said:
        return "is_directory"
    if error.code == "file_not_found":
        return "file_not_found"
    if error.code == "invalid_argument":
        return "invalid_path"
    return None
