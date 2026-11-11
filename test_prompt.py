"""the prompt bar: its rules, the multi-line prompt, the queue, and the status line with its sprint"""
import curses
import io
import threading

import sprint
import sprint_tui as tui
from sprint import Agent
from sprint_tui import Screen
from test_sprint import scripted


def flat(spans):
    return "".join(t for t, _ in spans)


def screen(*replies, **kw):
    kw.setdefault("mode", "auto")
    return Screen(Agent(chat=scripted(*replies), out=io.StringIO(), **kw))


def finish(s):
    s.worker.join(10)
    assert not s.worker.is_alive()


def typed(s, text):
    for ch in text:
        s.key(ch)


# ---------- the working animation ----------

def test_the_working_animation_held_still_is_a_comet_at_the_start_of_a_slanted_track():
    text = flat(tui.dash())
    assert 14 <= len(text) <= 18 and text[0] + text[-1] == tui.ENDS
    assert tui.ENDS == ("◢◤" if tui.VT else "//")
    assert text[1:5] == "░▒▓█" and set(text[5:-1]) == {" "}


def test_the_comet_sprints_left_to_right_faster_and_faster():
    frames = [flat(tui.dash(tui.LAP * f)) for f in (0, 0.3, 0.6, 0.9)]
    assert len({len(f) for f in frames}) == 1 and all(f[0] + f[-1] == tui.ENDS for f in frames)
    heads = [f.index("█") for f in frames]
    steps = [b - a for a, b in zip(heads, heads[1:])]
    assert steps == sorted(steps) and steps[0] > 0 and steps[-1] > 2 * steps[0]  # it gathers speed
    assert frames[2][heads[2] - 3:heads[2] + 1] == "░▒▓█"  # the tail fades out behind the head
    assert tui.dash(0.4) == tui.dash(0.4)  # nothing but the time decides the picture


def test_each_lap_of_the_comet_is_the_next_of_yellow_cyan_and_magenta():
    laps = [{role for _, role in tui.dash(tui.LAP * (n + 0.5))} for n in range(4)]
    assert laps == [{"yellow"}, {"cyan"}, {"magenta"}, {"yellow"}]
    assert 0.6 <= tui.LAP <= 2


# ---------- the bar: two rules, a marked prompt, a ghost of a hint ----------

def test_the_bar_has_the_model_on_its_top_rule_and_the_mode_on_its_bottom_rule():
    s = screen(mode="edits")
    rows, _ = s.input_rows(60, used=7)
    top, prompt, bottom = rows
    assert len(flat(top)) == len(flat(bottom)) == 60 and set(flat(top)[:20]) == {"─"}
    assert "Qwen3.5-0.8B" in flat(top) and "7%" in flat(top) and "edits" not in flat(top)
    assert flat(bottom).startswith("── edits ─") and ("edits", "edits") in bottom  # the mode, in the mode's colour


def test_the_prompt_is_marked_in_the_mode_colour_and_the_hint_is_a_ghost():
    s = screen(mode="ask")
    prompt = s.input_rows(60)[0][1]
    assert flat(prompt).startswith("░▒▓█ > Ask anything") and (">", "ask") in prompt
    assert prompt[-1] == ("Ask anything…  / for commands", "ghost")
    s.buf = "hello"
    prompt = s.input_rows(60)[0][1]
    assert flat(prompt) == "░▒▓█ > hello" and prompt[-1] == ("hello", "")


# ---------- more than one line ----------

def test_line_breaks_lay_the_text_out_and_later_lines_sit_under_the_first():
    s = screen()
    s.buf = "make it so\na second line\n"
    rows, lines = s.input_rows(60)
    assert lines == ["make it so", "a second line", ""] and len(rows) == 5
    assert [flat(r) for r in rows[1:4]] == ["░▒▓█ > make it so", "       a second line", "       "]


def test_the_bar_says_where_the_cursor_is():
    s = screen()
    s.buf = "one\ntwo"
    for cur, where in ((0, (1, 7)), (3, (1, 10)), (4, (2, 7)), (7, (2, 10))):
        s.cur = cur
        s.input_rows(60)
        assert s.caret == where  # the row within the bar, and the column from its left edge
    s.buf, s.cur = "x" * 50, 50  # a long line wraps at the width, and the cursor goes with it
    rows, lines = s.input_rows(40)
    assert lines == ["x" * 33, "x" * 17] and s.caret == (2, 24)
    s.buf, s.cur = "x" * 33, 33  # a full line: the cursor waits on the next one
    assert s.input_rows(40)[1] == ["x" * 33, ""] and s.caret == (2, 7)


