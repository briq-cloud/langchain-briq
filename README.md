# langchain-briq

[![PyPI](https://img.shields.io/pypi/v/langchain-briq)](https://pypi.org/project/langchain-briq/) [![CI](https://github.com/briq-cloud/langchain-briq/actions/workflows/ci.yml/badge.svg)](https://github.com/briq-cloud/langchain-briq/actions/workflows/ci.yml)

[briq](https://briq.cloud) sandbox integration for [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview). A Deep Agent runs its commands and file tools in a briq sandbox.

```python
from deepagents import create_deep_agent
from langchain_briq import BriqSandbox

backend = BriqSandbox.create(size="medium")  # reads BRIQ_API_KEY
try:
    agent = create_deep_agent(model="anthropic:claude-sonnet-4-5", backend=backend)
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Write fizzbuzz in Python and run it."}]}
    )
    print(result["messages"][-1].content)
finally:
    backend.destroy()  # removes the sandbox and its disk
```

Install: `pip install langchain-briq`. Python 3.11 or later. It depends on `deepagents` and [`briq-cloud`](https://pypi.org/project/briq-cloud/). Get an API key on the briq dashboard and set `BRIQ_API_KEY`.

## What it does

`BriqSandbox` is a `BaseSandbox`: it implements `execute`, `upload_files`, `download_files` and `id`, and the rest of the Deep Agents filesystem tools (`read`, `write`, `edit`, `ls`, `glob`, `grep`) come from `BaseSandbox`. They run `python3` in the sandbox, which the default image has.

| Method | Does |
| --- | --- |
| `BriqSandbox.create(...)` | Makes a sandbox and waits until it runs. |
| `BriqSandbox.connect(id)` | Reconnects to a sandbox. A sleeping one wakes on the next call. |
| `backend.execute(command, timeout=)` | Runs a shell command. Returns the output and the exit code. |
| `backend.upload_files([(path, bytes)])` | Writes files, creates the directories above them. |
| `backend.download_files([path])` | Reads files. |
| `backend.id` | The sandbox id. |
| `backend.destroy()` | Removes the sandbox and its disk. |
| `backend.sandbox` | The underlying [`briq_cloud.Sandbox`](https://pypi.org/project/briq-cloud/), for previews, stop, start. |

`async` methods (`aexecute`, `aupload_files`, `adownload_files`) run the same calls in a thread.

### Create options

```python
BriqSandbox.create(
    size="large",  # small, medium, large or xlarge
    image="ghcr.io/acme/tools:1",  # an OCI image with a shell and python3
    env={"MODE": "test"},  # shows in the sandbox's status
    secret_env={"API_TOKEN": token},  # write-only
    sleep="3h",  # idle time before it sleeps; "never" stays awake
    default_timeout=600,  # seconds a command may run without its own timeout
)
```

- Any other `size` raises `BriqError` with code `unsupported` before a request is sent.
- `secret_env` raises `unsupported` when the API cannot keep the values write-only.
- Pass `client=` a `briq_cloud.Briq` to share one client. You close it. Without it, `api_key` and `base_url` default to `BRIQ_API_KEY` and `BRIQ_API_URL`.
- `with BriqSandbox.create() as backend:` destroys the sandbox when the block ends.

### Reconnect

```python
backend = BriqSandbox.create(sleep="3h")
sandbox_id = backend.id
backend.close()  # closes the client, the sandbox keeps running

backend = BriqSandbox.connect(sandbox_id)
```

## Behavior

- `execute` returns stdout, then stderr inside `<stderr>` tags. A command that outlives its timeout is stopped and ends with exit code `124`. The timeout is 1 second to 24 hours; the default is 30 minutes.
- A command over about 6 KB is written to a file in the sandbox and run from there. The API takes 8 KB of command per call.
- `upload_files` and `download_files` return one response per path, in order. Error codes: `invalid_path` (a relative path), `file_not_found`, `permission_denied`, `is_directory`. A file over 100 MiB raises `BriqError` with code `quota`.
- Errors the model cannot fix (a wrong key, no credits, a plan limit, a sandbox that no longer exists) raise `briq_cloud.BriqError`. Its `code` is stable; see the [client's README](https://pypi.org/project/briq-cloud/).

## Not supported

- Streaming output: `execute` returns when the command ends.
- A working directory or environment per command. Put them in the command: `cd /work && make`.
- Output truncation: `truncated` is always `False`. A command's output over 32 MiB raises `BriqError` with code `quota`.

## Develop

Needs [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/briq-cloud/langchain-briq && cd langchain-briq
uv sync --group test
uv run pytest                     # offline
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

The tests run offline: `tests/unit_tests/test_sandbox.py` drives `BriqSandbox` through the real `briq-cloud` client against an in-memory API. `tests/unit_tests/test_standard_offline.py` runs the standard `SandboxIntegrationTests` suite of `langchain-tests` against a local Docker container (skipped without Docker).

Seven tests of that suite expect behavior that `BaseSandbox` itself no longer has (`glob` returns absolute paths, `write` overwrites): they are marked `xfail` in `tests/conftest.py`.

Against a real briq sandbox, with a key:

```sh
BRIQ_API_KEY=... uv run pytest tests/integration_tests
```

It makes one `small` sandbox for the run and destroys it.

## License

MIT
