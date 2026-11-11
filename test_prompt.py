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
    assert "nonl" in called and s.nl is True
    assert ("\033[>4;1m" in out and out.endswith("\033[>4;0m")) if tui.VT else out == ""
