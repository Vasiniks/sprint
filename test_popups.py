"""popups you can point at, themes with their own background, a sturdier wheel, and the flash on a jump"""
import curses
import re
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
    """the pair number in an attribute built by the stand-in color_pair these tests install (n << 8); every real
    attribute bit sits higher than that on both the Unix and the Windows curses"""
    return (attr >> 8) & 0xff


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
    assert s.popup is None and s.theme == "sprint" and "theme" not in sprint.load_config()
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


# ---------- popups stand out ----------

def test_ghost_is_a_grey_pair_with_256_colours_and_plain_dim_without(monkeypatch):
    monkeypatch.setattr(tui.curses, "color_pair", lambda n: n << 8)
    s = screen()
    assert "ghost" in tui.ROLE
    assert s.attr("ghost") == curses.A_DIM and s.attr("ghost", panel=True) == curses.A_DIM
    s.colors = True  # eight colours: still only dim
    assert s.attr("ghost") == curses.A_DIM
    s.rich = True
    assert pair(s.attr("ghost")) == 9 and pair(s.attr("ghost", panel=True)) == 19


def test_an_open_popup_greys_out_the_screen_and_sits_on_the_panel_shade(scr, tmp_path, monkeypatch):
    s = talked(tmp_path, monkeypatch)
    s.colors = s.rich = True
    s.draw(scr)
    assert not any(pair(m[3]) == 9 for m in scr.marks)
    s.submit("/mode")
    s.draw(scr)
    ghost = [m for m in scr.marks if pair(m[3]) == 9]
    assert [m[:3] for m in ghost] == [(y, 0, scr.w) for y in range(scr.h)]  # every row, edge to edge
    py, px, pw, ph = s.popup["box"]
    inside = [scr.cells[(y, x)][1] for y in range(py, py + ph) for x in range(px, px + pw)]
    assert len(inside) == pw * ph and all(10 <= pair(a) <= 19 for a in inside)
    sel = [m for m in scr.marks if m[3] & curses.A_REVERSE]
    assert sel == [(py + 1 + s.popup["sel"], px + 2, pw - 4, sel[0][3])]
    assert scr.marks.index(sel[0]) > scr.marks.index(ghost[-1])  # the highlight is laid over the grey, not under it


def test_without_256_colours_the_backdrop_is_dimmed(scr, tmp_path, monkeypatch):
    s = talked(tmp_path, monkeypatch)
    s.submit("/help")
    s.draw(scr)
    assert [m for m in scr.marks if m[3] == curses.A_DIM] == [(y, 0, scr.w, curses.A_DIM) for y in range(scr.h)]


# ---------- themes ----------

def test_every_theme_has_a_line_about_it_and_ten_exact_colours():
    assert set(tui.THEME_ABOUT) == set(tui.THEMES) and len(tui.THEMES) >= 12
    assert list(tui.THEMES)[0] == "sprint" and {"sapphire", "sapphire-dim", "terminal", "mono", "paper"} <= set(tui.THEMES)
    for name, t in tui.THEMES.items():
        assert len(tui.THEME_ABOUT[name]) <= 36, name
        if name not in ("terminal", "mono"):
            assert all(re.fullmatch(r"#[0-9a-f]{6}", t[k]) for k in (1, 2, 3, 4, 5, 6, "bg", "fg", "panel", "ghost")), name
            assert len({t["bg"], t["fg"], t["panel"], t["ghost"]}) == 4, name
    assert "bg" not in tui.THEMES["terminal"] and "bg" not in tui.THEMES["mono"]
    assert 20 + 7 * len(tui.THEMES) <= 230  # older curses cannot name a pair past 255, and the top ones are spoken for


def test_the_sprint_theme_is_nearly_black_not_black_with_the_logos_colours():
    t = tui.THEMES["sprint"]
    r, g, b = (int(t["bg"][i:i + 2], 16) for i in (1, 3, 5))
    assert 4 <= min(r, g, b) and max(r, g, b) <= 24  # a very dark grey: the whole screen takes it
    assert t[3] == "#ffe100" and tui.near(t["bg"]) == 232 and t["fg"] != "#000000"


def test_sapphire_carries_the_colours_of_the_theme_it_is_named_after():
    for name, ground in (("sapphire", "#181e29"), ("sapphire-dim", "#080d14")):
        t = tui.THEMES[name]
        assert t["bg"] == ground and t["fg"] == "#efefef"
        assert [t[i] for i in range(1, 7)] == ["#da6771", "#4eb071", "#fff099", "#399ef4", "#b168df", "#21c5c7"]


def test_the_nearest_of_the_256_stands_in_for_an_exact_colour():
    assert tui.near("#000000") == 16 and tui.near("#ffffff") == 231 and tui.near("#ff0000") == 196
    assert tui.near("#808080") == 244 and tui.near("#0b0c0e") == 232 and tui.near("#181e29") in (234, 235)
    assert len(set(tui.SLOT.values())) == len(tui.SLOT) and min(tui.SLOT.values()) >= 88 and max(tui.SLOT.values()) < 232


