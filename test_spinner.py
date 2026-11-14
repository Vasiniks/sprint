"""what is live in the conversation turns a small wheel of dots: the thought being written, the call being run"""
import io

import sprint
import sprint_tui as tui
from sprint import Agent
from sprint_tui import SPIN, Screen, flat
from test_popups import Scr, scr  # noqa: F401  (scr is a fixture)
from test_sprint import scripted


def screen(*replies, **kw):
    kw.setdefault("mode", "auto")
    return Screen(Agent(chat=scripted(*replies), out=io.StringIO(), **kw))


def shown(s, w=70):
    return "\n".join(flat(spans) for _, spans, _ in s.lines(w))


def spins(rows):
    return [span for _, spans, _ in rows for span in spans if span[1] == "spin"]


def test_the_wheel_is_six_dot_cells_that_go_round_and_come_back():
    assert len(set(SPIN)) == len(SPIN) >= 6 and all(0x2800 < ord(ch) < 0x2840 for ch in SPIN)  # braille, six dots a cell
    assert tui.spin(0) == SPIN[0] and tui.spin(tui.SPIN_BEAT * 1.5) == SPIN[1]
    assert tui.spin(tui.SPIN_BEAT * (len(SPIN) + 0.5)) == SPIN[0]


def test_a_thought_being_written_turns_the_wheel_where_its_mark_was():
    s = screen()
    s.show_thinking("First I should")
    (_, spans, _), = s.window(s.items[-1], 70)
    assert spans[:2] == [("  ", "frame"), (SPIN[0], "spin")] and flat(spans).startswith(f"  {SPIN[0]} thinking  4 tokens")
    assert len(flat(spans)) == 70
    s.show_thought("First I should")
    rows = s.window(s.items[-1], 70)
    assert not spins(rows) and flat(rows[0][1]).startswith("  ┄ thought  4 tokens")


def test_a_thought_shown_in_full_turns_it_in_its_frame():
    s = screen()
    s.showthink = True
    s.show_thinking("First I should")
    rows = s.window(s.items[-1], 50)
    assert flat(rows[0][1]).startswith(f"┌┄ {SPIN[0]} thinking  4 tokens · 3 words ┄") and len(spins(rows)) == 1
    assert all(len(flat(spans)) == 50 for _, spans, _ in rows)
    s.show_thought("First I should")
    rows = s.window(s.items[-1], 50)
    assert flat(rows[0][1]).startswith("┌┄ thought  4 tokens · 3 words ┄") and not spins(rows)


def test_a_call_that_is_running_turns_it_by_its_state_and_stops_with_the_result():
    s = screen()
    s.show_call(sprint.Call("shell", "", "sleep 9", 0, 0))
    rows = s.window(s.items[-1], 70)
    assert len(spins(rows)) == 1 and f"{SPIN[0]} running " in flat(rows[0][1]) and all(len(flat(r)) == 70 for _, r, _ in rows)
    s.show_result("done")
    assert not spins(s.window(s.items[-1], 70))


def test_a_call_waiting_for_your_word_does_not_turn_it():
    s = screen()
    s.show_call(sprint.Call("shell", "", "rm a.txt", 0, 0))
    s.items[-1]["state"] = "waiting"
    assert not spins(s.window(s.items[-1], 70))


def test_with_motion_off_nothing_turns():
    s = screen()
    s.motion = False
    s.show_thinking("First I should")
    rows = s.window(s.items[-1], 70)
    assert not spins(rows) and flat(rows[0][1]).rstrip() == "  ┄ thinking  4 tokens · 3 words"
    s.show_call(sprint.Call("shell", "", "sleep 9", 0, 0))
    assert not spins(s.window(s.items[-1], 70))


def test_switching_motion_off_is_seen_at_once_by_what_is_already_drawn():
    s = screen()
    s.show_thinking("First I should")
    assert SPIN[0] in shown(s)
    s.motion = False
    assert SPIN[0] not in shown(s) and "┄ thinking" in shown(s)


def test_the_wheel_is_drawn_at_the_cell_the_clock_has_reached_in_the_colour_of_the_mode(scr, monkeypatch):  # noqa: F811
    s, now = screen(), [1000.0]
    monkeypatch.setattr(tui.time, "time", lambda: now[0])
    s.put(scr, 0, 0, [("  ", "frame"), (SPIN[0], "spin"), (" thinking", "dim")])
    first = scr.cells[(0, 2)]
    assert first[0] == tui.spin(1000.0) and first[1] == s.attr(s.agent.mode) and scr.text(0).startswith(f"  {first[0]} thinking")
    now[0] += tui.SPIN_BEAT
    s.put(scr, 0, 0, [("  ", "frame"), (SPIN[0], "spin"), (" thinking", "dim")])
    assert scr.cells[(0, 2)][0] == tui.spin(1000.0 + tui.SPIN_BEAT) != first[0]


def test_a_whole_frame_draws_the_wheel_beside_the_thought(scr, monkeypatch):  # noqa: F811
    s = screen()
    s.items.append({"t": "user", "text": "go"})
    s.show_thinking("First I should")
    s.draw(scr)
    y = scr.find("thinking  4 tokens")
    assert scr.text(y).split(" thinking")[0][-1] in SPIN and "First I" not in scr.text(y)
