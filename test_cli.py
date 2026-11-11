"""the words sprint takes on the command line besides a message: help, serve, config, update; and the newer-version note"""
import io
import json
import re
import subprocess
import time
from pathlib import Path

import pytest

import sprint
import sprint_tui as tui
from sprint import Agent
from sprint_tui import Screen, flat
from test_sprint import scripted


def test_an_ordinary_first_message_is_not_a_subcommand():
    assert sprint.cli([]) is None and sprint.cli(["fix", "the", "typo"]) is None and sprint.cli(["-m", "x"]) is None
    assert sprint.cli(["update", "the", "readme"]) is None and sprint.cli(["help", "me", "with", "this"]) is None


def test_help_lists_the_subcommands(capsys):
    assert sprint.cli(["help"]) == 0
    out = capsys.readouterr().out
    for part in ("sprint serve stop", "sprint config", "sprint update", "sprint help", sprint.VERSION):
        assert part in out


def test_the_version_is_the_same_everywhere(capsys):
    assert re.search(r'^version = "(.+)"$', Path(sprint.__file__).with_name("pyproject.toml").read_text(), re.M).group(1) == sprint.VERSION
    assert sprint.cli(["--version"]) == 0 and capsys.readouterr().out.strip() == "sprint " + sprint.VERSION


def test_config_makes_the_settings_file_if_need_be_and_opens_it(monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(sprint, "open_file", lambda path: opened.append(path) or True)
    assert not sprint.CONFIG.exists()
    assert sprint.cli(["config"]) == 0
    assert opened == [str(sprint.CONFIG)] and json.loads(sprint.CONFIG.read_text()) == {} and str(sprint.CONFIG) in capsys.readouterr().out
    sprint.save_config(theme="ocean")
    sprint.cli(["config"])
    assert json.loads(sprint.CONFIG.read_text())["theme"] == "ocean"  # an existing file is left as it is


def test_open_file_prefers_your_editor(monkeypatch):
    ran = []
    monkeypatch.setenv("EDITOR", "nano -w")
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setattr(sprint.subprocess, "run", lambda argv, **kw: ran.append(argv) or subprocess.CompletedProcess(argv, 0))
    assert sprint.open_file("/tmp/x.json") and ran == [["nano", "-w", "/tmp/x.json"]]


def test_serve_stop_stops_a_local_ollama_and_says_whether_it_stayed_down(monkeypatch, capsys):
    ran, alive = [], [True]
    monkeypatch.setattr(sprint.subprocess, "run", lambda argv, **kw: (ran.append(argv), alive.__setitem__(0, False), subprocess.CompletedProcess(argv, 0))[2])
    monkeypatch.setattr(sprint, "answers", lambda url: alive[0])
    monkeypatch.setattr(sprint.time, "sleep", lambda s: None)
    assert sprint.cli(["serve", "stop"]) == 0
    assert any("ollama" in " ".join(argv).lower() for argv in ran) and "stopped" in capsys.readouterr().out
    alive[0] = True
    monkeypatch.setattr(sprint.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0))  # it comes straight back
    assert sprint.cli(["serve", "stop"]) == 1 and "still answering" in capsys.readouterr().out


def test_serve_stop_leaves_a_server_on_another_machine_alone(monkeypatch, capsys):
    ran = []
    monkeypatch.setenv("SPRINT_URL", "http://10.0.0.5:11434")
    monkeypatch.setattr(sprint.subprocess, "run", lambda argv, **kw: ran.append(argv))
    assert sprint.cli(["serve", "stop"]) == 1 and not ran and "not on this machine" in capsys.readouterr().out


def test_serve_starts_it_and_status_only_looks(monkeypatch, capsys):
    monkeypatch.setattr(sprint, "serve", lambda url, note=None: True)
    assert sprint.cli(["serve"]) == 0 and "running" in capsys.readouterr().out
    monkeypatch.setattr(sprint, "answers", lambda url: False)
    assert sprint.cli(["serve", "status"]) == 1 and "not running" in capsys.readouterr().out


def test_update_pulls_a_git_checkout_and_reinstalls_anything_else(tmp_path, monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(sprint.subprocess, "run", lambda argv, **kw: ran.append(argv) or subprocess.CompletedProcess(argv, 0))
    (tmp_path / "checkout" / ".git").mkdir(parents=True)
    assert sprint.update(tmp_path / "checkout") == 0 and ran[-1][:1] == ["git"] and "pull" in ran[-1] and "--ff-only" in ran[-1]
    assert sprint.update(tmp_path / "site-packages") == 0
    assert ran[-1][1:5] == ["-m", "pip", "install", "--upgrade"] and ran[-1][-1].endswith("github.com/Vasiniks/sprint.git")
    monkeypatch.setattr(sprint.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1))
    assert sprint.update(tmp_path / "site-packages") == 1 and "did not" in capsys.readouterr().out


def test_newer_compares_versions_as_numbers():
    assert sprint.newer("0.10.0", "0.9.3") and sprint.newer("1.0", "0.9.9") and not sprint.newer("0.4.0", "0.4.0")
    assert not sprint.newer("0.3.9", "0.4.0") and not sprint.newer("nonsense", "0.4.0") and not sprint.newer("", "0.4.0")


def test_the_check_asks_at_most_once_a_day_and_remembers_the_answer(monkeypatch):
    asked = []
    monkeypatch.setattr(sprint, "fetch_latest", lambda: asked.append(1) or "99.0.0")
    assert sprint.check_update() == "99.0.0" and sprint.check_update() == "99.0.0" and asked == [1]
    monkeypatch.setattr(sprint.time, "time", lambda t=time.time(): t + 90000)  # the next day
    assert sprint.check_update() == "99.0.0" and asked == [1, 1]


def test_no_note_when_up_to_date_offline_or_switched_off(monkeypatch):
    monkeypatch.setattr(sprint, "fetch_latest", lambda: sprint.VERSION)
    assert sprint.check_update() == ""
    sprint.update_file().unlink()

    def offline():
        raise OSError("no network")
    monkeypatch.setattr(sprint, "fetch_latest", offline)
    assert sprint.check_update() == ""
    sprint.save_config(update_check=False)
    monkeypatch.setattr(sprint, "fetch_latest", lambda: pytest.fail("asked although switched off"))
    assert sprint.check_update() == ""


def test_fetch_latest_reads_the_version_line_of_the_published_project_file(monkeypatch):
    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass
    seen = []
    monkeypatch.setattr(sprint.urllib.request, "urlopen", lambda url, timeout=0: seen.append(url) or Reply(b'[project]\nname = "sprint"\nversion = "1.2.3"\n'))
    assert sprint.fetch_latest() == "1.2.3" and seen[0].startswith("https://raw.githubusercontent.com/Vasiniks/sprint/")


def test_the_screen_shows_the_version_and_a_newer_one_beside_it():
    s = Screen(Agent(chat=scripted(), out=io.StringIO()))
    assert flat(s.version_text()) == "sprint " + sprint.VERSION
    s.newer = "9.9.9"
    assert flat(s.version_text()) == f"sprint {sprint.VERSION} · 9.9.9 is out: sprint update" and s.version_text()[-1][1] == "warn"