def test_a_tall_message_shows_six_lines_around_the_cursor():
    s = screen()
    s.buf = "\n".join(f"line {i}" for i in range(10))
    s.cur = len(s.buf)
    rows, lines = s.input_rows(60)
    assert lines == [f"line {i}" for i in range(4, 10)] and len(rows) == 8 and s.caret == (6, 13)
    s.cur = 0
    assert s.input_rows(60)[1] == [f"line {i}" for i in range(6)] and s.caret == (1, 7)
    s.cur = s.buf.index("line 5")
    assert s.input_rows(60)[1] == [f"line {i}" for i in range(4, 10)] and s.caret == (2, 7)


def test_shift_enter_and_alt_enter_start_a_new_line_however_the_terminal_says_it():
    for seq in ("\x1b\r", "\x1b\n", "\x1b[27;2;13~", "\x1b[13;2u"):
        s = screen()
        typed(s, "one")
        for ch in seq:
            s.take(ch)
        typed(s, "two")
        assert s.buf == "one\ntwo" and s.cur == 7 and s.worker is None


def test_other_modified_keys_the_terminal_now_reports_are_dropped_not_typed():
    s = screen()
    for ch in "a\x1b[27;5;9~b\x1b[57441;2uc":  # ctrl+tab, and a bare shift: nothing sprint has a use for
        s.take(ch)
    s.idle()
    assert s.buf == "abc" and s.esc is None


def test_ctrl_j_is_a_new_line_once_enter_arrives_as_a_return():
    s = screen("ok")
    assert s.nl is False
    typed(s, "hi")
    s.nl = True  # what the loop sets after telling curses not to turn Enter into "\n"
    s.key("\n")
    typed(s, "there")
    assert s.buf == "hi\nthere" and s.worker is None
    s.key("\r")
    finish(s)
    assert s.items[0] == {"t": "user", "text": "hi\nthere"} and s.buf == ""  # sent as it was typed


def test_a_backslash_then_enter_is_a_new_line_too():
    s = screen("ok")
    typed(s, "first\\")
    s.key("\n")
    assert s.buf == "first\n" and s.cur == 6 and s.worker is None
    typed(s, "second")
    s.key("\n")
    finish(s)
    assert s.items[0]["text"] == "first\nsecond"


def test_home_and_end_stay_on_the_line_and_up_still_recalls_history():
    s = screen("ok")
    s.buf, s.cur = "one\ntwo", 5
    s.key(curses.KEY_HOME)
    assert s.cur == 4
    s.key(curses.KEY_END)
    assert s.cur == 7
    s.cur = 1
    s.key(curses.KEY_END)
    assert s.cur == 3
    s.key("\n")
    finish(s)
    s.key(curses.KEY_UP)
    assert s.buf == "one\ntwo"


class Canvas:
    """a stand-in for the curses window that keeps what was drawn on it, and where the cursor was left"""
    def __init__(self, h=24, w=90):
        self.h, self.w, self.at = h, w, None
        self.erase()

    def getmaxyx(self):
        return self.h, self.w

    def erase(self):
        self.grid = [[" "] * self.w for _ in range(self.h)]

    def addstr(self, y, x, text, attr=0):
        self.grid[y][x:x + len(text)] = text

    def chgat(self, *a):
        pass

    def bkgdset(self, *a):
        pass

    def move(self, y, x):
        self.at = (y, x)

    def refresh(self):
        pass

    def rows(self):
        return ["".join(r).rstrip() for r in self.grid]


def drawn(s, monkeypatch, h=24, w=90):
    """the screen as text, without the side panels, and the canvas it was drawn on"""
    monkeypatch.setattr(tui.curses, "curs_set", lambda n: None)
    s.sidebar = s.history = False
    c = Canvas(h, w)
    s.draw(c)
    return c.rows(), c


