"""small faults found while the code was being tidied"""
import io
import json
import subprocess
import sys

import pytest

import sprint
from sprint import Agent
from test_sprint import scripted


def agent(*replies, **kw):
    return Agent(chat=scripted(*replies), out=io.StringIO(), **kw)


def text(rows):
    return "\n".join("".join(t for t, _ in r) for r in rows)


def test_the_help_for_steps_names_the_real_default(capsys):
    with pytest.raises(SystemExit):
        sprint.arguments(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "default 30" not in out and "8, 16 or 32" in out
    assert {steps for _, steps in sprint.EFFORT.values()} == {8, 16, 32}


def test_run_as_a_script_it_passes_on_its_exit_code(tmp_path):
    env = {**__import__("os").environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "SPRINT_URL": "http://127.0.0.1:9"}  # nothing answers there
    done = subprocess.run([sys.executable, sprint.__file__, "serve", "status"], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    assert "not running" in done.stdout and done.returncode == 1


def test_log_skips_a_line_that_is_not_one_of_its_own():
    sprint.log_file().parent.mkdir(parents=True, exist_ok=True)
    sprint.log_file().write_text('[1, 2]\n"words"\n' + json.dumps({"t": "2026-11-12 10:00:00", "kind": "message", "text": "hello"}) + "\n")
    out = text(sprint.command(agent(), "/log"))
    assert "hello" in out and "full log" in out


@pytest.mark.parametrize("line", ["/permissions forget ²", "/config ctx ²", "/config steps ٣x"])
def test_a_digit_that_is_no_number_is_turned_down_not_crashed_on(line):
    a = agent()
    a.allow = ["shell ls"]
    out = text(sprint.command(a, line))  # must not raise
    assert a.allow == ["shell ls"] and a.ctx != 2


def test_the_model_is_asked_to_load_once_the_server_is_up(monkeypatch):
    """warming went out before a server that was not running had been started, so it reached nothing"""
    order, up = [], [False]
    monkeypatch.setattr(sprint, "answers", lambda url: up[0])
    monkeypatch.setattr(sprint, "warm", lambda url, model: order.append("warm" if up[0] else "warm, to nobody"))
    monkeypatch.setattr(sprint, "serve", lambda url, status=None: order.append("serve") or up.__setitem__(0, True) or True)
    monkeypatch.setattr(sprint, "installed", lambda url, model: True)
    sprint.get_ready(agent())
    assert order == ["serve", "warm"]
    order.clear()
    sprint.get_ready(agent())  # already running: warmed at once, while the rest is checked
    assert order == ["warm", "serve"]


def test_a_lone_bang_in_the_plain_session_is_nothing_to_send():
    a = agent("should never be asked")
    sprint.plain_line(a, "!")
    sprint.plain_line(a, "!   ")
    assert len(a.msgs) == 1


def test_the_mode_key_typed_by_hand_does_not_bring_the_plain_session_down(monkeypatch):
    lines = iter(["/~ draft", "/bye"])
    monkeypatch.setattr(sprint, "plain_prompt", lambda *a, **k: next(lines))
    a = agent(mode="ask")
    sprint.plain_session(a)  # no history entry to take back: must not raise
    assert a.mode == "edits"


# ---------- found in the screen while it was being tidied ----------

import threading

import sprint_tui as tui
from sprint_tui import Screen


def held():
    """a screen whose model waits until it is let go, and the list of what it was sent"""
    go, sent = threading.Event(), []

    def chat(msgs):
        sent.append(msgs[-1]["content"])
        go.wait(5)
        yield "ok"

    return Screen(Agent(chat=chat, out=io.StringIO(), mode="auto")), go, sent


def test_a_queued_message_keeps_what_was_pasted_into_it():
    """sending one message threw away every paste and attachment, those of messages still waiting too: the model got
    the label, [Pasted #1 +5 lines], and never the text"""
    s, go, sent = held()
    s.submit("first")
    s.paste("one\ntwo\nthree\nfour\nfive")
    label = s.buf.strip()
    s.buf = f"how long is {label} ?"
    s.cur = len(s.buf)
    s.key("\r")  # enter while the first is still being answered: it waits in the queue
    assert s.queue == [f"how long is {label} ?"] and label in s.pastes
    s.queue.insert(0, "second")  # and something goes out before it
    go.set()
    s.worker.join(5)
    s.advance()
    s.worker.join(5)
    assert label in s.pastes  # still there for the message it belongs to
    go.set()
    s.worker.join(5)
    s.advance()  # the queue's turn
    s.worker.join(5)
    assert "one\ntwo\nthree\nfour\nfive" in sent[-1] and "[Pasted" not in sent[-1]
    assert s.pastes == {}  # and gone once it has gone out


def test_a_new_paste_never_takes_the_number_of_one_still_waiting():
    s, go, sent = held()
    s.paste("a\nb\nc\nd")
    one = s.buf.strip()
    s.buf = ""
    s.paste("e\nf\ng\nh")
    two = s.buf.strip()
    assert one != two
    s.pastes.pop(one)  # the first has gone out
    s.buf = ""
    s.paste("i\nj\nk\nl")
    assert s.buf.strip() not in (one, two) and len(s.pastes) == 2
    go.set()


def test_a_paste_in_a_command_of_your_own_is_run_with_its_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ran = []
    monkeypatch.setattr(tui.sprint.Agent, "bang", lambda self, cmd: ran.append(cmd) or "")
    s = Screen(Agent(chat=scripted(), out=io.StringIO()))
    s.paste("echo one\necho two\necho three\necho four")
    s.submit("!" + s.buf.strip())
    s.worker.join(5)
    assert ran and "echo one\necho two" in ran[0] and "[Pasted" not in ran[0]


def test_a_question_that_goes_away_mid_frame_does_not_bring_the_screen_down():
    """the wait for an answer ends on the agent's thread; the screen may be half way through drawing the question"""
    s = Screen(Agent(chat=scripted(), out=io.StringIO()))
    s.pending = None
    assert len(s.choices()) == 3
    s.answer_key("y")  # a key that arrives just as the question is withdrawn

    class Once:  # there when asked whether, gone when read again
        def __init__(self):
            self.reads = 0

        def __get__(self, obj, kind):
            self.reads += 1
            return tui.sprint.Call("shell", "", "ls", 0, 0) if self.reads == 1 else None

        def __set__(self, obj, value):
            pass

    class Flaky(Screen):
        pending = Once()

    f = Flaky(Agent(chat=scripted(), out=io.StringIO()))
    f.input_rows(80)  # must not raise
