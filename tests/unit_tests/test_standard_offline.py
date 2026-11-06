"""The standard Deep Agents sandbox suite, run offline against a local Docker container.

Skipped without Docker. `tests/integration_tests/test_sandbox.py` runs the same suite against briq.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_tests.integration_tests import SandboxIntegrationTests

from langchain_briq import BriqSandbox
from tests.docker_sandbox import DockerSandbox, docker_available

pytestmark = pytest.mark.skipif(not docker_available(), reason="Docker is not running")


class TestBriqSandboxStandardOffline(SandboxIntegrationTests):
    @pytest.fixture(scope="class")
    def sandbox(self) -> Iterator[SandboxBackendProtocol]:
        backend = BriqSandbox(DockerSandbox())  # type: ignore[arg-type]
        try:
            yield backend
        finally:
            backend.destroy()
