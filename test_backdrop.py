"""a theme's background reaches the edges of the window. A terminal keeps a margin round its grid of characters, and
that margin is in the terminal's own background colour, which no character can paint: so while sprint runs it gives the
terminal itself the theme's background, and hands back the old one when it leaves"""
import io

import pytest

import sprint
import sprint_tui as tui
from sprint import Agent
from test_popups import painted
from test_sprint import scripted

NAVY = "#181e29"
SET, BACK = "\033]11;#%02x%02x%02x\007", "\033]111\007"


@pytest.fixture
def live(monkeypatch):
    """painting as on a real screen at a terminal that takes escape codes and is not Apple's"""
    monkeypatch.setattr(tui, "VT", True)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.setattr(tui, "can_exact", lambda: False)


def shown(monkeypatch, theme, real=True, **kw):
    """a screen painted in a theme as the running app paints it: on a real screen, with someone at it"""
    s, pairs = painted(monkeypatch, theme, **kw)
    s.live = real
    s.paint()
    return s


def test_the_terminal_is_given_the_colour_the_cells_are_drawn_in(live, monkeypatch, capsys):
    s = shown(monkeypatch, "sapphire")  # this terminal shows the nearest of its 256
    near = tui.PALETTE[tui.near(NAVY)]
    assert SET % near in capsys.readouterr().out  # so the margin matches the cells exactly, not the theme on paper
    monkeypatch.setattr(tui, "ROUTED", True)
    monkeypatch.setattr(tui, "speaks_256", lambda: True)
    s = shown(monkeypatch, "sapphire")
    assert s.exact and SET % (0x18, 0x1e, 0x29) in capsys.readouterr().out


def test_a_theme_that_leaves_the_screen_to_the_terminal_hands_its_background_back(live, monkeypatch, capsys):
    s = shown(monkeypatch, "sapphire")
    capsys.readouterr()
    s.theme = "terminal"
    s.paint()
    out = capsys.readouterr().out
    assert BACK in out and "\033]11;" not in out
    s.theme = "mono"
    s.paint()
    assert "\033]11;" not in capsys.readouterr().out


def test_leaving_hands_it_back_too_and_nothing_is_touched_where_there_is_no_real_screen(live, monkeypatch, capsys):
    s = shown(monkeypatch, "sapphire")
    capsys.readouterr()
    s.backdrop(None)
    assert capsys.readouterr().out == BACK
    s = shown(monkeypatch, "sapphire", real=False)
    assert "\033]1" not in capsys.readouterr().out


def test_with_fewer_than_256_colours_the_screen_stays_the_terminals_and_so_does_its_background(live, monkeypatch, capsys):
    s = shown(monkeypatch, "sapphire", colours=8)
    assert "\033]11;" not in capsys.readouterr().out


# ---------- Apple's Terminal takes no such code: its window is asked through the Terminal app itself ----------

@pytest.fixture
def apple(live, monkeypatch):
    calls = []
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    monkeypatch.setattr(tui.os, "ttyname", lambda fd: "/dev/ttys042", raising=False)  # (Windows has no such call to stand in for)
    monkeypatch.setattr(tui, "apple_window", lambda tty, rgb: calls.append((tty, rgb)) or (65535, 65535, 65535))
    return calls


def settle(s):
    if s.backdrop_job:
        s.backdrop_job.join(5)


def test_apples_terminal_window_is_recoloured_and_given_back_exactly_what_it_had(apple, monkeypatch):
    s = shown(monkeypatch, "sapphire")
    settle(s)
    near = tuple(v * 257 for v in tui.PALETTE[tui.near(NAVY)])  # that app counts each colour to 65535
    assert apple == [("/dev/ttys042", near)]
    s.theme = "paper"
    s.paint()
    settle(s)
    assert len(apple) == 2 and apple[1][1] != near
    s.backdrop(None)
    settle(s)
    assert apple[2] == ("/dev/ttys042", (65535, 65535, 65535))  # what the window had before sprint, not the theme before


def test_leaving_before_the_window_has_even_answered_still_gives_it_back(apple, monkeypatch):
    import threading
    calls, go = [], threading.Event()

    def slow(tty, rgb):
        calls.append(rgb)
        go.wait(5)
        return (65535, 65535, 65535)

    monkeypatch.setattr(tui, "apple_window", slow)
    s = shown(monkeypatch, "sapphire")
    s.backdrop(None)  # gone again at once
    go.set()
    settle(s)
    assert len(calls) == 2 and calls[1] == (65535, 65535, 65535)


