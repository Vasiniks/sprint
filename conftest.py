import pytest

import sprint


@pytest.fixture(autouse=True)
def private_config(tmp_path, monkeypatch):
    """no test may read or write the real settings file"""
    monkeypatch.setattr(sprint, "CONFIG", tmp_path / "sprint-config.json", raising=False)


@pytest.fixture(autouse=True)
def no_window_scripting(monkeypatch):
    """no test may recolour a real terminal window (on a Mac that is done by scripting the Terminal app)"""
    import sprint_tui
    monkeypatch.setattr(sprint_tui, "apple_window", lambda tty, rgb: None, raising=False)
