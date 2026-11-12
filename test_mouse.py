"""the mouse, reported by the terminal in full (the "SGR" form): exact columns however far right, a press and a release
that cannot be mistaken, and a wheel that is not a guess. The older form cannot name a column past 223, and some
terminals garble it from column 95 on: there a click in the right-hand panel landed on the divider and dragged it"""
import curses

import pytest

import sprint_tui as tui
from test_popups import Scr, scr, talked  # noqa: F401  (scr is a fixture)


def send(s, text):
    for ch in text:
        s.take(ch)


def sgr(button, x, y, down=True):
    """what a terminal sends: the button, then column and row counted from 1, M for a press or a move and m for a release"""
    return f"\x1b[<{button};{x + 1};{y + 1}{'M' if down else 'm'}"


@pytest.fixture
def wide(scr, tmp_path, monkeypatch):
    """a conversation on a screen 200 columns wide, drawn once so the panels know where they are"""
    s = talked(tmp_path, monkeypatch)
    scr.w = 200
    s.sidebar = True
    s.draw(scr)
    return s, scr


def test_a_click_far_to_the_right_lands_where_it_was_made(wide):
    s, scr = wide
    w, lw, side = s.geo
    y, item = next((y, it) for (where, y), it in s.hits.items() if where == "side")
    x = w - side + 6
    assert x > 150  # past anything the older report can be trusted with
    send(s, sgr(0, x, y))
    assert (s.select["a"], s.select["b"]) == ((y, x), (y, x)) and s.drag is None
    send(s, sgr(0, x, y, down=False))
    assert s.jump is item and s.select is None and s.drag is None and s.esc is None


def test_the_wheel_is_told_apart_from_a_move(wide):
    s, scr = wide
    s.scroll = 6
    send(s, sgr(65, 60, 5))  # (over the conversation: over a side panel the wheel scrolls that panel's list)
    assert s.scroll == 3
    send(s, sgr(64, 60, 5))
    assert s.scroll == 6
    send(s, sgr(35, 61, 5))  # the mouse moving with no button down: not the wheel, whatever the older form made of it
    send(s, sgr(35, 62, 5))
    assert s.scroll == 6 and s.select is None


def test_dragging_the_divider_resizes_and_letting_go_ends_it(wide, monkeypatch):
    s, scr = wide
    monkeypatch.setattr(tui.sprint, "save_config", lambda **kw: None)
    w, lw, side = s.geo
    send(s, sgr(0, w - side - 1, 4))
    assert s.drag == "right"
    send(s, sgr(32, w - side - 11, 4))
    assert s.side_w == side + 10
    send(s, sgr(0, w - side - 11, 4, down=False))
    assert s.drag is None and s.side_w == side + 10


def test_a_new_press_lets_go_of_a_divider_whose_release_never_came(wide):
    s, scr = wide
    w, lw, side = s.geo
    s.drag = "right"  # stuck: the terminal never said the button came up
    y, item = next((y, it) for (where, y), it in s.hits.items() if where == "side")
    s.pointer(w - side + 6, y, curses.BUTTON1_PRESSED)
    assert s.drag is None and s.side_w == side
    s.pointer(w - side + 6, y, curses.BUTTON1_RELEASED)
    assert s.jump is item


def test_other_buttons_and_a_switched_off_mouse_do_nothing(wide):
    s, scr = wide
    send(s, sgr(2, 30, 5) + sgr(2, 30, 5, down=False) + sgr(1, 30, 5))
    assert s.select is None and s.drag is None
    s.mouse_on = False
    send(s, sgr(0, 30, 5))
    assert s.select is None


def test_the_full_report_is_asked_for_with_the_mouse_and_given_back_with_it(capsys, monkeypatch):
    monkeypatch.setattr(tui, "VT", True)
    monkeypatch.setattr(tui.curses, "mousemask", lambda m: None)
    monkeypatch.setattr(tui.curses, "mouseinterval", lambda n: None)
    s = tui.Screen(tui.sprint.Agent(chat=lambda *a: iter(()), out=__import__("io").StringIO()))
    capsys.readouterr()
    s.set_mouse()
    assert capsys.readouterr().out == "\033[?1002h\033[?1006h"
    s.mouse_on = False
    s.set_mouse()
    assert capsys.readouterr().out == "\033[?1006l\033[?1002l"


