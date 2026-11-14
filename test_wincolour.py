"""colours on the Windows curses (PDCurses), which differs from every other in two ways, both found on a real machine:
it makes bold text "bright" by adding 8 to the colour's number whatever the number is (so the sprint theme's cyan, 39,
came out as 47, a green: the word "ask" by the prompt), and it numbers the eight basic colours blue-green-red, not
red-green-blue. And the mark that leads the prompt, which is ❯ wherever the terminal can draw it"""
import io

import sprint_tui as tui
from sprint import Agent
from sprint_tui import Screen
from test_popups import painted
from test_sprint import scripted

PDCURSES = (0, 4, 2, 6, 1, 5, 3, 7)  # what COLOR_BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN and WHITE are there


def test_the_nearest_colour_can_be_asked_for_among_those_bold_leaves_alone():
    assert tui.near("#19c2ff") == 39 and tui.near("#19c2ff", True) == 45  # the next nearest blue, not a green
    assert tui.PALETTE[45] == (0, 215, 255)
    for t in tui.THEMES.values():
        for colour in (v for v in t.values() if isinstance(v, str)):
            n = tui.near(colour, True)
            assert n | 8 == n and n in tui.PALETTE
    assert tui.near("#ffe100") == tui.near("#ffe100", True) == 220  # one that bold never moved stays where it was


def test_on_windows_no_text_colour_is_one_that_bold_would_change(monkeypatch):
    monkeypatch.setattr(tui, "BRIGHTENS", True)
    for theme in tui.THEMES:
        s, pairs = painted(monkeypatch, theme)
        text = [pairs[n][0] for n in pairs if n not in (9, 19)]  # all but greyed-out text, which is never bold
        assert all(fg < 16 or fg | 8 == fg for fg in text if fg >= 0), theme
    s, pairs = painted(monkeypatch, "sprint")
    t = tui.THEMES["sprint"]
    assert pairs[6] == (45, tui.near(t["bg"])) and pairs[16][0] == 45 and pairs[236][0] == 45  # cyan, on each ground
    assert pairs[8] == (tui.near(t["fg"], True), tui.near(t["bg"]))  # the grounds are as near as they ever were
    assert not s.exact


def test_elsewhere_the_nearest_colour_is_the_nearest(monkeypatch):
    monkeypatch.setattr(tui, "BRIGHTENS", False)
    s, pairs = painted(monkeypatch, "sprint")
    assert pairs[6][0] == 39


def test_on_windows_exact_colours_are_not_tried_since_bold_would_move_their_slots_too(monkeypatch):
    monkeypatch.setattr(tui, "BRIGHTENS", True)
    monkeypatch.setattr(tui, "can_exact", lambda: True)
    monkeypatch.setattr(tui.curses, "init_color", lambda *a: None, raising=False)
    s, pairs = painted(monkeypatch, "sprint")
    assert not s.exact and pairs[6][0] == 45


def test_the_basic_colours_are_asked_for_by_name_not_by_number(monkeypatch):
    monkeypatch.setattr(tui, "BASIC", PDCURSES)
    s, pairs = painted(monkeypatch, "terminal")  # the terminal's own colours, on a terminal with 256
    assert [pairs[i][0] for i in range(1, 8)] == [4, 2, 6, 1, 5, 3, 7]  # red is 4 there, and cyan 3
    s, pairs = painted(monkeypatch, "sprint", colours=8)  # and on one with eight, whatever the theme
    assert [pairs[i][0] for i in range(1, 8)] == [4, 2, 6, 1, 5, 3, 7]
    assert s.ink(236) == 236 and s.ink(-1) == -1  # a number that is no basic colour is itself


def test_here_the_basic_colours_have_the_numbers_they_always_had():
    assert tui.BASIC == tuple(getattr(tui.curses, "COLOR_" + n) for n in ("BLACK", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE"))
    s = Screen(Agent(chat=scripted(), out=io.StringIO()))
    assert [s.ink(i) for i in range(8)] == list(tui.BASIC)


def test_the_prompt_mark_is_the_heavy_angle_wherever_the_terminal_can_draw_it():
    assert tui.prompt_mark(False, {}) == "❯"  # anywhere but Windows
    assert tui.prompt_mark(True, {"WT_SESSION": "abc"}) == "❯"  # Windows Terminal
    assert tui.prompt_mark(True, {"TERM_PROGRAM": "vscode"}) == "❯" and tui.prompt_mark(True, {"TERM": "xterm-256color"}) == "❯"  # an editor's, ssh
    assert tui.prompt_mark(True, {}) == ">"  # the old console window: its fonts have no such character
    assert tui.MARK == tui.prompt_mark(not tui.VT, tui.os.environ)