def slow(*replies, **kw):
    """a screen whose model says nothing until chat.go is set, or until it is hung up on, as esc does"""
    go, started, it = threading.Event(), threading.Event(), iter(replies)

    class Sock:
        def shutdown(self, how):
            go.set()

    def chat(msgs):
        sprint.SOCKETS[threading.get_ident()] = Sock()  # what a real request records once it has connected
        started.set()
        go.wait(5)
        yield next(it)

    chat.go, chat.started = go, started
    kw.setdefault("mode", "auto")
    return Screen(Agent(chat=chat, out=io.StringIO(), **kw)), chat


# ---------- the status line ----------

def test_the_status_line_says_what_is_happening_for_how_long_and_how_much():
    s, chat = slow("All done.")
    s.submit("go")
    assert chat.started.wait(5)
    s.began -= 12
    row = s.status_row()
    assert flat(row).startswith(" " + tui.ENDS[0]) and row[1][1] in tui.HUES  # the sprint leads it
    assert f"{tui.ENDS[1]} working (12s | ↑ {tui.kilo(s.agent.used())} tokens | medium effort)" in flat(row)
    s.emit("x" * 35)  # the reply has begun: count what comes down, by the agent's own measure of a token
    assert "answering (12s | ↓ 10 tokens | medium effort)" in flat(s.status_row())
    s.show_thought("y" * 7000)
    assert "↓ 2.0k tokens" in flat(s.status_row())
    s.status("thinking")  # the next step: the conversation goes up again
    assert "↑" in flat(s.status_row())
    s.began -= 113
    s.note, s.agent.effort = "", "high"
    assert "working (2m 05s | " in flat(s.status_row()) and "| high effort)" in flat(s.status_row())
    s.agent.think = True
    assert "thinking (2m 05s | " in flat(s.status_row()) and "| thinking with high effort)" in flat(s.status_row())
    chat.go.set()
    finish(s)


def test_the_status_line_starts_afresh_with_each_message_and_holds_still_without_motion():
    s = screen("one", "two")
    s.submit("first")
    finish(s)
    assert s.got == len("one\n") and s.down
    before = s.began
    s.submit("second")
    assert s.began >= before and not s.down
    finish(s)
    assert s.got == len("two\n")
    s.motion = False
    assert s.status_row()[1] == tui.dash()[0]


def test_while_a_turn_runs_the_status_is_above_the_bar_and_the_footer_is_free_for_a_note(monkeypatch):
    s, chat = slow("ok")
    s.submit("go")
    assert chat.started.wait(5)
    s.tell("copied 12 characters")
    rows, c = drawn(s, monkeypatch)
    assert rows[19].startswith("   " + tui.ENDS[0]) and "working (0s | ↑" in rows[19]  # where the blank row was
    assert rows[20].startswith("  ───") and "Qwen3.5-0.8B" in rows[20]
    assert sorted(rows[21][2:6]) == sorted("░▒▓█") and "> Ask anything" in rows[21] and c.at == (21, 9)
    assert rows[22].startswith("  ── auto ───")
    assert "copied 12 characters" in rows[23] and rows[23].endswith("esc interrupt  ctrl+o tools  /help")
    assert "thinking" not in rows[23] and "working" not in rows[23]
    chat.go.set()
    finish(s)
    rows, _ = drawn(s, monkeypatch)
    assert rows[19] == "" and rows[23].endswith("tab mode  ctrl+o tools  /help") and "pg" not in rows[23]


