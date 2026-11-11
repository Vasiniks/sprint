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

def test_the_working_animation_held_still_is_four_filled_cells_at_the_start_of_a_slanted_track():
    full, empty = tui.CELLS
    text = flat(tui.dash())
    assert tui.CELLS == ("▰▱" if tui.VT else "■□") and len(text) == tui.RUN == 14  # the same fourteen cells as the website's
    assert text[:4] == full * 4 and text[4:] == empty * 10
    assert {role for cell, role in tui.dash()[4:]} == {"ghost"}  # the track itself is grey


def test_the_runner_sprints_left_to_right_faster_and_faster():
    frames = [tui.dash(tui.LAP * f) for f in (0, 0.3, 0.6, 0.9)]
    assert all(len(f) == tui.RUN for f in frames)
    heads = [max(x for x, (cell, role) in enumerate(f) if role != "ghost") for f in frames]
    steps = [b - a for a, b in zip(heads, heads[1:])]
    assert steps == sorted(steps) and steps[0] > 0 and steps[-1] > 2 * steps[0]  # it gathers speed
    tail = [role for cell, role in frames[2][heads[2] - 3:heads[2] + 1]]
    assert tail == ["yellow.dim", "yellow.dim", "yellow", "yellow.bold"]  # it fades out behind the head
    assert tui.dash(0.4) == tui.dash(0.4)  # nothing but the time decides the picture


def test_each_lap_is_the_next_of_yellow_cyan_and_magenta():
    laps = [{role.split(".")[0] for _, role in tui.dash(tui.LAP * (n + 0.5)) if role != "ghost"} for n in range(4)]
    assert laps == [{"yellow"}, {"cyan"}, {"magenta"}, {"yellow"}]
    assert 0.6 <= tui.LAP <= 2
    for hue in tui.HUES:  # the bold and dim of each colour are real roles, in that colour
        assert tui.ROLE[hue + ".bold"] == (tui.ROLE[hue][0], curses.A_BOLD) and tui.ROLE[hue + ".dim"] == (tui.ROLE[hue][0], curses.A_DIM)


# ---------- the bar: two rules, a marked prompt, a ghost of a hint ----------

def test_the_bar_has_the_model_on_its_top_rule_and_the_mode_on_its_bottom_rule():
    s = screen(mode="edits")
    rows, _ = s.input_rows(60, used=7)
    top, prompt, bottom = rows
    assert len(flat(top)) == len(flat(bottom)) == 60 and set(flat(top)[:20]) == {"─"}
    assert "Qwen3.5-0.8B" in flat(top) and "7%" in flat(top) and "edits" not in flat(top)
    assert set(flat(bottom)) == {"─"} and "edits" not in flat(bottom)  # a plain rule: the mode sits under it


def test_the_prompt_is_marked_in_the_mode_colour_and_the_hint_is_a_ghost():
    s = screen(mode="ask")
    prompt = s.input_rows(60)[0][1]
    assert flat(prompt).startswith(tui.MARK + " Ask anything") and prompt[0] == (tui.MARK, "ask")  # just the mark: no shade trail
    assert prompt[-1] == ("Ask anything…  / for commands  ! for shell", "ghost")
    s.buf = "hello"
    prompt = s.input_rows(60)[0][1]
    assert flat(prompt) == tui.MARK + " hello" and prompt[-1] == ("hello", "")


# ---------- more than one line ----------

def test_line_breaks_lay_the_text_out_and_later_lines_sit_under_the_first():
    s = screen()
    s.buf = "make it so\na second line\n"
    rows, lines = s.input_rows(60)
    assert lines == ["make it so", "a second line", ""] and len(rows) == 5
    assert [flat(r) for r in rows[1:4]] == [tui.MARK + " make it so", "  a second line", "  "]


