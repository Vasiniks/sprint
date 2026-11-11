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
