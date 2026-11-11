"""what keeps a model from hurting the folder it works in, and sprint from falling over when one does: the working
folder vanishing, commands that delete, and a question nobody answers"""
import io
import os
import shutil

import pytest

import sprint
import sprint_tui as tui
from sprint import Agent
from test_sprint import scripted

posix = pytest.mark.skipif(sprint.WINDOWS, reason="Windows will not delete a folder something is working in")


@pytest.fixture
def nest(tmp_path, monkeypatch):
    """tmp/a/b, with sprint working in b"""
    inner = tmp_path / "a" / "b"
    inner.mkdir(parents=True)
    monkeypatch.chdir(inner)
    assert os.path.samefile(sprint.folder(), inner)
    return tmp_path


@posix
def test_a_deleted_working_folder_is_stepped_out_of_to_the_nearest_one_left(nest):
    shutil.rmtree(nest / "a")
    with pytest.raises(OSError):
        os.getcwd()  # what brought the whole screen down
    assert os.path.samefile(sprint.folder(), nest) and os.path.samefile(os.getcwd(), nest)


@posix
def test_the_file_listing_for_the_model_survives_it_too(nest):
    (nest / "kept.txt").write_text("x")
    shutil.rmtree(nest / "a")
    assert "kept.txt" in sprint.here()


@posix
def test_a_command_that_deletes_the_folder_it_ran_in_says_so_and_leaves_sprint_standing(nest):
    out = sprint.shell("cd .. && rm -r b && echo gone")
    assert "gone" in out and "working folder" in out and "deleted" in out
    assert os.path.samefile(os.getcwd(), nest / "a")
    assert "a" in out.split("working folder")[1]  # and the model is told where it is now


