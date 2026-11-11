"""popups you can point at, themes with their own background, a sturdier wheel, and the flash on a jump"""
import curses
import io

import pytest

import sprint
import sprint_tui as tui
from sprint import Agent
from sprint_tui import Screen, row
from test_sprint import scripted

PRESS, RELEASE, CLICK = curses.BUTTON1_PRESSED, curses.BUTTON1_RELEASED, curses.BUTTON1_CLICKED
MOVE, WHEEL_UP, FIFTH = curses.REPORT_MOUSE_POSITION, curses.BUTTON4_PRESSED, 0x200000


def flat(spans):
    return "".join(t for t, _ in spans)


def screen(*replies, **kw):
    kw.setdefault("mode", "auto")
    return Screen(Agent(chat=scripted(*replies), out=io.StringIO(), **kw))


def finish(s):
    s.worker.join(10)
    assert not s.worker.is_alive()


def opened(s, command, box=(3, 10, 60)):
    """open a popup and lay it out as draw would, at a known place -> the screen row of its first choice"""
    s.submit(command)
    lines = s.popup_lines(box[2], 20)[0]
    s.popup["box"] = box + (len(lines),)
    return box[0] + min(s.popup["at"])


class Scr:
    """a stand-in for the curses window: remembers what was written where, and every restyle"""

    def __init__(self, h=30, w=120):
        self.h, self.w, self.cells, self.marks, self.bkgd = h, w, {}, [], None

    def getmaxyx(self):
        return self.h, self.w

    def erase(self):
        self.cells, self.marks = {}, []

    def bkgdset(self, ch, attr=0):
        self.bkgd = attr

    def addstr(self, y, x, text, attr=0):
        for i, ch in enumerate(text):
            self.cells[(y, x + i)] = (ch, attr)

    def chgat(self, y, x, n, attr):
        self.marks.append((y, x, n, attr))

    def text(self, y):
        return "".join(self.cells.get((y, x), (" ", 0))[0] for x in range(self.w))

    def find(self, word):
        return next(y for y in range(self.h) if word in self.text(y))

    def __getattr__(self, name):  # move, refresh and whatever else: nothing to remember
        return lambda *a, **k: None


