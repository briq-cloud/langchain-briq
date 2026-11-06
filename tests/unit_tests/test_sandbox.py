"""`BriqSandbox` against an in-memory briq API: every call goes through the real client."""

from __future__ import annotations

import pytest
from briq_cloud import BriqError
from deepagents.backends.protocol import SandboxBackendProtocol
from deepagents.backends.sandbox import BaseSandbox

from langchain_briq import BriqSandbox
from tests.fake_briq import FakeBriq, Out


@pytest.fixture
def api() -> FakeBriq:
    return FakeBriq()


@pytest.fixture
def backend(api: FakeBriq) -> BriqSandbox:
    return BriqSandbox.create(client=api.client())


def test_is_a_deep_agents_sandbox(backend: BriqSandbox) -> None:
    assert isinstance(backend, BaseSandbox)
    assert isinstance(backend, SandboxBackendProtocol)
    assert backend.id == "bq_1"


class TestCreate:
    def test_sends_the_options(self, api: FakeBriq) -> None:
        backend = BriqSandbox.create(
            client=api.client(),
            name="agent",
            size="large",
            image="ghcr.io/acme/box:1",
            env={"MODE": "test"},
            sleep="3h",
        )
        body = api.sent("POST", "/v1/briqs")[0]
        assert body["recipe"] == "sandbox"
        assert (body["name"], body["size"], body["image"], body["sleep"]) == (
            "agent",
            "large",
            "ghcr.io/acme/box:1",
            "3h",
        )
        assert body["env"] == {"MODE": "test"}
        assert backend.sandbox.info.size == "large"

    @pytest.mark.parametrize("size", ["tiny", "2xl", "Medium", ""])
    def test_a_size_that_does_not_exist_fails_before_any_request(
        self, api: FakeBriq, size: str
    ) -> None:
        with pytest.raises(BriqError) as raised:
            BriqSandbox.create(client=api.client(), size=size)
        assert raised.value.code == "unsupported"
        assert api.requests == []

    @pytest.mark.parametrize("size", ["small", "medium", "large", "xlarge"])
    def test_the_four_sizes(self, api: FakeBriq, size: str) -> None:
        BriqSandbox.create(client=api.client(), size=size)
        assert api.sent("POST", "/v1/briqs")[0]["size"] == size

    def test_secret_env_fails_when_the_api_cannot_keep_it(self) -> None:
        api = FakeBriq(literal_secrets=False)
        with pytest.raises(BriqError) as raised:
            BriqSandbox.create(client=api.client(), secret_env={"TOKEN": "s3cret"})
        assert raised.value.code == "unsupported"
        assert api.sent("POST", "/v1/briqs") == []

    def test_a_wrong_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        api = FakeBriq()
        client = api.client()
        client._key = "wrong"
        with pytest.raises(BriqError) as raised:
            BriqSandbox.create(client=client)
        assert raised.value.code == "auth"

    def test_closes_the_client_it_made_when_creating_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BRIQ_API_KEY", raising=False)
        with pytest.raises(BriqError) as raised:
            BriqSandbox.create()
        assert raised.value.code == "auth"


class TestLifecycle:
    def test_destroy_deletes_the_sandbox(self, api: FakeBriq, backend: BriqSandbox) -> None:
        backend.destroy()
        assert api.sandboxes == {}
        assert api.sent("DELETE", "/v1/briqs/bq_1") != []

    def test_the_context_manager_destroys(self, api: FakeBriq) -> None:
        with BriqSandbox.create(client=api.client()) as backend:
            assert backend.id in api.sandboxes
        assert api.sandboxes == {}

    def test_connect_reaches_a_sandbox_by_id(self, api: FakeBriq, backend: BriqSandbox) -> None:
        again = BriqSandbox.connect(backend.id, client=api.client())
        assert again.id == backend.id

    def test_connect_to_a_missing_sandbox_raises(self, api: FakeBriq) -> None:
        with pytest.raises(BriqError) as raised:
            BriqSandbox.connect("bq_nope", client=api.client())
        assert raised.value.code == "not_found"

    def test_a_client_you_pass_stays_open(self, api: FakeBriq) -> None:
        client = api.client()
        BriqSandbox.create(client=client).destroy()
        assert client.create_sandbox().id  # still usable


