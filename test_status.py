"""a command that switches or sets something shows what it stands at now, at the far right: of its row in the list of
commands, and of the prompt once it is typed in full (so you know what Enter will change)"""
import pytest

import sprint_tui as tui
from sprint_tui import flat
from test_popups import Scr, scr, screen  # noqa: F401  (scr is a fixture)


def bar(s, text, w=70):
    s.buf, s.cur = text, len(text)
    return s.input_rows(w)[0][1]


def test_a_command_typed_in_full_shows_its_current_state_at_the_far_right_of_the_prompt():
    s = screen()
    row = bar(s, "/showthink")
    assert row[-1] == ("off", "dim") and len(flat(row)) == 70 and flat(row).startswith(f"{tui.MARK} /showthink")
    s.showthink = True
    assert bar(s, "/showthink")[-1] == ("on", "ok")
    s.agent.think = True
    assert bar(s, "/think")[-1] == ("on", "ok")
    assert bar(s, "/mode")[-1] == (s.agent.mode, s.agent.mode)
    assert bar(s, "/mode edits")[-1] == (s.agent.mode, s.agent.mode)  # what it is now, whatever you are about to make it
    assert bar(s, "/theme")[-1][0] == s.theme and bar(s, "/effort")[-1][0] == s.agent.effort
    assert bar(s, "/model")[-1][0] == tui.short(s.agent.model)


def test_commands_with_nothing_to_report_and_ordinary_text_get_no_state():
    s = screen()
    for text in ("/help", "/clear", "/copy", "hello", "/showthin", "say /think"):
        assert flat(bar(s, text)).rstrip() == f"{tui.MARK} {text}", text


def test_every_switch_reports_on_or_off():
    s = screen()
    for cmd in ("/think", "/showthink", "/plan", "/sidebar", "/history", "/mouse"):
        assert s.state_of(cmd)[0] in ("on", "off"), cmd
    s.mouse_on = False
    assert s.state_of("/mouse") == ("off", "dim")


def test_the_list_of_commands_shows_each_state_at_the_far_right_of_its_row(scr):
    s = screen()
    s.buf, s.cur = "/sho", 4
    s.draw(scr)
    y = scr.find("/showthink")
    left = scr.text(y).index("/showthink")
    bx = left - 5
    bw = min(scr.w - 8, 76)
    assert scr.text(y)[:bx + bw].rstrip().endswith("off") and scr.text(y)[bx + bw - 4:bx + bw].strip() == "off"
    s.showthink = True
    s.draw(scr)
    assert scr.text(scr.find("/showthink"))[:bx + bw].rstrip().endswith(" on")


def test_among_a_commands_choices_the_one_in_force_is_marked(scr):
    s = screen(mode="edits")
    s.buf, s.cur = "/mode ", 6
    s.draw(scr)
    assert scr.text(scr.find("file edits run")).rstrip().endswith("current")
    assert not scr.text(scr.find("asks before every")).rstrip().endswith("current")
