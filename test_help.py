"""help that can be read at a glance: sections, one thing per line in two aligned columns, names in colour.
In the full-screen app /help is a list you can filter and pick a command from; `sprint help` and plain mode print it"""
import re

import sprint
import sprint_tui as tui
from sprint_tui import flat
from test_popups import screen

CODES = re.compile(r"\x1b\[[\d;]*m")


def rows(s):
    return s.shown()[0]


def test_help_is_a_list_in_sections_with_every_command_on_its_own_line():
    s = screen()
    s.submit("/help")
    p = s.popup
    assert p["title"] == "Help" and p["field"]["filter"]
    heads = [flat(r["s"]) for r in rows(s) if r["v"] is None and flat(r["s"]).strip()]
    assert heads == ["In a message", "Keys", "When the model asks you", "Mouse", "Commands"]
    assert all(r["s"][0][1] == "title" for r in rows(s) if r["v"] is None and flat(r["s"]).strip())
    text = "\n".join(flat(r["s"]) for r in rows(s))
    for name, about in tui.COMMANDS:
        line = next(flat(r["s"]) for r in rows(s) if r["v"] == name)
        assert line.strip().startswith(name) and about in line, name
    for word in ("@path", "shift+enter", "ctrl+c", "esc", "tab", "/model [name]", "/copy [code|all]"):
        assert word in text, word


def test_names_are_coloured_and_what_they_do_starts_in_one_column():
    s = screen()
    s.submit("/help")
    items = [r for r in rows(s) if r["v"] is not None]
    assert len(items) > 40
    starts = set()
    for r in items:
        name, about = r["s"][0], r["s"][-1]
        assert name[1] in ("code", "kw") and about[1] == "", flat(r["s"])
        starts.add(len(flat(r["s"])) - len(about[0]))
    assert len(starts) == 1  # one column for every description
    hints = [span for r in items for span in r["s"] if span[0].strip().startswith("[")]
    assert hints and all(role == "dim" for _, role in hints)  # what a command takes is greyed


def test_typing_narrows_help_to_what_matches():
    s = screen()
    s.submit("/help")
    for ch in "theme":
        s.popup_key(ch)
    left = [r["v"] for r in rows(s) if r["v"] is not None]
    assert "/theme" in left and "/model" not in left


def test_enter_on_a_command_puts_it_in_the_prompt_and_on_a_key_just_closes():
    s = screen()
    s.submit("/help")
    for ch in "/model":
        s.popup_key(ch)
    s.popup_key("\r")
    assert s.popup is None and s.buf == "/model " and s.cur == len(s.buf)
    s.buf, s.cur = "", 0
    s.submit("/help")
    for ch in "shift+enter":
        s.popup_key(ch)
    s.popup_key("\r")
    assert s.popup is None and s.buf == ""


def test_sprint_help_is_plain_in_a_pipe_and_coloured_on_a_terminal():
    plain, coloured = sprint.usage(False), sprint.usage(True)
    assert "\x1b" not in plain and "\x1b[" in coloured and CODES.sub("", coloured) == plain
    lines = plain.split("\n")
    assert lines[0].startswith("sprint " + sprint.VERSION)
    heads = [l for l in lines[1:] if l and not l.startswith(" ") and not l.startswith("Inside")]
    assert heads == ["Start", "Local server", "Settings and updates"]
    starts = {len(l) - len(l[2:].split("  ", 1)[1].lstrip()) for l in lines if l.startswith("  sprint")}
    assert len(starts) == 1  # the descriptions line up
    assert "\x1b[1m" in coloured and "\x1b[36m" in coloured  # headings bold, commands in colour


def test_no_color_keeps_sprint_help_plain_even_on_a_terminal(monkeypatch, capsys):
    monkeypatch.setattr(sprint.sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    assert sprint.cli(["help"]) == 0
    assert "\x1b" not in capsys.readouterr().out


def test_plain_mode_help_is_one_command_per_line():
    table = sprint.help_rows()
    names = [flat(r).split()[0] for r in table]
    for name in ("@file", "!command", "/model", "/usage", "/compact", "/bye"):
        assert name in names
    assert all(r[0][1] == "cyan" and r[-1][1] == "" for r in table)
    assert len({len(flat(r)) - len(r[-1][0]) for r in table}) == 1  # aligned
