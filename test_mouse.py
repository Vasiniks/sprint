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
    assert s.select == {"a": (y, x), "b": (y, x)} and s.drag is None
    send(s, sgr(0, x, y, down=False))
    assert s.jump is item and s.select is None and s.drag is None and s.esc is None


def test_the_wheel_is_told_apart_from_a_move(wide):
    s, scr = wide
    s.scroll = 6
    send(s, sgr(65, 20, 5))
    assert s.scroll == 3
    send(s, sgr(64, 20, 5))
    assert s.scroll == 6
    send(s, sgr(35, 21, 5))  # the mouse moving with no button down: not the wheel, whatever the older form made of it
    send(s, sgr(35, 22, 5))
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
