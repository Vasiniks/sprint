"""a turn can always be stopped, and sprint can always be left. (Plan mode's first request, the one that asked the
model for its steps, was once a blind spot no stop reached: while it ran, every ctrl+c tried to stop it again and
there was no way out. That request is gone now that the model keeps its own to-do list; the way out stays)"""
import io
import threading
import time

import pytest

import sprint
import sprint_tui as tui
from sprint import Agent
from sprint_tui import flat


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