def test_exact_colours_are_only_asked_of_a_terminal_that_shows_24_bit_colour(monkeypatch):
    monkeypatch.setattr(tui.curses, "can_change_color", lambda: True, raising=False)
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    assert tui.can_exact()
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")  # says it can change its palette, and then does not
    assert not tui.can_exact()
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.delenv("COLORTERM")
    assert not tui.can_exact()


def test_a_theme_paints_its_own_background_text_panel_and_ghost(monkeypatch):
    s, pairs = painted(monkeypatch, "paper")
    t = {k: tui.near(v) for k, v in tui.THEMES["paper"].items()}  # this terminal cannot take exact colours
    assert not s.exact and s.base == 8 and pairs[8] == (t["fg"], t["bg"])
    assert [pairs[i] for i in range(1, 7)] == [(t[i], t["bg"]) for i in range(1, 7)]
    assert pairs[10] == (t["fg"], t["panel"]) and pairs[13] == (t[3], t["panel"])
    assert pairs[9] == (t["ghost"], t["bg"]) and pairs[19] == (t["ghost"], t["panel"])


def test_where_the_terminal_can_take_them_the_colours_are_exact(monkeypatch):
    defined = {}
    monkeypatch.setattr(tui, "can_exact", lambda: True)
    monkeypatch.setattr(tui.curses, "init_color", lambda n, r, g, b: defined.__setitem__(n, (r, g, b)), raising=False)
    s, pairs = painted(monkeypatch, "sapphire")
    t = tui.THEMES["sapphire"]
    assert s.exact and pairs[8] == (tui.SLOT[t["fg"]], tui.SLOT[t["bg"]]) and pairs[3][0] == tui.SLOT["#fff099"]
    assert defined[tui.SLOT["#181e29"]] == (0x18 * 1000 // 255, 0x1e * 1000 // 255, 0x29 * 1000 // 255)
    assert set(defined) == set(tui.SLOT.values())  # every theme's colours, so the theme list can show them all

    def refuses(*a):
        raise tui.curses.error("init_color() returned ERR")
    monkeypatch.setattr(tui.curses, "init_color", refuses, raising=False)
    s, pairs = painted(monkeypatch, "sapphire")
    assert not s.exact and pairs[8] == (tui.near(t["fg"]), tui.near(t["bg"]))  # it said it could and cannot


def test_the_terminal_theme_keeps_the_terminals_own_colours(monkeypatch):
    s, pairs = painted(monkeypatch, "terminal")
    assert s.base == 0 and [pairs[i] for i in range(1, 7)] == [(i, -1) for i in range(1, 7)]
    assert pairs[10] == (-1, 236) and pairs[12] == (2, 236) and pairs[9][1] == -1 and pairs[19][1] == 236


def test_sprint_is_the_theme_you_get_and_default_now_means_terminal():
    assert screen().theme == "sprint"
    sprint.save_config(theme="default")  # what the terminal's own colours used to be called
    assert screen().theme == "terminal"
    sprint.save_config(theme="cmyk")  # a theme that no longer exists
    assert screen().theme == "sprint"


def test_with_fewer_than_256_colours_a_theme_changes_nothing(monkeypatch):
    s, pairs = painted(monkeypatch, "paper", colours=8)
    assert s.base == 0 and not s.rich
    assert pairs == {**{i: (i, -1) for i in range(1, 8)}, **{10 + i: (i or -1, -1) for i in range(8)}}
    s, pairs = painted(monkeypatch, "mono", colours=8)
    assert pairs[1] == (-1, -1) and pairs[16] == (-1, -1)


def test_every_theme_gets_sample_pairs_in_its_own_colours(monkeypatch):
    s, pairs = painted(monkeypatch, "terminal")
    for name, t in tui.THEMES.items():
        text = tui.SAMPLE[f"{name}.0"]
        assert text >= 20 and pairs[text] == (s.ink(t.get("fg", -1)), s.ink(t.get("bg", -1))), name
        assert [pairs[tui.SAMPLE[f"{name}.{i}"]] for i in range(1, 7)] == [(s.ink(t.get(i, i)), s.ink(t.get("bg", -1))) for i in range(1, 7)], name
    assert len(set(tui.SAMPLE.values())) == 7 * len(tui.THEMES)


def test_plain_text_takes_the_themes_base_pair_and_samples_keep_theirs(monkeypatch):
    monkeypatch.setattr(tui.curses, "color_pair", lambda n: n << 8)
    s = screen()
    s.colors = s.rich = True
    s.base = 8
    assert pair(s.attr("")) == 8 and s.attr("dim") == 8 << 8 | curses.A_DIM and pair(s.attr("bold", panel=True)) == 10
    assert pair(s.attr("red")) == 1 and pair(s.attr("red", panel=True)) == 11
    sample = tui.SAMPLE["ocean.4"]
    assert pair(s.attr("ocean.4")) == sample and pair(s.attr("ocean.4", panel=True)) == sample  # wherever it is drawn
    s.rich = False  # without the 256 a sample is just the plain hue
    assert pair(s.attr("ocean.4", panel=True)) == 4 and pair(s.attr("ocean.0")) == 0


def test_a_highlight_sits_on_the_theme_and_the_screen_is_cleared_to_it(scr, tmp_path, monkeypatch):
    s = talked(tmp_path, monkeypatch)
    s.colors = s.rich = True
    s.base = 8
    s.mark(scr, 2, 3, 4, curses.A_REVERSE)
    s.mark(scr, 2, 3, 4, 5 << 8)
    assert scr.marks == [(2, 3, 4, curses.A_REVERSE | 8 << 8), (2, 3, 4, 5 << 8)]
    s.draw(scr)
    assert scr.bkgd == 8 << 8


def test_the_theme_popup_shows_each_theme_in_its_own_colours(scr):
    s = screen()
    s.submit("/theme")
    for r in s.popup["rows"]:
        name, roles = r["v"], [role for _, role in r["s"]]
        assert name in flat(r["s"]) and tui.THEME_ABOUT[name] in flat(r["s"])
        assert f"{name}.0" in roles and [x for x in roles if x[-1] in "123456"] == [f"{name}.{i}" for i in range(1, 7)]
    assert [r["v"] for r in s.popup["rows"]] == list(tui.THEMES)
    s.colors = s.rich = True
    s.popup["sel"] = 4
    s.draw(scr)
    py, px, pw, ph = s.popup["box"]
    lit = [m for m in scr.marks if m[3] & curses.A_REVERSE]
    assert len(lit) == 1 and lit[0][0] == py + 5 and lit[0][2] < 20  # the highlight stops short of the swatches
    swatches = [x for x in range(px, px + pw) if scr.cells[(py + 5, x)][0] == "█"]
    assert len(swatches) == 12 and swatches[0] >= lit[0][1] + lit[0][2]
    name = list(tui.THEMES)[4]
    assert [pair(scr.cells[(py + 5, x)][1]) for x in swatches[::2]] == [tui.SAMPLE[f"{name}.{i}"] for i in range(1, 7)]


# ---------- the flash on a jump ----------

def test_clicking_an_activity_line_flashes_that_window_then_fades(scr, tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(tui.time, "time", lambda: now[0])
    s = talked(tmp_path, monkeypatch)
    s.colors = s.rich = True
    tool = s.items[1]
    s.draw(scr)
    w, lw, side = s.geo
    y = next(y for (where, y), item in s.hits.items() if where == "side" and item is tool)
    assert not s.active()
    s.pointer(w - 5, y, PRESS)
    s.pointer(w - 5, y, RELEASE)
    assert s.flash == (tool, 1000.0) and s.active()

    def look():
        s.draw(scr)
        rows = sorted(y for (where, y), item in s.hits.items() if where == "main" and item is tool)
        reverse = sorted(m[0] for m in scr.marks if m[3] & curses.A_REVERSE)
        band = all(10 <= pair(scr.cells[(y, lw + 3)][1]) <= 19 for y in rows)
        return rows, reverse, band

    now[0] = 1000.2
    rows, reverse, band = look()
    assert len(rows) >= 3 and reverse == rows  # the whole window, and nothing else
    now[0] = 1000.9
    rows, reverse, band = look()
    assert reverse == [] and band and s.active()  # fading: the shaded band
    assert pair(scr.cells[(scr.find("all done"), lw + 4)][1]) == 0  # the rest is as it was
    now[0] = 1001.5
    rows, reverse, band = look()
    assert reverse == [] and not band and not s.active()


def test_clicking_a_message_flashes_it_too_and_it_keeps_its_band(scr, tmp_path, monkeypatch):
    now = [50.0]
    monkeypatch.setattr(tui.time, "time", lambda: now[0])
    s = talked(tmp_path, monkeypatch)
    s.draw(scr)  # no 256 colours here: a message is in reverse video anyway, and the flash adds the tool's
    user, tool = s.items[0], s.items[1]
    s.pointer(5, 3, PRESS)
    s.pointer(5, 3, RELEASE)
    assert s.flash == (user, 50.0)
    s.draw(scr)
    mine = sorted(y for (where, y), item in s.hits.items() if where == "main" and item is user)
    assert sorted(m[0] for m in scr.marks if m[3] & curses.A_REVERSE) == mine
    s.flash = (tool, 50.0)
    now[0] = 50.9
    s.draw(scr)
    lit = {m[0] for m in scr.marks if m[3] & curses.A_REVERSE}
    assert {y for (where, y), item in s.hits.items() if where == "main"} == lit  # without a shade to fade to, reverse stays
    now[0] = 52.0
    s.draw(scr)
    assert sorted(m[0] for m in scr.marks if m[3] & curses.A_REVERSE) == mine
