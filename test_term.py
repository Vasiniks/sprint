"""the curses macOS ships sends a run of one character, when the terminal's description offers a "repeat" code, as a
single byte and a count: right for a letter, garbage for anything else. Under cmux/Ghostty, kitty and others that meant
no bottom to a box (a run of ─), rubbish for the empty part of the context bar (·) and for the animation's track (▱).
Where that can happen curses is given a description without the repeat code"""
import pytest

import sprint_tui as tui


def describe(monkeypatch, with_rep, version=(6, 0, 20150808)):
    monkeypatch.setattr(tui, "VT", True)
    monkeypatch.setattr(tui, "has_repeat", lambda term, change=None: (term, "TERMINFO" in (change or {})) in with_rep or term in with_rep)
    monkeypatch.setattr(tui.curses, "ncurses_version", version, raising=False)


def test_an_old_curses_is_kept_away_from_a_description_that_repeats(monkeypatch):
    describe(monkeypatch, {"xterm-ghostty"})
    monkeypatch.setenv("TERM", "xterm-ghostty")
    assert tui.term_for_curses() == {"TERM": "xterm-256color"}
    monkeypatch.setenv("TERM", "xterm-256color")  # Apple's Terminal: nothing to avoid
    assert tui.term_for_curses() is None


def test_a_curses_that_gets_it_right_keeps_the_terminals_own_description(monkeypatch):
    describe(monkeypatch, {"xterm-ghostty"}, version=(6, 5, 20240427))
    monkeypatch.setenv("TERM", "xterm-ghostty")
    assert tui.term_for_curses() is None


def test_a_terminal_that_brings_its_own_plain_description_gets_the_systems_copy(monkeypatch):
    """cmux ships an xterm-256color of its own, with the repeat code in it"""
    describe(monkeypatch, {"xterm-ghostty", ("xterm-256color", False)})
    monkeypatch.setenv("TERM", "xterm-ghostty")
    assert tui.term_for_curses() == {"TERM": "xterm-256color", "TERMINFO": None, "TERMINFO_DIRS": None}


def test_where_every_plain_description_repeats_too_there_is_nothing_better_to_offer(monkeypatch):
    describe(monkeypatch, {"xterm-ghostty", "xterm-256color"})
    monkeypatch.setenv("TERM", "xterm-ghostty")
    assert tui.term_for_curses() is None


def test_not_on_windows(monkeypatch):
    describe(monkeypatch, {"xterm-ghostty"})
    monkeypatch.setattr(tui, "VT", False)
    monkeypatch.setenv("TERM", "xterm-ghostty")
    assert tui.term_for_curses() is None


def test_a_description_is_read_with_the_systems_own_tool(monkeypatch):
    class Done:
        def __init__(self, out):
            self.stdout, self.returncode = out, 0

    monkeypatch.setattr(tui.subprocess, "run", lambda argv, **kw: Done("\tkmous=\\E[<,\n\trep=%p1%c\\E[%p2%{1}%-%db,\n" if argv[-1] == "xterm-ghostty" else "\tkmous=\\E[M,\n"))
    assert tui.has_repeat("xterm-ghostty") and not tui.has_repeat("xterm-256color")
    monkeypatch.setattr(tui.subprocess, "run", lambda argv, **kw: (_ for _ in ()).throw(OSError("no infocmp")))
    assert not tui.has_repeat("xterm-ghostty")  # cannot tell: leave things as they are


def test_the_swap_lasts_only_while_curses_starts_so_commands_still_see_the_real_terminal(monkeypatch):
    import io
    from sprint import Agent
    seen = {}
    monkeypatch.setattr(tui, "term_for_curses", lambda: {"TERM": "xterm-256color", "TERMINFO": None})
    monkeypatch.setattr(tui, "can_route", lambda: False)
    monkeypatch.setenv("TERM", "xterm-ghostty")
    monkeypatch.setenv("TERMINFO", "/its/own")

    def wrapper(loop, *args):
        seen["starting"] = tui.os.environ["TERM"]
        seen["terminfo"] = tui.os.environ.get("TERMINFO")
        return None

    monkeypatch.setattr(tui.curses, "wrapper", wrapper)
    monkeypatch.setattr(tui.threading.Thread, "start", lambda self: None)
    tui.run(Agent(chat=lambda m: iter(()), out=io.StringIO()))
    assert seen["starting"] == "xterm-256color" and seen["terminfo"] is None
    assert tui.os.environ["TERM"] == "xterm-ghostty" and tui.os.environ["TERMINFO"] == "/its/own"


# ---------- the cells of the animation and the mode icons, where the font has no slanted ones ----------

def test_ghostty_and_cmux_get_the_square_cells_their_own_font_has(monkeypatch):
    """their built-in font (JetBrains Mono) lacks the slanted cells: borrowed from another font they came out wider than
    a cell and ran into each other along the animation's track"""
    monkeypatch.setattr(tui, "VT", True)
    assert tui.cell_glyphs({"TERM": "xterm-256color", "TERM_PROGRAM": "Apple_Terminal"}) == "▰▱"
    assert tui.cell_glyphs({"TERM": "xterm-ghostty"}) == "■□"
    assert tui.cell_glyphs({"TERM": "xterm-256color", "TERM_PROGRAM": "ghostty"}) == "■□"
    assert tui.cell_glyphs({"TERM": "xterm-256color", "CMUX_WORKSPACE_ID": "1"}) == "■□"  # cmux, whatever it calls the terminal
    assert tui.cell_glyphs({"TERM": "xterm-256color", "GHOSTTY_RESOURCES_DIR": "/x"}) == "■□"
    monkeypatch.setattr(tui, "VT", False)
    assert tui.cell_glyphs({"TERM": "xterm-256color"}) == "■□"  # Windows, as before
    assert tui.MODE_ICON["edits"] == tui.CELLS[0] * 2 + tui.CELLS[1]