class TestExecute:
    def test_returns_output_and_exit_code(self, api: FakeBriq, backend: BriqSandbox) -> None:
        api.on_exec = lambda cmd, body: Out(stdout="hello\n", code=0)
        result = backend.execute("echo hello")
        assert (result.output, result.exit_code, result.truncated) == ("hello\n", 0, False)

    def test_runs_the_command_in_a_shell(self, api: FakeBriq, backend: BriqSandbox) -> None:
        backend.execute("ls | wc -l")
        body = api.sent("POST", "/v1/briqs/bq_1/exec")[0]
        assert body["cmd"] == ["sh", "-c", "ls | wc -l"]

    def test_a_failing_command_is_a_result_not_an_error(
        self, api: FakeBriq, backend: BriqSandbox
    ) -> None:
        api.on_exec = lambda cmd, body: Out(stderr="boom\n", code=3)
        result = backend.execute("false")
        assert result.exit_code == 3
        assert result.output == "<stderr>boom</stderr>"

    def test_stderr_follows_stdout(self, api: FakeBriq, backend: BriqSandbox) -> None:
        api.on_exec = lambda cmd, body: Out(stdout="out", stderr="warn\n")
        assert backend.execute("x").output == "out\n<stderr>warn</stderr>"

    def test_the_timeout_goes_to_the_api(self, api: FakeBriq, backend: BriqSandbox) -> None:
        backend.execute("sleep 1", timeout=90)
        assert api.sent("POST", "/v1/briqs/bq_1/exec")[0]["timeoutMs"] == 90_000

    def test_the_default_timeout_is_the_backends(self, api: FakeBriq) -> None:
        backend = BriqSandbox.create(client=api.client(), default_timeout=120)
        backend.execute("true")
        assert api.sent("POST", "/v1/briqs/bq_1/exec")[0]["timeoutMs"] == 120_000

    @pytest.mark.parametrize(("asked", "sent"), [(0, 1_000), (86_400 * 2, 86_400_000)])
    def test_a_timeout_outside_the_api_range_is_clamped(
        self, api: FakeBriq, backend: BriqSandbox, asked: int, sent: int
    ) -> None:
        backend.execute("true", timeout=asked)
        assert api.sent("POST", "/v1/briqs/bq_1/exec")[0]["timeoutMs"] == sent

    def test_a_command_that_outlives_its_timeout_ends_with_124(
        self, api: FakeBriq, backend: BriqSandbox
    ) -> None:
        api.on_exec = lambda cmd, body: Out(stdout="partial\n", timed_out=True, code=-1)
        result = backend.execute("sleep 100", timeout=5)
        assert result.exit_code == 124
        assert result.output == "partial\nCommand timed out after 5 seconds"

    def test_a_blank_command_does_nothing(self, api: FakeBriq, backend: BriqSandbox) -> None:
        assert backend.execute("  ").exit_code == 0
        assert api.sent("POST", "/v1/briqs/bq_1/exec") == []

    def test_a_long_command_runs_from_a_file(self, api: FakeBriq, backend: BriqSandbox) -> None:
        script = "echo " + "x" * 20_000
        backend.execute(script)
        (path, content), *_ = ((p, c) for p, c in api.files.items())
        assert path.startswith("/tmp/.langchain-briq-")
        assert content == script.encode()
        commands = [b["cmd"] for b in api.sent("POST", "/v1/briqs/bq_1/exec")]
        assert commands[0] == ["sh", "-c", f"/bin/sh {path}"]
        assert commands[-1] == ["rm", "-f", "--", path]  # and the file is removed

    def test_an_api_failure_raises(self, api: FakeBriq, backend: BriqSandbox) -> None:
        del api.sandboxes["bq_1"]
        with pytest.raises(BriqError) as raised:
            backend.execute("true")
        assert raised.value.code == "not_found"

    async def test_aexecute(self, api: FakeBriq, backend: BriqSandbox) -> None:
        api.on_exec = lambda cmd, body: Out(stdout="async\n")
        result = await backend.aexecute("echo async")
        assert result.output == "async\n"


