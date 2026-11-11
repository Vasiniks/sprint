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


# ---------- the plan's top edge can be dragged, and /plan off puts the plan away ----------

PRESS, MOVE, RELEASE = tui.curses.BUTTON1_PRESSED, tui.MOVED, tui.curses.BUTTON1_RELEASED


def head_of(side):
    return next((y for y, row in enumerate(side) if row.startswith("Plan")), None)


def test_switching_plan_mode_off_puts_the_plan_away_and_on_brings_it_back(planned, scr):
    planned.plan = False
    side, main = panel(planned, scr)
    assert head_of(side) is None and not any("─" * 8 in row for row in side[9:])  # no plan, and no edge left behind
    assert not any("Make the file" in row for row in main)  # nor does it spill back into the conversation
    planned.plan = True
    side, _ = panel(planned, scr)
    assert head_of(side) == scr.h // 2


def test_a_line_you_can_take_hold_of_sits_above_the_plan(planned, scr):
    side, _ = panel(planned, scr)
    head = head_of(side)
    assert set(side[head - 1]) == {"─"} and len(side[head - 1]) >= 20
    assert planned.plan_edge == head - 1


def test_dragging_that_line_up_gives_the_plan_more_of_the_panel(planned, scr):
    saved = []
    tui.sprint.save_config = lambda **kw: saved.append(kw)
    side, _ = panel(planned, scr)
    w, lw, sw = planned.geo
    x, edge = w - sw + 8, planned.plan_edge
    planned.pointer(x, edge, PRESS)
    assert planned.drag == "plan" and planned.select is None
    planned.pointer(x, edge - 4, MOVE, exact=True)
    side, _ = panel(planned, scr)
    assert head_of(side) == edge - 3 and set(side[edge - 4]) == {"━"}  # the line follows, and shows it is held
    planned.pointer(x, edge - 4, RELEASE)
    assert planned.drag is None and saved[-1] == {"plan_h": scr.h - (edge - 3)}
    side, _ = panel(planned, scr)
    assert head_of(side) == edge - 3 and set(side[edge - 4]) == {"─"}


def test_the_line_stops_short_of_squeezing_out_the_activity_or_the_plan(planned, scr):
    panel(planned, scr)
    w, lw, sw = planned.geo
    x = w - sw + 8
    planned.pointer(x, planned.plan_edge, PRESS)
    planned.pointer(x, 0, MOVE, exact=True)
    side, _ = panel(planned, scr)
    assert head_of(side) == 11 and side[9].startswith("✓")  # one line of what it did is always left
    planned.pointer(x, scr.h - 1, MOVE, exact=True)
    side, _ = panel(planned, scr)
    assert head_of(side) == scr.h - 4 and side[scr.h - 3].startswith(("✓", "▸", "·"))  # and one step of the plan
    planned.pointer(x, scr.h - 1, RELEASE)


def test_the_size_you_gave_the_plan_is_kept(tmp_path, monkeypatch, scr):
    monkeypatch.chdir(tmp_path)
    s = screen("1. Make the file\n2. Show it", "```write a.txt\nhi\n```", "Made.", "```shell\ncat a.txt\n```", "Shown.")
    s.plan = s.sidebar = True
    s.submit("make a.txt and show it")
    finish(s)
    panel(s, scr)
    w, lw, sw = s.geo
    s.pointer(w - sw + 8, s.plan_edge, PRESS)
    s.pointer(w - sw + 8, 19, MOVE, exact=True)
    s.pointer(w - sw + 8, 19, RELEASE)
    assert tui.sprint.load_config()["plan_h"] == scr.h - 20 and tui.sprint.SETTINGS["plan_h"] is int
    again = screen()
    assert again.plan_h == scr.h - 20
