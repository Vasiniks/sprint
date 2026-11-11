"""exact colours on a terminal that shows 24-bit colour: what curses writes is routed through a filter that turns the
256-colour codes of the theme's slots into exact ones (curses itself cannot name a colour outside the 256)"""
import os

import pytest

import sprint_tui as tui

NAVY, TEXT = "#181e29", "#efefef"


def code(ground, colour):
    """what curses writes to set a slot: ground 3 is the text, 4 the background"""
    return b"\x1b[%d8;5;%dm" % (ground, tui.SLOT[colour])


def test_the_theme_slots_go_out_as_24_bit_colours_and_nothing_else_changes():
    data = b"\x1b[H" + code(3, TEXT) + code(4, NAVY) + b"hi\x1b[48;5;232m\x1b[38;5;39m\x1b[31m\x1b[K"
    assert tui.truecolour(data) == b"\x1b[H\x1b[38;2;239;239;239m\x1b[48;2;24;30;41mhi\x1b[48;5;232m\x1b[38;5;39m\x1b[31m\x1b[K"


def test_every_slot_is_a_colour_number_curses_can_name():
    assert min(tui.SLOT.values()) >= 88 and max(tui.SLOT.values()) < 232  # 232 and up are greys the plain themes use by number


@pytest.mark.parametrize("tail", [b"\x1b", b"\x1b[", b"\x1b[4", b"\x1b[48", b"\x1b[48;", b"\x1b[48;5", b"\x1b[48;5;", b"\x1b[48;5;10"])
def test_a_code_cut_in_two_by_a_read_waits_for_its_other_half(tail):
    assert tui.whole(b"abc" + tail) == (b"abc", tail)


@pytest.mark.parametrize("data", [b"abc", b"abc\x1b[48;5;100m", b"abc\x1b[K", b"\x1b[48;5;100mabc", b"abc\x1b[1;1H", b""])
def test_finished_codes_are_not_held_back(data):
    assert tui.whole(data) == (data, b"")


@pytest.mark.skipif(not tui.VT, reason="only where the terminal is written to with escape codes")
def test_what_is_written_to_a_routed_terminal_arrives_translated_and_afterwards_untouched():
    r, w = os.pipe()  # w stands in for the terminal
    assert not tui.ROUTED
    back = tui.route(w)
    assert tui.ROUTED
    cut = code(4, NAVY)
    os.write(w, b"one" + cut[:6])
    os.write(w, cut[6:] + b"two")
    back()
    assert not tui.ROUTED
    assert os.read(r, 1000) == b"one\x1b[48;2;24;30;41mtwo"
    os.write(w, cut)  # the terminal is itself again
    assert os.read(r, 1000) == cut
    os.close(r), os.close(w)


def test_a_terminal_is_routed_only_if_it_says_it_shows_24_bit_colour(monkeypatch):
    monkeypatch.setattr(tui, "VT", True)
    monkeypatch.setattr(tui.os, "isatty", lambda fd: True)
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert tui.can_route()
    monkeypatch.setenv("COLORTERM", "24bit")
    assert tui.can_route()
    monkeypatch.setattr(tui.os, "isatty", lambda fd: fd != 2)  # curses needs a terminal left to ask about size and keys
    assert not tui.can_route()
    monkeypatch.setattr(tui.os, "isatty", lambda fd: True)
    monkeypatch.delenv("COLORTERM")
    assert not tui.can_route()
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setattr(tui, "VT", False)  # Windows: curses there does not write escape codes
    assert not tui.can_route()