@pytest.fixture
def scr(monkeypatch):
    monkeypatch.setattr(tui.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(tui.curses, "color_pair", lambda n: n << 8)
    return Scr()


def pair(attr):
    return (attr & curses.A_COLOR) >> 8


def talked(tmp_path, monkeypatch):
    """a screen with one message, one tool call and an answer on it"""
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\necho a\n```", "all done")
    s.submit("go")
    finish(s)
    return s


def painted(monkeypatch, theme, colours=256):
    """a screen painted in a theme on a terminal with this many colours -> (screen, {pair: (text, background)})"""
    pairs = {}
    monkeypatch.setattr(tui.curses, "has_colors", lambda: True)
    monkeypatch.setattr(tui.curses, "use_default_colors", lambda: None)
    monkeypatch.setattr(tui.curses, "COLORS", colours, raising=False)
    monkeypatch.setattr(tui.curses, "init_pair", lambda n, fg, bg: pairs.__setitem__(n, (fg, bg)))
    s = screen()
    s.theme = theme
    pairs.clear()
    s.paint()
    return s, pairs


# ---------- the mouse in a popup ----------

def test_popup_lines_record_which_line_shows_which_choice():
    s = screen()
    s.submit("/mode")
    lines = s.popup_lines(60, 20)[0]
    assert s.popup["at"] == {1: 0, 2: 1, 3: 2}  # the three modes; the notes under them cannot be chosen
    assert "auto" in flat(lines[3]) and len(lines) > 6


def test_pointing_at_a_row_highlights_it_and_the_theme_preview_follows():
    s = screen()
    top = opened(s, "/theme")
    s.pointer(20, top + 3, MOVE)
    assert s.popup["sel"] == 3 and s.theme == list(tui.THEMES)[3]
    s.pointer(20, top + 1, MOVE)
    assert s.popup["sel"] == 1 and s.theme == list(tui.THEMES)[1]
    s.pointer(20, top - 1, MOVE)  # the border: not a row, so nothing moves
    assert s.popup["sel"] == 1 and s.popup is not None


def test_a_press_and_release_on_one_row_chooses_it():
    s = screen()
    top = opened(s, "/theme")
    s.pointer(20, top + 2, PRESS)
    assert s.popup["sel"] == 2 and "theme" not in sprint.load_config()
    s.pointer(24, top + 2, MOVE)  # the hand may wander along the row before it lets go
    s.pointer(24, top + 2, RELEASE)
    name = list(tui.THEMES)[2]
    assert s.popup is None and s.theme == name and sprint.load_config()["theme"] == name


def test_a_release_with_no_press_before_it_only_highlights():
    s = screen()
    top = opened(s, "/theme")
    s.pointer(20, top + 2, RELEASE)  # some terminals report a hover this way
    assert s.popup is not None and s.popup["sel"] == 2 and "theme" not in sprint.load_config()


def test_a_press_on_one_row_and_a_release_on_another_chooses_nothing():
    s = screen()
    top = opened(s, "/theme")
    s.pointer(20, top + 2, PRESS)
    s.pointer(20, top + 4, MOVE)
    s.pointer(20, top + 4, RELEASE)
    assert s.popup is not None and s.popup["sel"] == 4
    s.pointer(20, top + 2, RELEASE)  # nor does going back: the press was spent
    assert s.popup is not None


def test_a_click_event_chooses_the_row():
    s = screen()
    top = opened(s, "/mode")
    s.pointer(20, top, CLICK)
    assert s.popup is None and s.agent.mode == "ask"


def test_the_wheel_moves_the_choice_one_row_and_stops_at_the_ends(monkeypatch):
    monkeypatch.setattr(tui, "WHEEL_DOWN", FIFTH)
    s = screen()
    top = opened(s, "/mode")
    s.popup["sel"] = 0
    s.pointer(20, top, WHEEL_UP)
    assert s.popup["sel"] == 0
    for want in (1, 2, 2):
        s.pointer(20, top, FIFTH)
        assert s.popup["sel"] == want
    s.pointer(20, top, WHEEL_UP)
    assert s.popup["sel"] == 1 and s.popup is not None


def test_without_a_fifth_button_a_report_from_the_same_place_is_the_wheel_going_down(monkeypatch):
    monkeypatch.setattr(tui, "WHEEL_DOWN", 0)
    s = screen()
    top = opened(s, "/theme")
    s.pointer(20, top + 1, MOVE)  # the pointer arrives: a hover
    assert s.popup["sel"] == 1
    s.pointer(20, top + 1, MOVE)  # it has not moved, so this was the wheel
    s.pointer(20, top + 1, MOVE)
    assert s.popup["sel"] == 3 and s.theme == list(tui.THEMES)[3]
    s.pointer(21, top + 1, MOVE)  # it moved: a hover again
    assert s.popup["sel"] == 1


def test_a_press_outside_the_popup_closes_it_like_escape():
    s = screen()
    top = opened(s, "/theme")
    s.pointer(20, top + 3, MOVE)
    s.pointer(2, 1, PRESS)
    assert s.popup is None and s.theme == "default" and "theme" not in sprint.load_config()
    s.pointer(2, 1, RELEASE)  # the release that follows starts nothing
    assert s.select is None and s.drag is None


def test_the_text_editor_popup_ignores_the_mouse():
    s = screen()
    s.submit("/systemprompt")
    s.popup_lines(60, 20)
    s.popup["box"] = (3, 10, 60, 20)
    before = (s.popup["edit"]["row"], s.popup["edit"]["col"], list(s.popup["edit"]["lines"]))
    for state in (MOVE, PRESS, RELEASE, CLICK, WHEEL_UP):
        s.pointer(1, 1, state)
        s.pointer(20, 6, state)
    assert s.popup and (s.popup["edit"]["row"], s.popup["edit"]["col"], s.popup["edit"]["lines"]) == before


def test_a_popup_that_was_never_drawn_ignores_the_mouse():
    s = screen()
    s.submit("/mode")
    s.pointer(1, 1, PRESS)
    assert s.popup is not None


def test_mouse_keys_reach_the_popup(monkeypatch):
    s = screen()
    top = opened(s, "/mode")
    monkeypatch.setattr(tui.curses, "getmouse", lambda: (0, 20, top + 1, 0, MOVE))
    s.key(curses.KEY_MOUSE)
    assert s.popup["sel"] == 1


def test_a_long_list_holds_still_under_the_pointer_and_follows_the_keys():
    s = screen()
    s.pop("Many", [row(f"choice {n}", n) for n in range(40)], pick=lambda v: None)
    lines = s.popup_lines(60, 12)[0]
    s.popup["box"] = (3, 10, 60, len(lines))
    assert flat(lines[1]).split()[1:3] == ["choice", "0"] and len(s.popup["at"]) == 10
    s.pointer(20, 3 + 10, MOVE)  # the last row in view
    assert s.popup["sel"] == 9
    lines, sel_at, _ = s.popup_lines(60, 12)
    assert "choice 0 " in flat(lines[1]) and sel_at == 10  # pointing never scrolls the list away
    for _ in range(5):
        s.key(curses.KEY_DOWN)
    lines, sel_at, _ = s.popup_lines(60, 12)
    assert s.popup["sel"] == 14 and sel_at == 10 and "choice 14 " in flat(lines[10])  # the keys scroll it one row at a time
    s.key(curses.KEY_UP)
    assert s.popup_lines(60, 12)[1] == 9  # and not at all while the choice is in view
    s.key(curses.KEY_DOWN)
    s.key(curses.KEY_NPAGE)
    assert s.popup["sel"] == 22 and "choice 22 " in flat(s.popup_lines(60, 12)[0][10])
    s.popup["sel"] = 0
    assert "choice 0 " in flat(s.popup_lines(60, 12)[0][1])


def test_draw_says_where_the_popup_is(scr):
    s = screen()
    s.submit("/mode")
    s.draw(scr)
    py, px, pw, ph = s.popup["box"]
    assert scr.text(py)[px:px + 8] == "╭─ Mode " and scr.text(py + ph - 1)[px] == "╰" and scr.text(py)[px + pw - 1] == "╮"
    s.pointer(px + 6, py + 1, PRESS)
    s.pointer(px + 6, py + 1, RELEASE)
    assert s.popup is None and s.agent.mode == "ask"


def test_hover_reports_are_switched_on_with_a_popup_and_off_after(scr, capsys, monkeypatch):
    monkeypatch.setattr(tui, "VT", True)
    s = screen()
    s.draw(scr)
    assert "1003" not in capsys.readouterr().out
    s.submit("/mode")
    s.draw(scr)
    s.draw(scr)
    assert capsys.readouterr().out.count("\033[?1003h") == 1  # once, not on every frame
    s.key("\x1b")
    s.draw(scr)
    s.draw(scr)
    assert capsys.readouterr().out == "\033[?1003l\033[?1002h"  # and drag reports come back, as the two cannot be on together
    s.mouse_on = False
    s.submit("/mode")
    s.draw(scr)
    assert capsys.readouterr().out == ""


# ---------- the wheel outside popups ----------

def test_the_wheel_scrolls_even_when_a_selection_was_never_released(monkeypatch):
    monkeypatch.setattr(tui, "WHEEL_DOWN", FIFTH)
    s = screen()
    s.geo = (120, 26, 32)
    s.pointer(50, 8, PRESS)  # a press whose release never comes
    assert s.select is not None
    s.pointer(50, 8, WHEEL_UP)
    assert s.scroll == 3 and s.select is None
    s.pointer(50, 8, WHEEL_UP)
    s.pointer(50, 8, PRESS)
    s.pointer(50, 8, FIFTH)
    assert s.scroll == 3 and s.select is None
    s.pointer(50, 8, FIFTH)
    s.pointer(50, 8, FIFTH)
    assert s.scroll == 0


def test_a_press_after_a_lost_release_starts_afresh(tmp_path, monkeypatch):
    copied = []
    monkeypatch.setattr(sprint, "clipboard", lambda text: copied.append(text) or True)
    s = talked(tmp_path, monkeypatch)
    s.geo, s.hits, s.screen = (120, 26, 32), {("main", 8): s.items[1]}, {y: (28, "some text here") for y in range(3, 9)}
    s.pointer(30, 3, PRESS)  # its release never comes
    s.pointer(50, 8, PRESS)
    s.pointer(50, 8, RELEASE)
    assert s.items[1]["open"] is True and copied == [] and s.select is None  # a click, not a copy from the old press


def test_with_a_fifth_button_a_bare_report_is_not_the_wheel(monkeypatch):
    monkeypatch.setattr(tui, "WHEEL_DOWN", FIFTH)
    s = screen()
    s.geo, s.scroll = (120, 26, 32), 9
    s.pointer(50, 8, MOVE)
    assert s.scroll == 9


def test_without_a_fifth_button_a_bare_report_is_the_wheel_going_down(monkeypatch):
    monkeypatch.setattr(tui, "WHEEL_DOWN", 0)
    monkeypatch.setattr(sprint, "clipboard", lambda text: True)
    s = screen()
    s.geo, s.scroll = (120, 26, 32), 9
    monkeypatch.setattr(tui, "VT", False)  # only where sprint itself switches the mouse reports on and off
    s.pointer(50, 8, MOVE)
    assert s.scroll == 9
    monkeypatch.setattr(tui, "VT", True)
    s.pointer(50, 8, MOVE)
    assert s.scroll == 6
    s.pointer(50, 8, PRESS)  # while text is dragged over, the same report is the drag
    s.pointer(60, 9, MOVE)
    assert s.scroll == 6 and s.select["b"] == (9, 60)
    s.pointer(60, 9, RELEASE)
    s.pointer(87, 5, PRESS)  # and so it is while a divider is dragged
    s.pointer(69, 9, MOVE)
    assert s.scroll == 6 and s.drag == "right" and s.side_w == 50
    s.pointer(69, 9, RELEASE)
    s.hover = True  # hover reports still on from a popup that just closed are not the wheel either
    s.pointer(50, 8, MOVE)
    assert s.scroll == 6
    s.hover = False
    s.pointer(50, 8, MOVE)
    s.pointer(50, 8, MOVE)
    s.pointer(50, 8, MOVE)
    assert s.scroll == 0