def test_the_bar_says_where_the_cursor_is():
    s = screen()
    s.buf = "one\ntwo"
    for cur, where in ((0, (1, 2)), (3, (1, 5)), (4, (2, 2)), (7, (2, 5))):
        s.cur = cur
        s.input_rows(60)
        assert s.caret == where  # the row within the bar, and the column from its left edge
    s.buf, s.cur = "x" * 50, 50  # a long line wraps at the width, and the cursor goes with it
    rows, lines = s.input_rows(40)
    assert lines == ["x" * 38, "x" * 12] and s.caret == (2, 14)
    s.buf, s.cur = "x" * 38, 38  # a full line: the cursor waits on the next one
    assert s.input_rows(40)[1] == ["x" * 38, ""] and s.caret == (2, 2)


def test_a_tall_message_shows_six_lines_around_the_cursor():
    s = screen()
    s.buf = "\n".join(f"line {i}" for i in range(10))
    s.cur = len(s.buf)
    rows, lines = s.input_rows(60)
    assert lines == [f"line {i}" for i in range(4, 10)] and len(rows) == 8 and s.caret == (6, 8)
    s.cur = 0
    assert s.input_rows(60)[1] == [f"line {i}" for i in range(6)] and s.caret == (1, 2)
    s.cur = s.buf.index("line 5")
    assert s.input_rows(60)[1] == [f"line {i}" for i in range(4, 10)] and s.caret == (2, 2)


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

    def clearok(self, yes):
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
    assert flat(row).startswith(" " + tui.CELLS[0]) and row[1][1].split(".")[0] in tui.HUES  # the sprint leads it
    assert f"{tui.CELLS[1]} working (12s | ↑ {tui.kilo(s.agent.used())} tokens | medium effort)" in flat(row)
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
    assert rows[19].startswith("   " + tui.CELLS[0]) and "working (0s | ↑" in rows[19]  # where the blank row was
    assert rows[20].startswith("  ───") and "Qwen3.5-0.8B" in rows[20]
    assert rows[21].startswith("  " + tui.MARK + " Ask anything") and "░" not in rows[21] and c.at == (21, 4)
    assert set(rows[22].strip()) == {"─"}  # a plain rule; the mode sits on the row under it
    assert rows[23].strip().startswith(tui.MODE_ICON["auto"] + " auto (tab to cycle)") and rows[23].endswith("copied 12 characters")
    assert "thinking" not in rows[23] and "working" not in rows[23]
    s.toast = ("", 0.0)
    assert drawn(s, monkeypatch)[0][23].endswith("esc interrupt  ctrl+o tools  /help")
    chat.go.set()
    finish(s)
    rows, _ = drawn(s, monkeypatch)
    assert rows[19] == "" and rows[23].endswith("ctrl+o tools  /help") and "pg" not in rows[23]


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
    assert "always this" in rows[21] and set(rows[22].strip()) == {"─"} and " ask (tab to cycle)" in rows[23]
    s.key("n")
    finish(s)


def test_the_home_screen_has_the_same_bar_under_the_logo(monkeypatch):
    s = screen()
    s.motion = False
    rows, c = drawn(s, monkeypatch, h=30, w=100)
    bar = next(i for i, r in enumerate(rows) if tui.MARK + " Ask anything" in r)
    assert "Qwen3.5-0.8B" in rows[bar - 1] and set(rows[bar + 1].strip()) == {"─"} and c.at[0] == bar
    assert "░░░░░░░░" in rows[bar - 8] and "pgup" not in "".join(rows)
    assert rows[bar].index(tui.MARK) == c.at[1] - 2


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


# ---------- shell mode: a line that starts with ! ----------

