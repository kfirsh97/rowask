import pytest


@pytest.fixture(autouse=True)
def gemini_fakes(monkeypatch):
    """Most agent-loop tests play back a fake Gemini client; Claude tests pass provider="claude" themselves.
    The app's own default (Claude) is not what these fakes speak."""
    monkeypatch.setenv("PROVIDER", "gemini")
