from __future__ import annotations

import pytest

#: Tests of the standard suite (langchain-tests 1.1.9) that disagree with `BaseSandbox` itself
#: (deepagents 0.7.34): `glob` returns absolute paths and `write` overwrites a file. Both run
#: through `execute` only, so they fail for every backend, not for this one.
_STALE_STANDARD_TESTS = {
    "test_glob",
    "test_glob_basic_pattern",
    "test_glob_with_directories",
    "test_glob_hidden_files_explicitly",
    "test_glob_with_character_class",
    "test_glob_with_question_mark",
    "test_write_existing_file_fails",
}

_REASON = "langchain-tests and deepagents disagree on this behavior of BaseSandbox"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        function = getattr(item, "function", None)
        if (
            "Standard" in getattr(item.cls, "__name__", "")  # type: ignore[attr-defined]
            and function is not None
            and function.__name__ in _STALE_STANDARD_TESTS
        ):
            item.add_marker(pytest.mark.xfail(reason=_REASON, strict=False))