def test_a_window_sprint_never_recoloured_is_left_alone_on_the_way_out(apple, monkeypatch):
    s = shown(monkeypatch, "terminal")
    s.backdrop(None)
    settle(s)
    assert apple == []


def test_the_script_names_the_window_by_its_terminal_and_counts_colours_as_that_app_does(monkeypatch):
    ran = []

    class Done:
        stdout = "1000 2000 3000\n"

    monkeypatch.setattr(tui.subprocess, "run", lambda argv, **kw: ran.append((argv, kw)) or Done())
    assert tui.apple_script_window("/dev/ttys042", (6168, 7710, 10537)) == (1000, 2000, 3000)
    argv, kw = ran[0]
    assert argv[0] == "osascript" and argv[-4:] == ["/dev/ttys042", "6168", "7710", "10537"] and kw["timeout"] <= 5
    assert "background color" in argv[2] and "tty of" in argv[2]
    monkeypatch.setattr(tui.subprocess, "run", lambda argv, **kw: (_ for _ in ()).throw(OSError("no osascript")))
    assert tui.apple_script_window("/dev/ttys042", (1, 2, 3)) is None  # no such tool: nothing happens, nothing breaks


# ---------- Windows ----------

def test_on_windows_it_is_tried_only_in_windows_terminal_and_only_if_codes_can_be_switched_on(monkeypatch, capsys):
    monkeypatch.setattr(tui, "VT", False)
    monkeypatch.setattr(tui, "can_exact", lambda: False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.setattr(tui, "windows_codes", lambda: False)
    shown(monkeypatch, "sapphire")
    assert "\033]11;" not in capsys.readouterr().out
    monkeypatch.setattr(tui, "windows_codes", lambda: True)
    shown(monkeypatch, "sapphire")
    assert "\033]11;" in capsys.readouterr().out


# ---------- on Apple's Terminal the cells then use the window's own background, so the two cannot differ ----------
# (that app does not show a colour it is given through scripting quite as it shows the same colour drawn in a cell: on
# the more colourful themes the margin came out a shade off the screen)

def test_once_the_window_has_the_colour_the_cells_borrow_it_instead_of_painting_their_own(apple, monkeypatch):
    pairs = {}
    s = shown(monkeypatch, "sapphire")
    monkeypatch.setattr(tui.curses, "init_pair", lambda n, fg, bg: pairs.__setitem__(n, (fg, bg)))
    settle(s)
    assert s.repaint  # the window answered: the screen is to be painted again
    s.paint()
    settle(s)
    assert pairs[8][1] == -1 and pairs[1][1] == -1 and pairs[9][1] == -1  # plain text, the hues, greyed text: on the window's own
    assert pairs[10][1] != -1 and pairs[230][1] != -1  # the panel and the band keep their own shades
    assert len(apple) == 1  # and the window is not asked again for what it already has


def test_a_new_theme_is_painted_in_full_until_the_window_has_followed(apple, monkeypatch):
    pairs = {}
    s = shown(monkeypatch, "sapphire")
    settle(s)
    s.paint()
    monkeypatch.setattr(tui.curses, "init_pair", lambda n, fg, bg: pairs.__setitem__(n, (fg, bg)))
    hold = []
    monkeypatch.setattr(tui, "apple_window", lambda tty, rgb: hold.append(rgb) and None)  # the window is slow to answer, or cannot
    s.theme = "paper"
    s.paint()
    settle(s)
    assert pairs[8][1] != -1  # paper's own background, in every cell
    s.paint()
    assert pairs[8][1] != -1  # and it stays so while the window has not taken it


def test_a_window_that_cannot_be_asked_leaves_the_cells_painted(apple, monkeypatch):
    pairs = {}
    monkeypatch.setattr(tui, "apple_window", lambda tty, rgb: None)
    s = shown(monkeypatch, "sapphire")
    monkeypatch.setattr(tui.curses, "init_pair", lambda n, fg, bg: pairs.__setitem__(n, (fg, bg)))
    settle(s)
    s.paint()
    assert pairs[8][1] != -1 and not s.repaint


def test_other_terminals_keep_painting_every_cell(live, monkeypatch):
    pairs = {}
    s = shown(monkeypatch, "sapphire")
    monkeypatch.setattr(tui.curses, "init_pair", lambda n, fg, bg: pairs.__setitem__(n, (fg, bg)))
    s.paint()
    assert pairs[8][1] != -1
