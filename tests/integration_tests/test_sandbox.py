"""The standard Deep Agents sandbox suite against a real briq sandbox.

Needs `BRIQ_API_KEY` (and `BRIQ_API_URL` for an API other than the public one). It makes one
sandbox for the class and destroys it afterwards. Skipped without a key.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_tests.integration_tests import SandboxIntegrationTests

from langchain_briq import BriqSandbox

pytestmark = pytest.mark.skipif(
    not os.environ.get("BRIQ_API_KEY"), reason="BRIQ_API_KEY is not set"
)


class TestBriqSandboxStandard(SandboxIntegrationTests):
    @pytest.fixture(scope="class")
    def sandbox(self) -> Iterator[SandboxBackendProtocol]:
        backend = BriqSandbox.create(size="small", sleep="30m")
        try:
            yield backend
        finally:
            backend.destroy()