def test_a_line_starting_with_a_bang_turns_the_bar_into_shell_mode():
    s = screen(mode="ask")
    s.buf, s.cur = "!git status", 11
    top, prompt, bottom = s.input_rows(60)[0]
    assert flat(prompt) == "! git status" and prompt[0] == ("!", "yellow")  # the ! is the mark; it is not shown twice
    assert top[0][1] == "yellow" and bottom[0][1] == "yellow"  # both rules change colour
    assert flat(s.mode_row()).startswith("! shell") and s.mode_row()[0] == ("!", "yellow") and "ask" not in flat(s.mode_row())
    assert len(flat(top)) == len(flat(bottom)) == 60 and s.caret == (1, 12)
    s.buf, s.cur = "!", 1
    assert s.input_rows(60)[0][1][-1] == ("a command to run yourself", "ghost")
    s.buf, s.cur = "", 0  # the ! deleted: an ordinary prompt again
    top, prompt, bottom = s.input_rows(60)[0]
    assert prompt[0] == (tui.MARK, "ask") and top[0][1] == "frame" and ("ask", "ask") in s.mode_row()


def test_a_command_you_ran_yourself_keeps_its_mark_in_the_conversation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen()
    s.submit("!echo hi")
    finish(s)
    first = next(spans for kind, spans, item in s.lines(60) if kind == "user")
    assert flat(first) == "▌ ! echo hi" and first[0] == ("▌", "yellow") and ("!", "yellow") in first


# ---------- answering an approval: a highlighted choice, arrows to move it, enter to take it ----------