class TestFiles:
    def test_upload_then_download(self, backend: BriqSandbox) -> None:
        data = bytes(range(256))
        up = backend.upload_files([("/work/a.bin", data)])
        assert [(r.path, r.error) for r in up] == [("/work/a.bin", None)]
        down = backend.download_files(["/work/a.bin"])
        assert (down[0].path, down[0].content, down[0].error) == ("/work/a.bin", data, None)

    def test_responses_follow_the_order_of_the_request(self, backend: BriqSandbox) -> None:
        files = [(f"/work/{n}.txt", n.encode()) for n in "cab"]
        backend.upload_files(files)
        down = backend.download_files([p for p, _ in files])
        assert [(r.path, r.content) for r in down] == files

    def test_a_missing_file_is_file_not_found(self, backend: BriqSandbox) -> None:
        (response,) = backend.download_files(["/work/nope.txt"])
        assert (response.content, response.error) == (None, "file_not_found")

    def test_one_missing_file_does_not_stop_the_others(self, backend: BriqSandbox) -> None:
        backend.upload_files([("/work/here.txt", b"x")])
        down = backend.download_files(["/work/nope.txt", "/work/here.txt"])
        assert [r.error for r in down] == ["file_not_found", None]
        assert down[1].content == b"x"

    def test_a_relative_path_is_invalid_and_sends_nothing(
        self, api: FakeBriq, backend: BriqSandbox
    ) -> None:
        up = backend.upload_files([("rel.txt", b"x")])
        down = backend.download_files(["rel.txt"])
        assert (up[0].error, down[0].error) == ("invalid_path", "invalid_path")
        assert api.sent("PUT", ".*") == []

    def test_a_path_with_a_nul_byte_is_invalid(self, backend: BriqSandbox) -> None:
        assert backend.upload_files([("/a\0b", b"")])[0].error == "invalid_path"

    @pytest.mark.parametrize(
        ("said", "code"),
        [
            ("write failed: Permission denied", "permission_denied"),
            ("write failed: Is a directory", "is_directory"),
        ],
    )
    def test_a_refused_write_has_a_standard_code(
        self, api: FakeBriq, backend: BriqSandbox, said: str, code: str
    ) -> None:
        api.write_refusals["/ro/file"] = said
        assert backend.upload_files([("/ro/file", b"x")])[0].error == code

    def test_an_error_the_model_cannot_fix_raises(
        self, api: FakeBriq, backend: BriqSandbox
    ) -> None:
        api.write_refusals["/x"] = "write failed: something odd"
        with pytest.raises(BriqError):
            backend.upload_files([("/x", b"x")])

    def test_an_empty_batch(self, backend: BriqSandbox) -> None:
        assert backend.upload_files([]) == []
        assert backend.download_files([]) == []

    async def test_async_files(self, backend: BriqSandbox) -> None:
        (up,) = await backend.aupload_files([("/work/a.txt", b"a")])
        (down,) = await backend.adownload_files(["/work/a.txt"])
        assert (up.error, down.content) == (None, b"a")


class TestFileTools:
    """The tools of Deep Agents work over `execute` and `upload_files`."""

    def test_write_sends_the_content_as_an_upload(
        self, api: FakeBriq, backend: BriqSandbox
    ) -> None:
        result = backend.write("/work/note.md", "# hi")
        assert result.error is None
        assert api.files["/work/note.md"] == b"# hi"
