"""the model's reasoning is on show while it is being written, not only once it is over"""
import io
import json
import os
import queue
import threading

import pytest

import sprint
import sprint_tui as tui
from sprint import Agent
from sprint_tui import Screen, flat
from test_popups import Scr
from test_sprint import fake_urlopen, scripted


def listening(*replies, think=True, **kw):
    """an agent that notes what it is told of the reasoning -> (agent, [("thinking" | "thought" | "said", text), ...])"""
    kw.setdefault("mode", "auto")
    a, seen = Agent(chat=scripted(*replies), out=io.StringIO(), think=think, **kw), []
    a.show_thinking = lambda text: seen.append(("thinking", text))
    a.show_thought = lambda text: seen.append(("thought", text))
    a.emit = lambda s, style="": seen.append(("said", s)) if s.strip() else None
    return a, seen


def screen(*replies, **kw):
    kw.setdefault("mode", "auto")
    return Screen(Agent(chat=scripted(*replies), out=io.StringIO(), **kw))


def paced(*chunks, **kw):
    """a screen whose model says one chunk each time step() is called; step returns once the agent has taken it in"""
    go, taken = queue.Queue(), threading.Semaphore(0)

    def chat(msgs):
        for c in chunks:
            go.get(timeout=5)
            yield c
            taken.release()  # asked for the next one: this one has been dealt with

    def step(wait=True):
        go.put(1)
        assert not wait or taken.acquire(timeout=5)

    kw.setdefault("mode", "auto")
    return Screen(Agent(chat=chat, out=io.StringIO(), think=True, **kw)), step


def finish(s):
    s.worker.join(10)
    assert not s.worker.is_alive()


def shown(s, w=70):
    return "\n".join(flat(spans) for _, spans, _ in s.lines(w))


# ---------- the agent: reasoning is passed on as it arrives ----------

def test_reasoning_is_passed_on_as_it_grows_and_once_whole_when_it_is_over():
    a, seen = listening(["<think>weigh", " the options", " carefully</think>", "Four."])
    assert a.run("2+2") == "Four."
    assert seen == [("thinking", "weigh"), ("thinking", "weigh the options"), ("thinking", "weigh the options carefully"),
                    ("thought", "weigh the options carefully"), ("said", "Four.")]


def test_reasoning_that_arrives_in_one_piece_with_the_answer_is_only_shown_whole():
    a, seen = listening("<think>weigh again</think>Five.")
    a.run("2+3")
    assert seen == [("thought", "weigh again"), ("said", "Five.")]


def test_nothing_is_passed_on_while_thinking_is_off():
    a, seen = listening(["<think>secret", " plan", "</think>", "Four."], think=False)
    a.run("2+2")
    assert seen == [("said", "Four.")]


def test_a_closing_tag_that_arrives_in_pieces_is_never_shown_as_reasoning():
    a, seen = listening(["<think>plan", "</th", "ink>", "Four."])
    a.run("2+2")
    assert [text for kind, text in seen if kind != "said"] == ["plan", "plan"] and seen[1] == ("thought", "plan")


def test_reasoning_that_ends_in_a_tool_call_is_closed_before_the_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("42\n")
    a, seen = listening(["<think>read it", " first</think>", {"call": {"name": "read", "arguments": {"path": "a.txt"}}}], "It says 42.")
    calls = []
    a.show_call = lambda c: calls.append(len(seen))
    a.run("what is in a.txt?")
    assert seen[:3] == [("thinking", "read it"), ("thinking", "read it first"), ("thought", "read it first")] and calls == [3]


def test_reasoning_cut_short_is_still_closed():
    a, seen = listening([])

    def chat(msgs):
        yield "<think>so far"
        a.cancel = True  # esc, while it is still reasoning
        yield " and more"  # arrives after the stop: never taken in

    a.chat = chat
    with pytest.raises(KeyboardInterrupt):
        a.turn()
    assert seen == [("thinking", "so far"), ("thought", "so far")]


def test_reasoning_a_server_sends_apart_from_the_reply_streams_too(monkeypatch):
    fake_urlopen(monkeypatch, "\n".join([
        json.dumps({"message": {"thinking": "let"}}),
        json.dumps({"message": {"thinking": " me see"}}),
        json.dumps({"message": {"content": "4"}}),
        json.dumps({"done": True, "done_reason": "stop"}),
    ]))
    a, seen = Agent(model="tiny", url="http://localhost:11434", out=io.StringIO(), think=True, mode="auto"), []
    a.show_thinking, a.show_thought = seen.append, lambda text: seen.append("over: " + text)
    assert a.run("2+2") == "4" and seen == ["let", "let me see", "over: let me see"]


def test_the_tally_of_reasoning_is_the_same_shown_or_not():
    def tally(think):
        a, _ = listening([{"prompt": 100}, "<think>" + "weigh it up " * 10, "weigh it up " * 10 + "</think>", "Four.",
                          {"out": 70, "secs": 1.0}], think=think)
        a.run("2+2")
        return {k: a.tally[k] for k in ("reasoning", "written", "sent", "replies")}

    assert tally(True) == tally(False) and 40 <= tally(True)["reasoning"] <= 70 and tally(True)["written"] == 70


