"""the model sprint opens with before you have chosen one, and the one it suggests where it asks for a name"""
import io

import pytest

import sprint
import sprint_tui as tui
from sprint import Agent
from sprint_tui import Screen, flat
from test_sprint import scripted

MAGI = "hf.co/GEHIRNS/magi-1.2-2B"


def test_a_first_run_opens_with_magi_2b(monkeypatch):
    monkeypatch.delenv("SPRINT_MODEL", raising=False)
    assert sprint.DEFAULT_MODEL == MAGI
    assert sprint.build_agent(sprint.arguments([])).model == MAGI  # nothing saved, nothing asked for
    assert Agent(chat=scripted("ok"), out=io.StringIO()).model == MAGI


def test_the_model_you_used_last_still_comes_first(monkeypatch):
    monkeypatch.delenv("SPRINT_MODEL", raising=False)
    sprint.save_config(model="qwen3.5:0.8b")
    assert sprint.build_agent(sprint.arguments([])).model == "qwen3.5:0.8b"
    assert sprint.build_agent(sprint.arguments(["-m", "smollm2:135m"])).model == "smollm2:135m"


def test_it_counts_as_installed_under_the_name_ollama_gives_it(monkeypatch):
    monkeypatch.setattr(sprint, "model_names", lambda url: [MAGI + ":latest"])
    assert sprint.installed("http://localhost:11434", sprint.DEFAULT_MODEL)  # so a second run does not offer to pull it again


def test_the_command_lines_help_names_it(capsys):
    with pytest.raises(SystemExit):
        sprint.arguments(["--help"])
    said = " ".join(capsys.readouterr().out.split())
    assert "magi 1.2 2B" in said and "Qwen" not in said


@pytest.fixture
def popups(monkeypatch):
    monkeypatch.setattr(tui.curses, "curs_set", lambda n: None)
    s = Screen(Agent(chat=scripted("ok"), out=io.StringIO(), mode="auto"))
    s.models = ["qwen3.5:0.8b"]
    return s


def test_where_a_name_is_asked_for_it_is_the_example(popups):
    s = popups
    s.model_pick(("name", ""))
    assert MAGI in "".join(flat(r["s"]) for r in s.popup["rows"])
    s.model_pick(("hf", ""))
    assert "magi 1.2 2b" in "".join(flat(r["s"]) for r in s.popup["rows"])
