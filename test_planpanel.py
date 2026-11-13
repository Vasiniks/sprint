"""the model's to-do list lives in the bottom half of the right-hand panel, under what the agent has done, so it stays
in sight while the conversation scrolls; with the panel hidden the newest list stands whole in the conversation"""
import pytest

import sprint_tui as tui
from sprint_tui import flat
from test_popups import Scr, finish, scr, screen  # noqa: F401  (scr is a fixture)

JOB = ("```todo\n[>] Make the file\n[ ] Show it\n```", "```write a.txt\nhi\n```", "```shell\ncat a.txt\n```",
       "```todo\n[x] Make the file\n[x] Show it\n```", "Shown.")


@pytest.fixture
def planned(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tui.sprint, "save_config", lambda **kw: None)
    s = screen(*JOB, "You are welcome.")
    s.sidebar = True
    s.submit("make a.txt and show it")
    finish(s)
    return s


def panel(s, scr):
    """the right-hand panel's rows as text, and the conversation's"""
    s.draw(scr)
    w, lw, side = s.geo
    return [scr.text(y)[w - side + 1:].rstrip() for y in range(scr.h)], [scr.text(y)[lw:w - side - 1] for y in range(scr.h)]


def test_the_list_is_in_the_bottom_half_of_the_right_hand_panel(planned, scr):
    side, main = panel(planned, scr)
    half = scr.h // 2
    head = next(y for y, row in enumerate(side) if row.startswith("Todo"))
    assert head == half and side[head].endswith("2/2")  # how far along, at a glance
    assert side[head + 1] == "✓ Make the file" and side[head + 2] == "✓ Show it"
    assert not any("✓ Make the file" in row or "✓ Show it" in row for row in main)  # and is not spread out in the conversation,
    assert sum("☰ todo" in row for row in main) == 2  # where each time it was brought up to date is one line
    activity = [y for y, row in enumerate(side) if row.startswith(("✓ write", "✓ shell"))]
    assert activity and max(activity) < half  # what it did stays above
    assert any(row.startswith("✓ todo 2/2") for row in side)  # with the list's own updates among it


def test_every_state_has_its_mark_and_the_item_in_hand_stands_out(planned, scr):
    planned.agent.todos = [("Make the file", "completed"), ("Show it", "in_progress"), ("Tidy up", "pending"), ("Add a map", "cancelled")]
    side, _ = panel(planned, scr)
    head = next(y for y, row in enumerate(side) if row.startswith("Todo"))
    assert side[head].endswith("1/4")
    assert side[head + 1:head + 5] == ["✓ Make the file", "▸ Show it", "· Tidy up", "– Add a map"]
    w = planned.geo[0]
    x = w - planned.geo[2] + 1
    assert tui.curses.A_BOLD & scr.cells[(head + 2, x + 2)][1]  # the one in hand stands out


def test_a_long_item_wraps_inside_the_panel_instead_of_being_cut(planned, scr):
    planned.agent.todos[0] = ("Read every configuration file in the project and list the ones that mention the old name", "completed")
    side, _ = panel(planned, scr)
    head = next(y for y, row in enumerate(side) if row.startswith("Todo"))
    block = " ".join(row.strip() for row in side[head + 1:head + 6])
    assert "list the ones that mention the old name" in block.replace("✓ ", "") and side[head + 2].startswith("  ")
    assert any(row == "✓ Show it" for row in side[head + 2:head + 7])


def test_a_long_list_is_kept_around_the_item_in_hand(planned, scr):
    planned.agent.todos = [(f"Step number {n}", "completed" if n < 20 else "in_progress" if n == 20 else "pending") for n in range(30)]
    side, _ = panel(planned, scr)
    head = next(y for y, row in enumerate(side) if row.startswith("Todo"))
    assert side[head].endswith("20/30") and "▸ Step number 20" in side[head + 1:] and "✓ Step number 0" not in side


def test_with_the_panel_hidden_the_newest_list_stands_whole_in_the_conversation(planned, scr):
    planned.sidebar = False
    planned.draw(scr)
    rows = [scr.text(y) for y in range(scr.h)]
    assert any("✓ Make the file" in row for row in rows) and any("✓ Show it" in row for row in rows)
    assert sum("☰ todo" in row for row in rows) == 1  # the older one is still a line


def test_a_folded_list_opens_when_clicked(planned, scr):
    panel(planned, scr)
    first = next(i for i in planned.items if i["t"] == "tool" and i["call"].tool == "todo")
    first["open"] = True
    _, main = panel(planned, scr)
    assert any("▸ Make the file" in row for row in main) and any("· Show it" in row for row in main)  # as it stood then


def test_with_no_plan_the_activity_has_the_whole_panel(tmp_path, monkeypatch, scr):
    monkeypatch.chdir(tmp_path)
    s = screen(*["```shell\necho %d\n```" % n for n in range(18)], "done", steps=40)
    s.sidebar = True
    s.submit("count")
    finish(s)
    side, _ = panel(s, scr)
    assert not any(row.startswith("Todo") for row in side)
    assert max(y for y, row in enumerate(side) if row.startswith("✓ shell")) > scr.h // 2


def test_a_finished_list_leaves_the_panel_when_you_send_the_next_message(planned, scr):
    side, _ = panel(planned, scr)
    assert any(row.startswith("Todo") for row in side)  # done, and still there to look at
    planned.submit("thanks")
    finish(planned)
    side, _ = panel(planned, scr)
    assert not any(row.startswith("Todo") for row in side)


# ---------- the plan's top edge can be dragged, and /plan off puts the plan away ----------

PRESS, MOVE, RELEASE = tui.curses.BUTTON1_PRESSED, tui.MOVED, tui.curses.BUTTON1_RELEASED


def head_of(side):
    return next((y for y, row in enumerate(side) if row.startswith("Todo")), None)


def test_switching_plan_off_puts_the_list_away_and_on_brings_it_back(planned, scr):
    planned.agent.planning = False
    side, main = panel(planned, scr)
    assert head_of(side) is None and not any("─" * 8 in row for row in side[9:])  # no list, and no edge left behind
    planned.agent.planning = True
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
    s = screen(*JOB)
    s.sidebar = True
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