def test_in_a_popup_a_full_report_from_the_same_place_is_not_taken_for_the_wheel(scr):
    from test_popups import opened, screen
    s = screen()
    first = opened(s, "/mode")
    py, px, pw, ph = s.popup["box"]
    send(s, sgr(35, px + 4, first + 1))  # hover a row
    was = s.popup["sel"]
    send(s, sgr(35, px + 4, first + 1))  # the same place again: the older form's sign of a wheel turn
    assert s.popup["sel"] == was
    send(s, sgr(65, px + 4, first + 1))  # a real turn of the wheel moves the choice
    assert s.popup["sel"] == was + 1


# ---------- dragging to copy works wherever there is text, not only in the conversation ----------

PRESS, MOVE, RELEASE = curses.BUTTON1_PRESSED, tui.MOVED, curses.BUTTON1_RELEASED


@pytest.fixture
def copied(monkeypatch):
    got = []
    monkeypatch.setattr(tui.sprint, "clipboard", lambda text: got.append(text) or True)
    return got


def drag(s, scr, a, b):
    """press at a, move to b, let go: positions are (row, column)"""
    s.pointer(a[1], a[0], PRESS)
    s.pointer(b[1], b[0], MOVE, exact=True)
    s.draw(scr)
    lit = [m for m in scr.marks if m[3] & curses.A_REVERSE]
    s.pointer(b[1], b[0], RELEASE)
    return lit


def test_dragging_over_the_right_hand_panel_copies_what_it_says(wide, copied):
    s, scr = wide
    w, lw, side = s.geo
    y = next(y for (where, y) in s.hits if where == "side")
    line = scr.text(y)[w - side + 1:].rstrip()
    lit = drag(s, scr, (y, w - side + 1), (y, w - 2))
    assert copied == [line] and "shell" in line
    lit = [m for m in lit if m[0] == y]
    assert lit and all(m[1] >= w - side + 1 for m in lit)  # and what is being copied is shown, in the panel
    copied.clear()
    drag(s, scr, (1, w - side + 1), (4, w - 2))  # several rows: the context meter and what is under it
    assert copied[0].startswith("Context") and "tokens" in copied[0] and copied[0].count("\n") == 3


def test_a_drag_that_starts_in_a_panel_stays_in_it(wide, copied):
    s, scr = wide
    w, lw, side = s.geo
    y = next(y for (where, y) in s.hits if where == "side")
    drag(s, scr, (y, w - 2), (y, 5))  # let go far out in the conversation
    assert copied == [scr.text(y)[w - side + 1:w - 1].rstrip()]


def test_dragging_over_the_left_hand_panel_copies_your_messages(wide, copied):
    s, scr = wide
    s.history = True
    s.draw(scr)
    w, lw, side = s.geo
    assert lw
    y = next(y for (where, y) in s.hits if where == "left")
    drag(s, scr, (y, 1), (y, lw - 2))
    assert copied and copied[0] == scr.text(y)[1:lw - 1].strip() and "go" in copied[0]


def test_dragging_over_the_prompt_copies_what_you_typed_without_its_mark(wide, copied):
    s, scr = wide
    s.buf, s.cur = "hello world\nsecond line", 23
    s.draw(scr)
    y = scr.find("hello world")
    x = scr.text(y).index("hello")
    drag(s, scr, (y, x), (y, x + 4))
    assert copied == ["hello"]
    drag(s, scr, (y, x - 2), (y + 1, x + 40))  # from the mark, across both lines, past their end
    assert copied[1] == "hello world\nsecond line"


def test_the_conversation_still_copies_as_it_did(wide, copied):
    s, scr = wide
    y = scr.find("all done")
    x = scr.text(y).index("all done")
    drag(s, scr, (y, x), (y, x + 7))
    assert copied == ["all done"]