# ---------- the plain prompt: the passing status line carries it ----------

class Terminal(io.StringIO):
    def isatty(self):
        return True


def test_without_a_terminal_the_plain_prompt_prints_what_it_always_did():
    a = Agent(chat=scripted(["<think>weigh", " the options</think>", "Four."]), out=io.StringIO(), mode="auto", think=True)
    a.run("2+2")
    assert a.out.getvalue() == "  ┊ weigh the options\nFour.\n"


def test_on_a_terminal_the_status_line_follows_the_reasoning(monkeypatch):
    monkeypatch.setattr(sprint.shutil, "get_terminal_size", lambda *a: os.terminal_size((60, 24)))
    words = [f"word{n} " for n in range(40)]
    a = Agent(chat=scripted(["<think>" + words[0]] + words[1:] + ["</think>", "Four."]), out=Terminal(), mode="auto", think=True)
    a.run("2+2")
    notes = [n for n in a.out.getvalue().split("\r\033[K") if "⋯ thinking ·" in n]
    assert len(notes) == 40 and "1 word " in notes[0] and "word0" in notes[0] and "40 words" in notes[-1]
    assert notes[-1].split("\033[0m")[0].endswith("word38 word39") and "word0 " not in notes[-1]  # the newest words, not the first
    assert all(len(n.split("\033[0m")[0]) - len("\033[2m") < 60 for n in notes)  # never so long that the line wraps
    assert "word0 word1" in notes[1] and "┊ word0 word1" in a.out.getvalue() and "Four.\n" in a.out.getvalue()  # then as before


# ---------- the screen: one item that grows ----------

def test_a_thought_is_one_item_from_its_first_words_until_it_is_over():
    s = screen()
    s.show_thinking("First I")
    it = s.items[-1]
    assert it["call"].tool == "think" and it["state"] == "running" and it["result"] == "First I"
    s.show_thinking("First I should read")
    assert s.items == [it] and it["result"] == "First I should read" and it["state"] == "running"
    s.show_thought("First I should read the file.")
    assert s.items == [it] and it["result"] == "First I should read the file." and it["state"] == "done"
    s.show_thinking("Another")  # the next reply's reasoning is a thought of its own
    assert len(s.items) == 2 and s.items[-1]["state"] == "running" and it["state"] == "done"


def test_a_thought_that_was_never_seen_arriving_is_still_one_finished_item():
    s = screen()
    s.show_call(sprint.Call("shell", "", "ls", 0, 0))
    s.show_result("a.txt")
    s.show_thought("Check the tests first.")
    assert [i["call"].tool for i in s.items] == ["shell", "think"] and s.items[-1]["state"] == "done"
    assert s.items[0]["result"] == "a.txt"


def test_what_comes_down_is_counted_once_however_often_the_thought_is_shown():
    s = screen()
    s.show_thinking("y" * 1000)
    assert s.got == 1000 and s.down
    s.show_thinking("y" * 4000)
    s.show_thinking("y" * 7000)
    assert s.got == 7000
    s.show_thought("y" * 7000)
    assert s.got == 7000 and "↓ 2.0k tokens" in flat(s.status_row())
    s.emit("x" * 35)
    assert s.got == 7035


def test_the_status_line_says_thinking_while_the_reasoning_comes_down():
    s = screen()
    s.agent.think = True
    s.show_thinking("y" * 700)
    assert " thinking (" in flat(s.status_row()) and "↓ 200 tokens" in flat(s.status_row())
    s.show_thought("y" * 700)
    s.emit("Four.")
    assert " answering (" in flat(s.status_row())


def test_folded_it_says_it_is_thinking_with_a_count_and_the_newest_words_that_move():
    s = screen()
    s.show_thinking("First I should")
    (_, spans, item), = s.window(s.items[-1], 70)
    assert flat(spans).rstrip() == "  ┄ thinking  3 words  First I should" and len(flat(spans)) == 70 and item is s.items[-1]
    assert spans[:3] == [("  ┄ ", "frame"), ("thinking", "dim"), ("  3 words ", "dim")] and spans[3] == (" First I should", "thought")
    s.show_thinking("First I should read the file.\nThen change the word.")
    (_, spans, _), = s.window(s.items[-1], 80)
    assert flat(spans).rstrip() == "  ┄ thinking  10 words  First I should read the file. Then change the word."
    s.show_thought("First I should read the file.\nThen change the word.")
    (_, spans, _), = s.window(s.items[-1], 70)
    assert flat(spans).rstrip() == "  ┄ thought  10 words"  # over: the line it always settled into


