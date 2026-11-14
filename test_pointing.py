"""things you can point at: the list of commands over the prompt, the switches at the top of the right-hand panel,
and the effort bar. Hovering lights what is under the mouse, a click takes it"""
import curses
import time

import pytest

import sprint
import sprint_tui as tui
from test_popups import MOVE, PRESS, RELEASE, Scr, flat, screen
from test_tui import typed


@pytest.fixture
def scr(monkeypatch):
    monkeypatch.setattr(tui.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(tui.curses, "color_pair", lambda n: n << 8)
    return Scr(h=44, w=140)  # tall enough for the session rows, wide enough for both panels


def click(s, x, y):
    s.pointer(x, y, PRESS, exact=True)
    s.pointer(x, y, RELEASE, exact=True)


def lit(scr, y):
    """is this row of the screen in reverse video anywhere?"""
    return any(row == y and attr & curses.A_REVERSE for row, _, _, attr in scr.marks)


# ---------- /new is gone ----------

def test_clear_is_the_one_command_that_starts_afresh():
    assert "/new" not in [name for name, _ in tui.COMMANDS] and "/clear" in [name for name, _ in tui.COMMANDS]
    s = screen()
    typed(s, "/")
    assert "/new" not in [o for o, _ in s.options()]


# ---------- /model with a space after it ----------

def models(s):
    s.models = ["magi-1.2-2B:latest", "magi-1.2-4B:latest", "qwen3.5:0.8b"]
    s.installed = lambda: s.models


def test_slash_model_and_a_space_opens_the_model_window_like_slash_model_alone():
    """picked from the list, /model lands in the prompt with a space after it, ready for a name. Enter then took the
    first model of a bare list of names instead of opening the window with its sizes and its ways to pull one"""
    for line in ("/model", "/model "):
        s = screen()
        models(s)
        typed(s, line)
        assert s.options() == [], line
        s.key("\r")
        assert s.popup and s.popup["title"] == "Model", line
        assert s.agent.model == sprint.DEFAULT_MODEL  # nothing was switched behind your back


def test_picked_from_the_list_and_entered_it_opens_the_window_too():
    s = screen()
    models(s)
    typed(s, "/mod")
    s.key("\r")   # takes /model from the list; there is nothing more to choose over the prompt, so it runs
    assert s.popup and s.popup["title"] == "Model"


def test_a_name_begun_after_it_still_completes_in_place():
    s = screen()
    models(s)
    typed(s, "/model q")
    assert [o for o, _ in s.options()] == ["qwen3.5:0.8b"]
    s.key("\r")
    assert s.agent.model == "qwen3.5:0.8b" and s.popup is None


# ---------- the list over the prompt takes the mouse ----------

def rows_of(s, scr):
    """the screen rows the list over the prompt stands on, in order, and a column inside it"""
    s.draw(scr)
    ys = sorted(y for (kind, y) in s.hits if kind == "opt")
    left = s.areas["prompt"][0]
    return ys, left + 6


def test_a_click_on_a_command_in_the_list_takes_it(scr):
    s = screen()
    typed(s, "/th")
    ys, x = rows_of(s, scr)
    names = [o for o, _ in s.options()]
    assert names[:2] == ["/think", "/theme"] and len(ys) == len(names)
    assert s.agent.think is False
    click(s, x, ys[0])
    assert s.agent.think is True and s.buf == ""  # /think was run, as Enter on it would


def test_a_click_on_a_command_that_has_a_window_opens_it(scr):
    s = screen()
    typed(s, "/th")
    ys, x = rows_of(s, scr)
    click(s, x, ys[1])
    assert s.popup and s.popup["title"] == "Theme"


def test_a_click_on_a_choice_of_a_command_takes_that_choice(scr):
    s = screen(mode="ask")
    typed(s, "/mode ")
    ys, x = rows_of(s, scr)
    assert [o for o, _ in s.options()] == ["ask", "edits", "auto"]
    click(s, x, ys[2])
    assert s.agent.mode == "auto" and s.buf == ""


def test_the_row_under_the_mouse_becomes_the_chosen_one(scr):
    s = screen()
    typed(s, "/")
    ys, x = rows_of(s, scr)
    assert s.pick == 0
    s.pointer(x, ys[3], MOVE, exact=True)
    assert s.pick == 3
    s.draw(scr)
    assert lit(scr, ys[3]) and not lit(scr, ys[0])
    s.pointer(x, ys[0] - 4, MOVE, exact=True)  # off the list: the choice stays where it was
    assert s.pick == 3


def test_a_click_beside_the_list_is_an_ordinary_click(scr):
    s = screen()
    typed(s, "/th")
    ys, _ = rows_of(s, scr)
    click(s, 1, ys[0])  # in the message list on the left, on the same row
    assert s.agent.think is False and s.buf == "/th"


def test_every_move_of_the_mouse_is_asked_for_once_the_terminal_reports_in_full(monkeypatch):
    """hovering needs every move reported. A terminal that does not report in full has its wheel guessed from bare
    moves, which that would break; so it is asked for only once a full report has been seen (and under a popup, as ever)"""
    wrote = []
    monkeypatch.setattr(tui, "VT", True)
    monkeypatch.setattr(tui.sys.stdout, "write", lambda text: wrote.append(text))
    s = screen()
    s.track_hover()
    assert s.hover is False and not any("1003h" in t for t in wrote)
    s.sgr_mouse(35, 10, 10, True)  # a bare move, reported in full
    s.track_hover()
    assert s.hover is True and any("1003h" in t for t in wrote)
    s.submit("/mouse")  # mouse off: nothing is reported
    s.track_hover()
    assert s.hover is False


# ---------- the switches at the top of the right-hand panel ----------

def switches(s, scr):
    """the panel's switch rows -> {command: screen row}, and a column inside the panel"""
    s.sidebar = True
    if not s.items:
        s.items.append({"t": "user", "text": "hello"})  # (the opening screen has no panels: a conversation does)
    s.draw(scr)
    w, _, side = s.geo
    return {cmd: y for (kind, y), cmd in s.hits.items() if kind == "switch"}, w - side + 4


def test_thinking_showthink_and_plan_each_have_a_checkbox(scr):
    s = screen()
    at, x = switches(s, scr)
    assert set(at) == {"/think", "/showthink", "/plan"}
    w, _, side = s.geo
    text = {cmd: scr.text(y)[w - side + 1:].rstrip() for cmd, y in at.items()}
    assert text["/think"].startswith("thinking") and "[ ] off" in text["/think"]
    assert "[ ] off" in text["/showthink"] and "[✓] on" in text["/plan"]  # the to-do list is on unless switched off


def test_a_click_on_a_switch_flips_it(scr):
    s = screen()
    at, x = switches(s, scr)
    click(s, x, at["/think"])
    assert s.agent.think is True
    click(s, x, at["/showthink"])
    assert s.showthink is True
    click(s, x, at["/plan"])
    assert s.agent.planning is False and sprint.load_config()["plan"] is False
    at, _ = switches(s, scr)
    w, _, side = s.geo
    assert "[✓] on" in scr.text(at["/think"]) and "[ ] off" in scr.text(at["/plan"])
    click(s, x, at["/think"])
    assert s.agent.think is False


def test_the_switch_under_the_mouse_is_lit_and_no_other_row(scr):
    s = screen()
    at, x = switches(s, scr)
    assert not any(lit(scr, y) for y in at.values())
    s.pointer(x, at["/showthink"], MOVE, exact=True)
    s.draw(scr)
    assert lit(scr, at["/showthink"]) and not lit(scr, at["/think"]) and not lit(scr, at["/plan"])
    s.pointer(x, at["/showthink"] + 6, MOVE, exact=True)  # moved off, onto a row that is no switch
    s.draw(scr)
    assert not any(lit(scr, y) for y in at.values())


def test_a_click_on_another_row_of_the_panel_flips_nothing(scr):
    s = screen()
    at, x = switches(s, scr)
    click(s, x, min(at.values()) - 1)  # the model's row, just above
    assert (s.agent.think, s.showthink, s.agent.planning) == (False, False, True)


# ---------- the effort bar ----------

def bar(s, w=60):
    """the effort window's lines as text, laid out as draw would at a known place"""
    lines = s.popup_lines(w, 20)[0]
    s.popup["box"] = (5, 10, w, len(lines))
    return [flat(line) for line in lines]


def test_effort_is_a_window_with_one_bar_of_three_choices():
    s = screen()
    s.submit("/effort")
    assert s.popup["title"] == "Effort" and s.popup["bar"]["at"] == 1  # medium, as it stands
    text = bar(s)
    label = next(t for t in text if "low" in t)
    assert "medium" in label and "high" in label  # the three stand side by side on one line
    assert all(len(t) == 60 for t in text)
    assert any("steps" in t for t in text)  # and what the chosen one means is said under them


def test_left_and_right_move_along_the_bar_and_enter_sets_it():
    s = screen()
    s.submit("/effort")
    s.key(curses.KEY_RIGHT)
    assert s.popup["bar"]["at"] == 2 and "32 steps" in " ".join(bar(s))
    s.key(curses.KEY_RIGHT)
    assert s.popup["bar"]["at"] == 2  # the bar has ends
    s.key(curses.KEY_LEFT)
    s.key(curses.KEY_LEFT)
    assert s.popup["bar"]["at"] == 0
    s.key("\r")
    assert s.agent.effort == "low" and s.popup is None and "effort" in s.note_text()


def test_escape_leaves_the_effort_as_it_was():
    s = screen()
    s.submit("/effort")
    s.key(curses.KEY_RIGHT)
    s.key("\x1b")
    assert s.agent.effort == "medium" and s.popup is None


def test_the_mouse_lights_the_part_of_the_bar_it_is_over_and_a_click_sets_it():
    s = screen()
    s.submit("/effort")
    bar(s)
    py, px, _, _ = s.popup["box"]
    (a0, b0, y0), (a1, b1, _), (a2, b2, _) = s.popup["bar"]["cells"]  # where each choice stands in the window
    s.pointer(px + a2 + 2, py + y0, MOVE, exact=True)
    assert s.popup["bar"]["at"] == 2
    s.pointer(px + a0 + 2, py + y0, MOVE, exact=True)
    assert s.popup["bar"]["at"] == 0
    s.pointer(px + a2 + 2, py + y0, PRESS, exact=True)
    s.pointer(px + a2 + 2, py + y0, RELEASE, exact=True)
    assert s.agent.effort == "high" and s.popup is None


def test_each_effort_has_glints_that_cross_its_part_faster_than_the_one_before():
    def laps(n, secs=12.0, step=0.02):
        """how many times a glint sets off from the left of the nth track in this many seconds"""
        count, was = 0, None
        for k in range(int(secs / step)):
            lit_cells = [i for i, on in enumerate(tui.effort_tracks(k * step, 16)[n]) if on]
            head = max(lit_cells) if lit_cells else None
            if head is not None and was is not None and head < was:
                count += 1
            was = head if head is not None else was
        return count

    slow, mid, fast = laps(0), laps(1), laps(2)
    assert 0 < slow < mid < fast and fast >= 3 * slow
    assert all(sum(tui.effort_tracks(0.3, 16)[n]) <= 3 for n in range(3))  # small glints: a cell or two of each track


def test_the_bar_moves_only_while_motion_is_on(monkeypatch):
    s = screen()
    s.submit("/effort")
    assert s.active()  # the screen keeps drawing while the bar is open, for the glints
    now = time.time()
    monkeypatch.setattr(tui.time, "time", lambda: now)
    one = bar(s)
    monkeypatch.setattr(tui.time, "time", lambda: now + 0.33)
    assert bar(s) != one  # a third of a second on, a glint has moved
    s.motion = False
    still = bar(s)
    monkeypatch.setattr(tui.time, "time", lambda: now + 0.9)
    assert bar(s) == still and not s.active()


def test_the_effort_given_by_name_needs_no_window():
    s = screen()
    s.submit("/effort high")
    assert s.agent.effort == "high" and s.popup is None
