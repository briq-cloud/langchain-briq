"""A stand-in for `briq_cloud.Sandbox` that runs in a local Docker container.

It has the methods `BriqSandbox` calls (`id`, `exec`, `write_file`, `read_file`, `remove`,
`destroy`) and answers as the briq API does: a file call that fails raises a `BriqError` with the
same code and message shape. It lets the standard Deep Agents suite run without a briq account.
It proves the backend, not the API: the HTTP layer is `briq-cloud`'s, tested in its own package.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid

from briq_cloud import BriqError, ExecResult

#: A Debian image with `python3`, like the sandbox image.
IMAGE = "python:3.12-slim"


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


class DockerSandbox:
    def __init__(self) -> None:
        self.id = f"bq_test_{uuid.uuid4().hex[:12]}"
        subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", self.id, IMAGE, "sleep", "infinity"],
            check=True,
            capture_output=True,
        )

    def exec(self, command: str, *, timeout: float = 60.0, **_: object) -> ExecResult:
        try:
            done = subprocess.run(
                ["docker", "exec", self.id, "/bin/sh", "-c", command],
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ExecResult("", "", None, timed_out=True)
        return ExecResult(
            done.stdout.decode("utf-8", "replace"),
            done.stderr.decode("utf-8", "replace"),
            done.returncode,
        )

    def write_file(self, path: str, data: bytes, *, mode: int | None = None) -> None:
        script = 'mkdir -p "$(dirname "$1")" && cat > "$1"'
        done = subprocess.run(
            ["docker", "exec", "-i", self.id, "/bin/sh", "-c", script, "briq", path],
            input=data,
            capture_output=True,
            check=False,
        )
        if done.returncode != 0:
            detail = done.stderr.decode("utf-8", "replace").strip().rsplit(": ", 1)[-1]
            raise BriqError("provider", f"write failed: {detail}")

    def read_file(self, path: str) -> bytes:
        done = subprocess.run(
            ["docker", "exec", self.id, "cat", path], capture_output=True, check=False
        )
        if done.returncode != 0:
            raise BriqError("file_not_found", "read failed: " + done.stderr.decode().strip())
        return done.stdout

    def remove(self, path: str, recursive: bool = False) -> None:
        self.exec(f"rm -f {path}")

    def destroy(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.id], capture_output=True, check=False)