@posix
def test_the_screen_draws_on_after_the_working_folder_is_deleted(nest, monkeypatch):
    from test_popups import Scr
    monkeypatch.setattr(tui.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(tui.curses, "color_pair", lambda n: n << 8)
    s, scr = tui.Screen(Agent(chat=scripted(), out=io.StringIO())), Scr(30, 120)
    s.draw(scr)
    shutil.rmtree(nest / "a")
    s.draw(scr)  # the opening screen names the folder
    s.items = [{"t": "text", "text": "hello"}]
    s.sidebar = True
    s.draw(scr)  # and so does the side panel
    assert os.path.samefile(os.getcwd(), nest) and "hello" in "".join(scr.text(y) for y in range(6))  # the conversation, drawn


# ---------- deleting: files in the folder are fine, the folder itself and anything outside it are put to you ----------

@pytest.mark.parametrize("cmd", ["rm notes.txt", "rm -rf build", "cd src && rm *.pyc", "sudo rm -rf /tmp/x", "/bin/rm x", "rmdir old",
                                 "ls | xargs rm", "find . -name '*.log' -delete", "git clean -fd", "unlink a", "shred -u key",
                                 "python3 -c 'import shutil; shutil.rmtree(\"x\")'", "python -c \"import os; os.remove('a')\"",
                                 "node -e \"require('fs').rmSync('x', {recursive: true})\"", "Remove-Item -Recurse x", "echo hi; rm x"])
def test_commands_that_delete_are_recognised(cmd):
    assert sprint.deletes(cmd)


@pytest.mark.parametrize("cmd", ["ls -la", "cat rm-notes.txt", "docker run --rm img", "git status", "npm run format", "echo confirm",
                                 "grep -r term .", "python3 app.py --farm", "mkdir -p out && cp a out/", "find . -name '*.py'"])
def test_and_ordinary_commands_are_not(cmd):
    assert not sprint.deletes(cmd)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    """tmp/proj, with sprint working in it, a file inside and one beside it"""
    here = tmp_path / "proj"
    (here / "build").mkdir(parents=True)
    (here / "gone.txt").write_text("x")
    (tmp_path / "gone.txt").write_text("x")
    monkeypatch.chdir(here)
    return here


@pytest.mark.parametrize("cmd", ["rm notes.txt", "rm -rf build", "rm -f src/*.pyc", "rm ./a ./build/c", "rmdir build", "rm -- -odd", "unlink gone.txt",
                                 "echo hi; rm gone.txt", "rm -rf build/*", "sudo rm -f gone.txt", "FORCE=1 rm gone.txt", "rm *.log"])
def test_deleting_what_is_inside_the_folder_is_an_ordinary_command(proj, cmd):
    assert sprint.deletion(cmd) == ""


@pytest.mark.parametrize("cmd", ["rm -rf .", "rm -rf ./", "rm -rf $PWD", 'rm -rf "$PWD"', "rm -rf ../proj", "rm -rf *", "rm -rf ./*", "rm -rf ..",
                                 "rm -rf /", "rmdir .", "rm -rf build . ", "rm -rf .* *", "rm -rf ${PWD}"])
def test_deleting_the_folder_sprint_works_in_is_told_apart(proj, cmd):
    assert sprint.deletion(cmd) == "own"
    assert sprint.deletion(f"rm -rf {proj}") == "own" and sprint.deletion(f"rm -rf {proj.parent}") == "own"  # it, or what holds it


@pytest.mark.parametrize("cmd", ["rm ../gone.txt", "rm -rf ../other", "rm /etc/hosts", "rm ~/notes.txt", "rm -rf $HOME/x", "rm -rf /tmp/zzz-not-here",
                                 "rm -rf ../*", "rm gone.txt ../gone.txt"])
def test_deleting_anything_outside_the_folder_is_told_apart(proj, cmd):
    assert sprint.deletion(cmd) == "outside"


@pytest.mark.parametrize("cmd", ["cd .. && rm -rf proj", "find . -delete", "git clean -fd", "ls | xargs rm", "rm -rf $TARGET", "rm $(cat list)",
                                 "python3 -c 'import shutil; shutil.rmtree(\"x\")'", "Remove-Item x", "rm -rf `pwd`", "cd build && rm -rf x",
                                 "rm 'unfinished"])
def test_a_deletion_that_cannot_be_read_is_treated_as_one_to_ask_about(proj, cmd):
    assert sprint.deletion(cmd) == "unknown"


def test_a_command_that_deletes_nothing_is_no_deletion(proj):
    assert sprint.deletion("ls -la") == "" and sprint.deletion("docker run --rm img") == ""


def asking(answer, **kw):
    """an agent in auto mode whose every question is recorded and given this answer"""
    kw.setdefault("mode", "auto")
    a = Agent(chat=scripted(), out=io.StringIO(), **kw)
    a.asked = []
    a.ask = lambda call: a.asked.append(call) or answer
    return a


def rm(path="../gone.txt"):
    return sprint.Call("shell", "", f"rm {path}", 0, 0)


def test_in_auto_mode_files_in_the_folder_go_without_a_question(proj):
    a = asking(False)
    a.do(rm("gone.txt"))
    a.do(sprint.Call("shell", "", "rm -rf build", 0, 0))
    assert a.asked == [] and not (proj / "gone.txt").exists() and not (proj / "build").exists()


def test_in_auto_mode_a_deletion_outside_the_folder_still_asks(proj):
    a = asking(False)
    assert "hi" in a.do(sprint.Call("shell", "", "echo hi", 0, 0)) and a.asked == []
    assert a.do(rm()).startswith("denied") and len(a.asked) == 1 and (proj.parent / "gone.txt").exists()
    a.ask = lambda call: True
    a.do(rm())
    assert not (proj.parent / "gone.txt").exists()


def test_deleting_the_working_folder_always_asks_and_a_no_keeps_it(proj):
    a = asking(False)
    for cmd in ("rm -rf .", "rm -rf ../proj", "cd .. && rm -rf proj"):
        assert a.do(sprint.Call("shell", "", cmd, 0, 0)).startswith("denied")
    assert len(a.asked) == 3 and proj.is_dir() and (proj / "gone.txt").exists()


def test_always_is_never_remembered_for_such_a_deletion_and_no_allow_rule_frees_one(proj, monkeypatch):
    saved = []
    monkeypatch.setattr(sprint, "save_config", lambda **kw: saved.append(kw))
    a = asking("always")
    a.do(rm())
    assert a.allow == [] and saved == []  # yes, this once
    a.allow = ["shell rm*", "shell *"]
    a.do(rm("../other.txt"))
    assert len(a.asked) == 2  # asked again all the same


def test_with_nobody_to_ask_such_a_deletion_is_refused_whatever_the_mode(proj, monkeypatch):
    a = Agent(chat=scripted(), out=io.StringIO(), mode="auto")
    monkeypatch.setattr(sprint.sys.stdin, "isatty", lambda: False, raising=False)
    said = a.do(rm())
    assert said.startswith("denied") and "delet" in said and "-y" not in said and (proj.parent / "gone.txt").exists()


# ---------- a question nobody answers does not hold the work up ----------

def test_an_unanswered_question_sets_the_call_aside_and_the_model_is_told_to_carry_on(proj):
    a = asking("later")
    said = a.do(rm())
    assert said.startswith("set aside") and "carry on" in said.lower() and (proj.parent / "gone.txt").exists()
    assert [c.body for c in a.parked] == ["rm ../gone.txt"]
    a.do(rm())
    assert len(a.parked) == 1  # the same call waits once, however often it is tried


def test_what_was_set_aside_runs_once_you_allow_it_the_next_time_the_model_asks(proj):
    a = asking("later")
    a.do(rm())
    assert "rm ../gone.txt" in a.approve(0) and a.parked == []
    a.do(rm())
    assert not (proj.parent / "gone.txt").exists() and len(a.asked) == 1  # it ran without a second question
    (proj.parent / "gone.txt").write_text("x")
    a.do(rm())
    assert len(a.asked) == 2 and (proj.parent / "gone.txt").exists()  # once only


def test_how_long_a_question_waits_is_a_setting_twenty_seconds_unless_changed(tmp_path, monkeypatch):
    a = Agent(chat=scripted(), out=io.StringIO())
    assert a.ask_timeout == 20 and sprint.SETTINGS["ask_timeout"] is int
    sprint.command(a, "/config ask_timeout 45")
    assert a.ask_timeout == 45 and sprint.load_config()["ask_timeout"] == 45
    assert any("ask_timeout" in "".join(t for t, _ in r) for r in sprint.command(a, "/config"))


# ---------- the same, on the full screen ----------

import threading
import time

from sprint_tui import flat


def waiting(call, **kw):
    """a screen with this call on it, as it is when the agent is about to ask"""
    kw.setdefault("mode", "auto")
    s = tui.Screen(Agent(chat=scripted(), out=io.StringIO(), **kw))
    s.show_call(call)
    return s


def test_a_question_left_alone_gives_up_after_the_wait_and_says_where_it_went():
    s = waiting(rm())
    s.agent.ask_timeout = 0.3
    began = time.time()
    assert s.ask(rm()) == "later" and 0.25 <= time.time() - began < 2
    assert s.pending is None and "set aside" in s.note_text() and "/permissions" in s.note_text()
    s.show_result("set aside: this needs the user's say-so and they have not answered.")
    assert s.items[-1]["state"] == "parked"


def test_an_answer_in_time_is_taken_and_a_key_starts_the_wait_again():
    s = waiting(rm())
    s.agent.ask_timeout = 0.6
    got = []
    t = threading.Thread(target=lambda: got.append(s.ask(rm())))
    t.start()
    for _ in range(4):  # still deciding: moving the choice shows you are there
        time.sleep(0.3)
        assert s.pending is not None
        s.key(tui.curses.KEY_RIGHT)
    s.key("y")
    t.join(2)
    assert got == [True]


def test_with_the_wait_switched_off_a_question_waits_for_ever(monkeypatch):
    s = waiting(rm())
    s.agent.ask_timeout = 0
    got = []
    t = threading.Thread(target=lambda: got.append(s.ask(rm())), daemon=True)
    t.start()
    time.sleep(0.5)
    assert got == [] and s.pending is not None and "s " not in flat(s.input_rows(90)[0][2])[-8:]
    s.key("n")
    t.join(2)
    assert got == [False]


def test_a_deletion_can_be_allowed_once_or_refused_but_never_for_ever():
    s = waiting(rm())
    s.pending, s.choice, s.deadline = rm(), 0, time.time() + 14.4
    question, keys = s.input_rows(100)[0][1:3]
    assert "always" not in flat(keys) and "once" in flat(keys) and "no" in flat(keys)
    assert "delete" in flat(question)  # and it says why it is asking
    assert "14s" in flat(keys) or "15s" in flat(keys)  # how long it will wait
    s.key("a")
    assert s.pending is not None and not s.gate.is_set()  # "always" is not on offer
    s.key(tui.curses.KEY_RIGHT)
    assert s.choice == 1
    s.key(tui.curses.KEY_RIGHT)
    assert s.choice == 0  # two choices, round and round
    s.pending = sprint.Call("shell", "", "ls", 0, 0)
    assert "always" in flat(s.input_rows(100)[0][2])  # anything else can still be


def test_what_is_waiting_shows_under_the_prompt_and_in_permissions_where_enter_allows_it_once():
    s = waiting(rm())
    assert "waiting" not in flat(s.mode_row())
    s.agent.parked = [rm()]
    assert "1 waiting" in flat(s.mode_row()) and "/permissions" in flat(s.mode_row())
    s.submit("/permissions")
    rows, can = s.shown()
    assert any("rm ../gone.txt" in flat(r["s"]) for r in rows)
    s.popup["sel"] = next(n for n, r in enumerate(can) if r["v"] == ("parked", 0))
    s.popup_key("\r")
    assert s.agent.parked == [] and s.agent.passes == [("shell", "rm ../gone.txt")] and "allowed once" in s.note_text()


def test_the_question_says_what_kind_of_deletion_it_is():
    s = waiting(rm())
    s.pending, s.choice = rm(), 0
    assert "outside this folder" in flat(s.input_rows(110)[0][1])
    s.pending = sprint.Call("shell", "", "rm -rf .", 0, 0)
    assert "this very folder" in flat(s.input_rows(110)[0][1]) and "always" not in flat(s.input_rows(110)[0][2])
    s.pending = sprint.Call("shell", "", "rm notes.txt", 0, 0)  # in ask mode a file in the folder is asked about like anything else
    assert "delete" not in flat(s.input_rows(110)[0][1]) and "always" in flat(s.input_rows(110)[0][2])


# ---------- the mode you leave in is the mode you come back to ----------

def test_changing_the_mode_is_remembered_and_starting_in_one_is_not(tmp_path):
    a = Agent(chat=scripted(), out=io.StringIO(), mode="auto")  # sprint -y: for this run
    assert "mode" not in sprint.load_config()
    a.cycle()
    assert sprint.load_config()["mode"] == a.mode == "ask"
    sprint.command(a, "/mode edits")
    assert sprint.load_config()["mode"] == "edits"
    sprint.command(a, "/permissions auto")
    assert sprint.load_config()["mode"] == "auto"
