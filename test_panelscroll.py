"""the lists in the side panels (your messages on the left, what the agent did on the right) scroll: the wheel moves
the list the mouse is over, and each says which of its entries are on show"""
import curses
import io

import pytest

import sprint
import sprint_tui as tui
from sprint import Agent
from sprint_tui import Screen, flat
from test_popups import Scr
from test_sprint import scripted

UP, DOWN = 64, 65  # the wheel, as a terminal reports it in full


@pytest.fixture(autouse=True)
def no_curses(monkeypatch):
    monkeypatch.setattr(tui.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(tui.curses, "color_pair", lambda n: n << 8)


def long(messages=20, calls=3, h=30, w=140):
    """a long conversation: this many messages, each with this many tool calls, drawn once on a screen with both panels"""
    s = Screen(Agent(chat=scripted(), out=io.StringIO(), mode="auto"))
    s.sidebar = s.history = True
    s.plan = False
    for m in range(1, messages + 1):
        s.items.append({"t": "user", "text": f"message {m}"})
        for c in range(1, calls + 1):
            s.show_call(sprint.Call("read", f"file{m}_{c}.py", "", 0, 0))
            s.show_result("ok")
        s.items.append({"t": "text", "text": f"answer {m}"})
    scr = Scr(h, w)
    s.draw(scr)
    return s, scr


def redraw(s, scr):
    s.draw(scr)
    return "\n".join(scr.text(y) for y in range(scr.h))


def over(s, where):
    """a column inside this part of the screen"""
    w, lw, side = s.geo
    return {"left": 5, "side": w - side + 5, "main": lw + 10}[where]


def listed(s, where):
    """what the panel's list shows now, top to bottom: the names in it"""
    rows = sorted((y, it) for (panel, y), it in s.hits.items() if panel == where)
    seen = []
    for _, it in rows:
        name = it["text"] if it["t"] == "user" else it["call"].arg
        seen += [name] if name not in seen else []
    return seen


def test_at_first_both_lists_show_their_newest_entries():
    s, scr = long()
    assert listed(s, "side")[-1] == "file20_3.py" and listed(s, "left")[-1] == "message 20"
    assert "file1_1.py" not in listed(s, "side") and "message 1" not in listed(s, "left")


def test_the_wheel_over_the_activity_list_scrolls_it_and_nothing_else():
    s, scr = long()
    before, was = listed(s, "side"), listed(s, "left")
    s.sgr_mouse(UP, over(s, "side"), 12, True)
    redraw(s, scr)
    now = listed(s, "side")
    assert len(now) == len(before) and now[-1] == before[-4] and now[3:] == before[:-3]  # three rows back
    assert s.scroll == 0 and listed(s, "left") == was
    s.sgr_mouse(DOWN, over(s, "side"), 12, True)
    redraw(s, scr)
    assert listed(s, "side") == before


def test_the_wheel_over_the_message_list_scrolls_that_one_message_at_a_time():
    s, scr = long()
    before, was = listed(s, "left"), listed(s, "side")
    s.sgr_mouse(UP, over(s, "left"), 8, True)
    redraw(s, scr)
    now = listed(s, "left")
    assert now[-1] == "message 19" and len(now) == len(before)
    assert s.scroll == 0 and listed(s, "side") == was


def test_the_wheel_over_the_conversation_still_scrolls_the_conversation():
    s, scr = long()
    sides = listed(s, "side"), listed(s, "left")
    s.sgr_mouse(UP, over(s, "main"), 8, True)
    redraw(s, scr)
    assert s.scroll == 3 and (listed(s, "side"), listed(s, "left")) == sides


def test_a_list_stops_at_its_oldest_entry_and_at_its_newest():
    s, scr = long()
    for _ in range(200):
        s.sgr_mouse(UP, over(s, "side"), 12, True)
        s.sgr_mouse(UP, over(s, "left"), 8, True)
    redraw(s, scr)
    assert listed(s, "side")[0] == "file1_1.py" and listed(s, "left")[0] == "message 1"
    fits = len(listed(s, "side"))
    s.sgr_mouse(DOWN, over(s, "side"), 12, True)  # one notch back down moves it at once: nothing was scrolled past the end
    redraw(s, scr)
    assert listed(s, "side")[0] == "file2_1.py" and len(listed(s, "side")) == fits
    for _ in range(200):
        s.sgr_mouse(DOWN, over(s, "side"), 12, True)
        s.sgr_mouse(DOWN, over(s, "left"), 8, True)
    redraw(s, scr)
    assert listed(s, "side")[-1] == "file20_3.py" and listed(s, "left")[-1] == "message 20"
    s.sgr_mouse(UP, over(s, "side"), 12, True)
    redraw(s, scr)
    assert listed(s, "side")[-1] == "file19_3.py"


def test_each_list_says_which_of_its_entries_are_on_show():
    s, scr = long()
    text = redraw(s, scr)
    fits, total = len(listed(s, "side")), 60
    assert f"{total - fits + 1}-{total} of {total}" in text
    shown = len(listed(s, "left"))
    assert f"{20 - shown + 1}-20 of 20" in text
    s.sgr_mouse(UP, over(s, "side"), 12, True)
    assert f"{total - fits - 2}-{total - 3} of {total}" in redraw(s, scr)


def test_a_list_that_fits_says_nothing_and_does_not_move():
    s, scr = long(messages=2, calls=1)
    text = redraw(s, scr)
    assert " of 2" not in text
    s.sgr_mouse(UP, over(s, "side"), 12, True)
    s.sgr_mouse(UP, over(s, "left"), 8, True)
    redraw(s, scr)
    assert listed(s, "side") == ["file1_1.py", "file2_1.py"] and listed(s, "left") == ["message 1", "message 2"]
    assert s.shift == {"left": 0, "side": 0}


def test_a_click_on_a_scrolled_list_goes_to_the_entry_under_the_mouse():
    s, scr = long()
    for _ in range(5):
        s.sgr_mouse(UP, over(s, "side"), 12, True)
    redraw(s, scr)
    y, item = min((y, it) for (panel, y), it in s.hits.items() if panel == "side")
    x = over(s, "side")
    s.sgr_mouse(0, x, y, True)
    s.sgr_mouse(0, x, y, False)
    assert s.jump is item and item["call"].arg == listed(s, "side")[0]


def test_sending_a_message_brings_both_lists_back_to_the_newest():
    s, scr = long()
    s.agent.chat = scripted("ok")
    for _ in range(4):
        s.sgr_mouse(UP, over(s, "side"), 12, True)
        s.sgr_mouse(UP, over(s, "left"), 8, True)
    for ch in "one more":
        s.key(ch)
    s.key("\r")
    s.worker.join(5)
    redraw(s, scr)
    assert s.shift == {"left": 0, "side": 0} and listed(s, "left")[-1] == "one more"


def test_the_older_mouse_report_scrolls_the_list_under_it_too(monkeypatch):
    monkeypatch.setattr(tui, "WHEEL_DOWN", 0x200000)
    s, scr = long()
    before = listed(s, "side")
    s.pointer(over(s, "side"), 12, curses.BUTTON4_PRESSED)
    redraw(s, scr)
    assert listed(s, "side")[-1] == before[-4] and s.scroll == 0
    s.pointer(over(s, "side"), 12, 0x200000)
    redraw(s, scr)
    assert listed(s, "side") == before


def test_with_a_plan_under_it_the_activity_list_scrolls_in_the_room_it_has():
    s, scr = long()
    s.plan = True
    s.items.append({"t": "user", "text": "last"})
    s.items.append({"t": "plan", "steps": ["one", "two", "three"], "at": 1})
    redraw(s, scr)
    fits = len(listed(s, "side"))
    assert 0 < fits < 20
    s.sgr_mouse(UP, over(s, "side"), 12, True)
    text = redraw(s, scr)
    assert len(listed(s, "side")) == fits and listed(s, "side")[-1] == "file19_3.py" and "Plan" in text