def waiting(tmp_path, monkeypatch, *more):
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\necho 1\n```", *more, "ok", mode="ask")
    s.submit("go")
    for _ in range(300):
        if s.pending:
            return s
        s.worker.join(0.02)
    raise AssertionError("never asked")


def choices(s):
    return [(text.strip(), role) for text, role in s.input_rows(70)[0][2] if text.strip()]


def test_the_approval_shows_its_choices_with_once_highlighted(tmp_path, monkeypatch):
    s = waiting(tmp_path, monkeypatch)
    assert ("y once", "chosen") in choices(s) and [t for t, r in choices(s) if r == "chosen"] == ["y once"]
    assert "enter" in flat(s.input_rows(70)[0][2]) and "←→" in flat(s.input_rows(70)[0][2])
    s.key("n")
    finish(s)


def test_right_and_left_move_the_choice_and_enter_takes_it(tmp_path, monkeypatch):
    s = waiting(tmp_path, monkeypatch)
    s.key(curses.KEY_RIGHT)
    assert [t for t, r in choices(s) if r == "chosen"] == ["a always this"]
    s.key(curses.KEY_RIGHT)
    assert [t for t, r in choices(s) if r == "chosen"] == ["n no"]
    s.key(curses.KEY_RIGHT)  # round to the start
    s.key(curses.KEY_LEFT)   # and back to the end
    assert [t for t, r in choices(s) if r == "chosen"] == ["n no"]
    s.key(curses.KEY_LEFT)
    s.key("\n")  # always
    finish(s)
    assert s.agent.allow == ["shell echo 1"] and s.items[1]["state"] == "done"


def test_enter_on_no_refuses_and_the_next_approval_starts_on_once_again(tmp_path, monkeypatch):
    s = waiting(tmp_path, monkeypatch, "```shell\necho 2\n```")
    s.key(curses.KEY_LEFT)  # from once round to no
    s.key("\r")
    for _ in range(300):
        if s.pending and s.pending.body.strip() == "echo 2":
            break
        s.worker.join(0.02)
    assert s.items[1]["state"] == "denied" and [t for t, r in choices(s) if r == "chosen"] == ["y once"]
    s.key("y")  # the letters still answer directly
    finish(s)
    assert s.items[2]["state"] == "done"


# ---------- found on a real Mac terminal ----------

def test_a_marked_paste_whose_lines_end_in_carriage_returns_still_becomes_a_chip():
    """once Enter arrives as a return, so do the line breaks inside a paste"""
    s = screen()
    for ch in "\x1b[200~" + "\r".join(f"line {i}" for i in range(12)) + "\x1b[201~":
        s.take(ch)
    assert s.buf == "[Pasted #1 +12 lines] " and s.pastes["[Pasted #1 +12 lines]"] == "\n".join(f"line {i}" for i in range(12))
    s = screen()
    for ch in "\x1b[200~one\r\ntwo\x1b[201~":
        s.take(ch)
    assert s.buf == "one two" and "\r" not in s.buf


def test_enter_with_shift_held_is_a_new_line_where_the_terminal_cannot_say_so(monkeypatch):
    s = screen("ok")
    typed(s, "one")
    monkeypatch.setattr(tui, "shift_held", lambda: True)
    s.key("\r")
    finish(s)  # not a live screen (only tests call key directly): the keyboard is not asked, and Enter sends
    assert s.items[0]["text"] == "one"
    s = screen("ok")
    s.live = True
    typed(s, "one")
    s.key("\r")
    typed(s, "two")
    assert s.buf == "one\ntwo" and s.worker is None
    monkeypatch.setattr(tui, "shift_held", lambda: False)
    s.key("\r")
    finish(s)
    assert s.items[0]["text"] == "one\ntwo"


def test_shift_is_never_read_from_this_machine_over_ssh(monkeypatch):
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 5000 10.0.0.1 22")
    monkeypatch.setattr(tui, "shift_reader", lambda: pytest.fail("asked the keyboard of the wrong machine"))
    assert tui.shift_held() is False


def test_a_keyboard_that_cannot_be_asked_is_asked_once_and_then_left_alone(monkeypatch):
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)
    asked = []

    def broken():
        asked.append(1)
        raise OSError("no window server")
    monkeypatch.setattr(tui, "SHIFT", [])
    monkeypatch.setattr(tui, "shift_reader", broken)
    assert tui.shift_held() is False and tui.shift_held() is False and asked == [1]
    monkeypatch.setattr(tui, "SHIFT", [])
    monkeypatch.setattr(tui, "shift_reader", lambda: (lambda: True))
    assert tui.shift_held() is True


def test_the_prompt_mark_is_a_heavy_angle_where_the_font_has_one():
    assert tui.MARK == ("❯" if tui.VT else ">")
    s = screen(mode="ask")
    assert s.input_rows(60)[0][1][0] == (tui.MARK, "ask")


# ---------- the mode: under the bar, with an icon and how to change it ----------

def test_each_mode_has_an_icon_more_filled_the_more_it_may_do_by_itself():
    full, empty = tui.CELLS
    assert full != empty and tui.MODE_ICON == {"ask": full + empty * 2, "edits": full * 2 + empty, "auto": full * 3}


def test_the_mode_row_is_icon_name_and_tab_to_cycle():
    for mode in ("ask", "edits", "auto"):
        row = screen(mode=mode).mode_row()
        assert flat(row) == f"{tui.MODE_ICON[mode]} {mode} (tab to cycle)"
        assert row[0] == (tui.MODE_ICON[mode], mode) and (mode, mode) in row and row[-1] == (" (tab to cycle)", "dim")


def test_the_mode_is_drawn_directly_under_the_bar_and_a_note_sits_to_its_right(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("ok", mode="edits")
    s.submit("hi")
    finish(s)
    rows, c = drawn(s, monkeypatch)
    under = rows[-1]
    assert set(rows[-2].strip()) == {"─"} and under.strip().startswith(f"{tui.MODE_ICON['edits']} edits (tab to cycle)")
    assert "ctrl+o" in under  # the keys keep the right-hand side
    s.tell("copied 12 characters")
    under = drawn(s, monkeypatch)[0][-1]
    assert "(tab to cycle)" in under and under.rstrip().endswith("copied 12 characters")  # the note takes the keys' place


def test_on_the_opening_screen_the_mode_is_under_the_bar_too(monkeypatch):
    s = screen(mode="ask")
    s.motion = False
    rows, c = drawn(s, monkeypatch, h=30, w=100)
    bar = next(i for i, r in enumerate(rows) if tui.MARK + " Ask anything" in r)
    assert rows[bar + 2].strip().startswith(f"{tui.MODE_ICON['ask']} ask (tab to cycle)") and "ctrl+c" in rows[bar + 2]
    assert rows[bar + 2].index(tui.MODE_ICON["ask"]) == rows[bar].index(tui.MARK)  # in line with the prompt
