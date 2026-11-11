"""The parts that differ on Windows, run for real there (CI has a Windows job). Skipped everywhere else."""
import io
import os
import time

import pytest

import sprint
from sprint import Agent, edit, read, shell, write

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")


def test_shell_runs_a_command():  # through Git Bash when it is installed, else PowerShell
    assert shell("echo hi") == "hi"


def test_shell_also_works_through_powershell_alone(monkeypatch):
    monkeypatch.setattr(sprint, "bash_path", lambda: None)
    assert shell("echo hi") == "hi" and shell("exit 3").endswith("[exit 3]")
    assert shell("echo one && echo two").split() == ["one", "two"]  # && is translated for the built-in PowerShell


def test_shell_reports_a_failing_exit_code():
    assert shell("exit 3").endswith("[exit 3]")


def test_cd_carries_over_to_the_next_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    shell("cd sub")
    assert os.path.realpath(os.getcwd()) == os.path.realpath(tmp_path / "sub")


def test_a_command_that_runs_too_long_is_killed():
    began = time.time()
    assert "timed out" in shell("sleep 30", timeout=2) and time.time() - began < 15


def test_files_round_trip_with_accents(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write("caf\u00e9.txt", "na\u00efve\n")
    edit("caf\u00e9.txt", "na\u00efve\n====\nna\u00eff\n")
    assert read("caf\u00e9.txt") == "na\u00eff"


def test_the_agent_runs_a_tool_and_answers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sprint, "CONFIG", tmp_path / "config.json")
    replies = iter(["```shell\necho hi\n```", "It said hi."])
    a = Agent(chat=lambda msgs: iter([next(replies)]), out=io.StringIO(), mode="auto")
    assert a.run("say hi") == "It said hi."
    assert "hi" in a.msgs[3]["content"] and "This is Windows" in a.msgs[0]["content"]


def test_the_full_screen_module_loads():
    import sprint_tui
    assert sprint_tui.logo_rows()
