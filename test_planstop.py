"""plan mode's first request (the one that asks the model for its steps) used to be a blind spot: nothing showed, esc
could not stop it, and it reasoned at full effort for a minute or more. While it ran, every ctrl+c tried to stop it
again, so there was no way out of sprint at all. A turn can now always be stopped, and sprint can always be left"""
import io
import threading
import time

import pytest

import sprint
import sprint_tui as tui
from sprint import Agent
from sprint_tui import flat


def planner(*chunks, then=()):
    """an agent whose model writes these chunks as its plan; then() runs after the first, with the agent"""
    seen = {"calls": 0, "think": [], "limit": [], "thread": [], "status": []}

    def chat(msgs):
        seen["calls"] += 1
        seen["think"].append(a.think), seen["limit"].append(a.limit), seen["thread"].append(a.thread)
        for n, c in enumerate(chunks):
            yield c
            if n == 0:
                for step in then:
                    step(a)

    a = Agent(chat=chat, out=io.StringIO())
    a.status = lambda s: seen["status"].append(s)
    return a, seen


def test_the_planning_request_can_be_stopped_and_nothing_runs_after_it():
    a, seen = planner("1. Look\n", "2. Change\n", "3. Check\n", then=[lambda a: a.stop()])
    with pytest.raises(KeyboardInterrupt):
        a.run_plan("tidy the parser")
    assert seen["calls"] == 1 and len(a.msgs) == 1 and a.bare is False  # the plan was dropped, the task never started


def test_stop_can_hang_up_on_the_planning_request_because_it_knows_its_thread():
    a, seen = planner("1. Look\n", "2. Change\n")
    a.thread = 12345  # left over from an earlier turn, on a thread long gone
    a.plan("tidy the parser")
    assert seen["thread"] == [threading.get_ident()]


def test_a_stop_from_the_last_message_does_not_kill_this_ones_plan():
    a, seen = planner("1. Look\n", "2. Change\n")
    a.cancel = True
    assert a.plan("tidy the parser") == ["Look", "Change"]


def test_a_plan_is_a_short_list_so_it_is_asked_for_without_reasoning_and_with_a_small_budget():
    a, seen = planner("1. Look\n", "2. Change\n")
    a.think, a.limit = True, 0
    a.plan("tidy the parser")
    assert seen["think"] == [False] and 0 < seen["limit"][0] <= 512
    assert a.think is True  # your setting is back for the work itself
    assert "planning" in seen["status"] and seen["status"][-1] == ""  # and you are told what it is doing meanwhile


# ---------- ctrl+c always gets you out ----------

@pytest.fixture
def stuck():
    """a screen whose turn will not stop: its worker sits in something no stop reaches"""
    stops, hold = [], threading.Event()
    s = tui.Screen(Agent(chat=lambda msgs: iter(()), out=io.StringIO()))
    s.agent.stop = lambda: stops.append(1)
    s.worker = threading.Thread(target=hold.wait, daemon=True)
    s.worker.start()
    yield s, stops
    hold.set()


def test_ctrl_c_stops_the_turn_first_and_if_it_will_not_stop_offers_the_way_out(stuck):
    s, stops = stuck
    s.key("\x03")
    assert stops == [1] and not s.quit
    s.key("\x03")  # still running: the turn has been told once, so this is about leaving
    assert stops == [1] and not s.quit and "ctrl+c again to quit" in s.note_text()
    s.key("\x03")
    assert s.quit


def test_a_new_turn_gets_its_own_stop(stuck):
    s, stops = stuck
    s.key("\x03")
    hold = threading.Event()
    s.worker = threading.Thread(target=hold.wait, daemon=True)  # the next message's turn
    s.worker.start()
    s.key("\x03")
    hold.set()
    assert stops == [1, 1] and not s.quit


def test_escape_still_only_stops_and_never_quits(stuck):
    s, stops = stuck
    for _ in range(4):
        s.key("\x1b")
    assert not s.quit and stops
