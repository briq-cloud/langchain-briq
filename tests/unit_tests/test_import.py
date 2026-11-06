from langchain_briq import BriqSandbox, __version__


def test_public_api() -> None:
    assert BriqSandbox.__name__ == "BriqSandbox"
    assert __version__