def test_a_long_thought_folded_shows_its_last_words_on_one_line_of_the_right_width():
    s = screen()
    s.show_thinking(" ".join(f"word{n}" for n in range(200)))
    for w in (20, 40, 70, 120):
        (_, spans, _), = s.window(s.items[-1], w)
        assert len(flat(spans)) == w and flat(spans).startswith("  ┄ thinking  200 w")
    line = flat(s.window(s.items[-1], 70)[0][1])
    assert line.rstrip().endswith("word198 word199") and "word0 " not in line and "… word" in line
    assert all(piece.startswith("word") for piece in line.split("… ")[1].split())  # cut between words, not through one


def test_with_showthink_on_the_frame_holds_the_reasoning_as_it_grows():
    s = screen()
    s.showthink = True
    s.show_thinking("First I should")
    text = [flat(spans) for _, spans, _ in s.window(s.items[-1], 50)]
    assert text[0].startswith("┌┄ thinking  3 words ┄") and text[1].startswith("┆ First I should ") and text[2].startswith("└┄")
    assert all(len(t) == 50 for t in text)
    s.show_thinking("First I should read the file.\nThen change the word.")
    text = [flat(spans) for _, spans, _ in s.window(s.items[-1], 50)]
    assert len(text) == 4 and text[0].startswith("┌┄ thinking  10 words ┄") and text[2].startswith("┆ Then change the word. ")
    s.show_thought("First I should read the file.\nThen change the word.")
    text = [flat(spans) for _, spans, _ in s.window(s.items[-1], 50)]
    assert len(text) == 4 and text[0].startswith("┌┄ thought  10 words ┄") and text[2].startswith("┆ Then change the word. ")


def test_a_thought_opened_by_hand_grows_in_its_frame_too():
    s = screen()
    s.show_thinking("Check the")
    s.items[-1]["open"] = True
    s.show_thinking("Check the tests first.")
    text = [flat(spans) for _, spans, _ in s.window(s.items[-1], 50)]
    assert text[0].startswith("┌┄ thinking  4 words ┄") and text[1].startswith("┆ Check the tests first. ")


def test_the_redraw_cache_notices_a_thought_growing_and_ending():
    for showthink in (False, True):
        s = screen()
        s.showthink = showthink
        s.show_thinking("alpha")
        first = shown(s)
        s.show_thinking("alpha bravo")
        second = shown(s)
        assert "alpha" in first and "bravo" not in first and "bravo" in second and "2 words" in second
        s.show_thinking("alpha bravo charlie")  # as many words as characters differ: the count alone would not tell them apart
        s.show_thinking("alpha bravo charlie delta")
        assert "delta" in shown(s) and "thinking" in shown(s)
        s.show_thought("alpha bravo charlie delta")
        assert "thought  4 words" in shown(s) and "thinking" not in shown(s)


def test_the_view_follows_the_newest_lines_of_a_long_thought(monkeypatch):
    monkeypatch.setattr(tui.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(tui.curses, "color_pair", lambda n: n << 8)
    scr, s = Scr(), screen()
    s.showthink = True
    s.items.append({"t": "user", "text": "go"})
    for n in range(1, 81):
        s.show_thinking("\n".join(f"step {i} of the plan" for i in range(n)))
        if n in (1, 40, 80):
            s.draw(scr)
            assert scr.find(f"step {n - 1} of the plan")  # the line it has just written is on the screen
    assert not any("step 0 of the plan" in scr.text(y) for y in range(scr.h))  # the first lines have scrolled away


# ---------- through a whole turn ----------

def test_a_turn_shows_the_thought_growing_then_settles_it_before_the_answer():
    s, step = paced("<think>weigh it up", "\nthen decide", "</think>", "Four.")
    s.submit("2+2")
    step()
    thought = s.items[-1]
    assert thought["call"].tool == "think" and thought["state"] == "running" and thought["result"] == "weigh it up"
    assert "thinking  3 words  weigh it up" in shown(s) and s.got == len("weigh it up")
    step()
    assert s.items[-1] is thought and "thinking  5 words  weigh it up then decide" in shown(s)
    step(), step()
    finish(s)
    assert [i["t"] for i in s.items][-2:] == ["tool", "text"] and s.items[-2] is thought
    assert thought["state"] == "done" and thought["result"] == "weigh it up\nthen decide"
    assert "thought  5 words" in shown(s) and "thinking" not in shown(s)
    assert s.got == len("weigh it up\nthen decide") + len("Four.") + 1  # each character that came down, once (and the line's end)
    assert sum(i["t"] == "tool" for i in s.items) == 1


def test_a_turn_stopped_while_it_reasons_does_not_leave_the_thought_thinking_for_ever():
    s, step = paced("<think>weigh it up", " then", " decide</think>Four.")
    s.submit("2+2")
    step()
    thought = s.items[-1]
    assert thought["state"] == "running"
    s.interrupt()
    step(wait=False)  # the model's next words arrive, and find the turn stopped
    finish(s)
    assert thought["state"] == "done" and "thought  " in shown(s) and "thinking" not in shown(s)
    assert s.items[-1]["text"] == "interrupted" and sum(i["t"] == "tool" for i in s.items) == 1
