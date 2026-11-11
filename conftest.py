import pytest

import sprint


@pytest.fixture(autouse=True)
def private_config(tmp_path, monkeypatch):
    """no test may read or write the real settings file"""
    monkeypatch.setattr(sprint, "CONFIG", tmp_path / "sprint-config.json", raising=False)