def test_the_approval_question_sits_between_the_same_two_rules(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\necho 1\n```", "ok", mode="ask")
    s.submit("go")
    for _ in range(200):
        if s.pending:
            break
        s.worker.join(0.02)
    rows, _ = drawn(s, monkeypatch)
    assert "waiting for you (" in rows[18] and "Qwen" in rows[19] and "allow shell  echo 1" in rows[20]
    assert "always this" in rows[21] and rows[22].startswith("  ── ask ───")
    s.key("n")
    finish(s)


def test_the_home_screen_has_the_same_bar_under_the_logo(monkeypatch):
    s = screen()
    s.motion = False
    rows, c = drawn(s, monkeypatch, h=30, w=100)
    bar = next(i for i, r in enumerate(rows) if "> Ask anything" in r)
    assert "Qwen3.5-0.8B" in rows[bar - 1] and "── auto ─" in rows[bar + 1] and c.at[0] == bar
    assert "░░░░░░░░" in rows[bar - 8] and "pgup" not in "".join(rows)
    assert rows[bar].index(">") == c.at[1] - 2


# ---------- the queue ----------

def said(s):
    return [i["text"] for i in s.items if i["t"] == "user"]


def stopped(s):
    return any(i["t"] == "note" and "interrupted" in i["text"] for i in s.items)


def test_enter_while_a_turn_runs_queues_the_message_and_it_runs_when_the_turn_is_over():
    s, chat = slow("one", "two")
    s.submit("first")
    assert chat.started.wait(5)
    typed(s, "second")
    s.key("\n")
    assert s.queue == ["second"] and s.buf == "" and said(s) == ["first"]
    s.advance()  # not yet: the first turn is still running
    assert s.queue == ["second"] and said(s) == ["first"]
    chat.go.set()
    finish(s)
    assert s.active()  # something is still to happen, so the loop must not go to sleep
    s.advance()
    assert s.queue == []
    finish(s)
    assert said(s) == ["first", "second"] and not stopped(s)
    s.advance()
    assert not s.busy() and not s.active()
    s.key(curses.KEY_UP)
    assert s.buf == "second"  # a queued message is in the history like any other


def test_a_command_typed_while_a_turn_runs_is_not_queued_and_enter_alone_does_nothing():
    s, chat = slow("one")
    s.submit("first")
    assert chat.started.wait(5)
    typed(s, "/sidebar")
    s.key("\n")
    assert s.queue == [] and s.buf == "" and s.sidebar is False  # it ran at once
    s.key("\n")
    assert s.busy() and not s.agent.cancel  # nothing queued: an empty Enter stops nothing
    chat.go.set()
    finish(s)


def test_enter_on_an_empty_prompt_stops_the_turn_so_the_queue_starts_at_once():
    s, chat = slow("too late", "second answer")
    s.submit("first")
    assert chat.started.wait(5)
    typed(s, "second")
    s.key("\n")
    s.key("\n")
    finish(s)
    assert stopped(s) and s.queue == ["second"] and s.buf == ""
    s.advance()
    finish(s)
    assert said(s) == ["first", "second"] and "second answer" in s.items[-1]["text"]


def test_escape_stops_the_turn_and_puts_the_queue_back_on_the_prompt():
    s, chat = slow("too late")
    s.submit("first")
    assert chat.started.wait(5)
    for text in ("second", "third"):
        typed(s, text)
        s.key("\n")
    typed(s, "half")
    s.key("\x1b")
    finish(s)
    assert stopped(s) and s.queue == [] and s.buf == "second\nthird\nhalf" and s.cur == len(s.buf)
    s.advance()
    assert not s.busy() and said(s) == ["first"]  # nothing runs by surprise


def test_ctrl_c_and_stopping_at_an_approval_hand_the_queue_back_too(tmp_path, monkeypatch):
    s, chat = slow("too late")
    s.submit("first")
    assert chat.started.wait(5)
    typed(s, "second")
    s.key("\n")
    s.key("\x03")
    finish(s)
    assert stopped(s) and s.buf == "second" and s.queue == [] and not s.quit
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\necho 1\n```", "ok", mode="ask")
    s.submit("go")
    for _ in range(200):
        if s.pending:
            break
        s.worker.join(0.02)
    s.queue = ["later"]
    s.advance()  # an approval is waiting: the queue waits with it
    assert s.queue == ["later"]
    s.key("\x1b")
    finish(s)
    assert s.buf == "later" and s.queue == []


def test_clear_empties_the_queue():
    s = screen()
    s.queue = ["one", "two"]
    s.submit("/clear")
    assert s.queue == []


def test_queued_messages_are_listed_above_the_bar_three_at_most(monkeypatch):
    s, chat = slow("ok")
    s.submit("go")
    assert chat.started.wait(5)
    s.queue = ["then run the tests", "and\ncommit"]
    rows, _ = drawn(s, monkeypatch)
    assert "working (" in rows[17] and rows[18:20] == ["    queued 1  then run the tests", "    queued 2  and …"]
    assert "Qwen3.5-0.8B" in rows[20] and "enter run queued now  esc interrupt" in rows[23]
    s.queue += ["three", "four", "five"]
    rows, _ = drawn(s, monkeypatch)
    assert "working (" in rows[15] and rows[18:20] == ["    queued 3  three", "    +2 more"] and "go" in rows[1]
    chat.go.set()
    finish(s)


# ---------- leaving ----------

def test_ctrl_c_asks_to_be_pressed_again_before_it_quits(monkeypatch):
    s = screen()
    typed(s, "half a thought")
    s.key("\x03")
    assert s.buf == "" and not s.quit and not s.note_text()  # with text on the prompt it only clears the line
    s.key("\x03")
    assert not s.quit and "ctrl+c again to quit" in s.note_text()
    s.key("x")  # any other key takes it back, and the note with it
    assert not s.note_text()
    s.key("\x15")
    s.key("\x03")
    assert not s.quit
    s.key("\x03")
    assert s.quit


def test_the_second_ctrl_c_must_come_within_two_seconds_and_ctrl_d_still_leaves_at_once(monkeypatch):
    s, now = screen(), [1000.0]
    monkeypatch.setattr(tui.time, "time", lambda: now[0])
    s.key("\x03")
    now[0] += 2.5
    assert not s.note_text()  # the offer has lapsed, and so has its note
    s.key("\x03")
    assert not s.quit and s.note_text()
    now[0] += 1
    s.key("\x03")
    assert s.quit
    s = screen()
    s.key("\x04")
    assert s.quit


class Window:
    """a stand-in for the curses window: no keys, and the first wait for one ends the loop"""
    def __init__(self, s):
        self.s = s

    def keypad(self, on):
        pass

    def timeout(self, ms):
        pass

    def get_wch(self):
        self.s.quit = True
        raise curses.error("no input")


def test_the_loop_asks_for_enter_as_a_return_and_for_shift_enter_to_be_reported(monkeypatch, capsys):
    s, called = screen(), []
    for name in ("raw", "nonl"):
        monkeypatch.setattr(tui.curses, name, lambda name=name: called.append(name))
    monkeypatch.setattr(tui.curses, "has_colors", lambda: False)
    monkeypatch.setattr(s, "set_mouse", lambda: None)
    monkeypatch.setattr(s, "draw", lambda scr: None)
    s.loop(Window(s))
    out = capsys.readouterr().out
    assert ("nonl" in called and s.nl is True) if tui.VT else ("nonl" not in called and s.nl is False)
    assert ("\033[>4;1m" in out and out.endswith("\033[>4;0m")) if tui.VT else out == ""


def test_the_status_says_working_unless_the_model_is_set_to_reason_first(monkeypatch):
    s = screen()
    monkeypatch.setattr(s, "busy", lambda: True)
    s.status("thinking")  # what the agent calls the start of every step
    assert " working " in flat(s.status_row()) and "thinking" not in flat(s.status_row())
    s.agent.think = True
    assert " thinking " in flat(s.status_row())
    s.status("running make")
    assert " running make " in flat(s.status_row())


def test_ctrl_j_only_means_a_new_line_where_enter_is_known_to_arrive_differently(monkeypatch):
    calls = []
    monkeypatch.setattr(tui.curses, "nonl", lambda: calls.append(1))
    monkeypatch.setattr(tui, "VT", True)
    s = screen()
    s.free_ctrl_j()
    assert s.nl and calls == [1]
    monkeypatch.setattr(tui, "VT", False)  # Windows: untried there, so "\n" stays Enter and can never swallow a send
    s = screen()
    s.free_ctrl_j()
    assert not s.nl and calls == [1]


def test_a_terminal_that_refuses_to_change_enter_still_starts(monkeypatch):
    def refuse():
        raise tui.curses.error("nonl() returned ERR")
    monkeypatch.setattr(tui.curses, "nonl", refuse)
    monkeypatch.setattr(tui, "VT", True)
    s = screen()
    s.free_ctrl_j()
    assert not s.nl


def test_the_verb_follows_what_is_happening(monkeypatch):
    s = screen()
    assert s.verb() == "working"
    s.down = True
    assert s.verb() == "answering"  # the reply is streaming
    s.down, s.note = False, "write hello.py · 3 lines"
    assert s.verb() == "write hello.py · 3 lines"  # a tool call being written out
    s.note = ""
    for tool, doing in (("shell", "running"), ("read", "reading"), ("write", "writing"), ("edit", "editing"), ("fetch", "fetching")):
        s.show_call(sprint.Call(tool, "a.txt", "ls", 0, 0))
        assert s.verb() == doing
    s.show_result("ok")
    assert s.verb() == "working"  # the tool is done: back to the model
    s.pending = object()
    assert s.verb() == "waiting for you"
