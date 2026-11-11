"""plan mode's checklist lives in the bottom half of the right-hand panel, under what the agent has done, so it stays in
sight while the conversation scrolls; with the panel hidden it stays in the conversation, as before"""
import pytest

import sprint_tui as tui
from sprint_tui import flat
from test_popups import Scr, finish, scr, screen  # noqa: F401  (scr is a fixture)


@pytest.fixture
def planned(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tui.sprint, "save_config", lambda **kw: None)
    s = screen("1. Make the file\n2. Show it", "```write a.txt\nhi\n```", "Made.", "```shell\ncat a.txt\n```", "Shown.")
    s.plan = s.sidebar = True
    s.submit("make a.txt and show it")
    finish(s)
    return s


def panel(s, scr):
    """the right-hand panel's rows as text, and the conversation's"""
    s.draw(scr)
    w, lw, side = s.geo
    return [scr.text(y)[w - side + 1:].rstrip() for y in range(scr.h)], [scr.text(y)[lw:w - side - 1] for y in range(scr.h)]


def test_the_plan_is_in_the_bottom_half_of_the_right_hand_panel(planned, scr):
    side, main = panel(planned, scr)
    half = scr.h // 2
    head = next(y for y, row in enumerate(side) if row.startswith("Plan"))
    assert head == half and side[head].endswith("2/2")  # how far along, at a glance
    assert side[head + 1] == "✓ Make the file" and side[head + 2] == "✓ Show it"
    assert not any("Make the file" in row for row in main)  # and no longer takes up the conversation
    activity = [y for y, row in enumerate(side) if row.startswith(("✓ write", "✓ shell"))]
    assert activity and max(activity) < half  # what it did stays above


def test_the_step_in_hand_is_marked_and_the_rest_are_still_to_do(planned, scr):
    plan = next(i for i in planned.items if i["t"] == "plan")
    plan["at"] = 1
    side, _ = panel(planned, scr)
    head = next(y for y, row in enumerate(side) if row.startswith("Plan"))
    assert side[head].endswith("1/2") and side[head + 1] == "✓ Make the file" and side[head + 2] == "▸ Show it"
    w = planned.geo[0]
    x = w - planned.geo[2] + 1
    assert tui.curses.A_BOLD & scr.cells[(head + 2, x + 2)][1]  # the one in hand stands out


def test_a_long_step_wraps_inside_the_panel_instead_of_being_cut(planned, scr):
    plan = next(i for i in planned.items if i["t"] == "plan")
    plan["steps"][0] = "Read every configuration file in the project and list the ones that mention the old name"
    side, _ = panel(planned, scr)
    head = next(y for y, row in enumerate(side) if row.startswith("Plan"))
    block = " ".join(row.strip() for row in side[head + 1:head + 6])
    assert "list the ones that mention the old name" in block.replace("✓ ", "") and side[head + 2].startswith("  ")
    assert any(row == "✓ Show it" for row in side[head + 2:head + 7])


def test_with_the_panel_hidden_the_plan_stays_in_the_conversation(planned, scr):
    planned.sidebar = False
    planned.draw(scr)
    assert any("✓ Make the file" in scr.text(y) for y in range(scr.h))


def test_with_no_plan_the_activity_has_the_whole_panel(tmp_path, monkeypatch, scr):
    monkeypatch.chdir(tmp_path)
    s = screen(*["```shell\necho %d\n```" % n for n in range(18)], "done", steps=40)
    s.sidebar = True
    s.submit("count")
    finish(s)
    side, _ = panel(s, scr)
    assert not any(row.startswith("Plan") for row in side)
    assert max(y for y, row in enumerate(side) if row.startswith("✓ shell")) > scr.h // 2


def test_a_new_message_without_a_plan_clears_the_panel_of_the_old_one(planned, scr):
    planned.items.append({"t": "user", "text": "thanks"})
    side, _ = panel(planned, scr)
    assert not any(row.startswith("Plan") for row in side)
