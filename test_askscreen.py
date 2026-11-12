"""the model's question on the full screen: it is answered in the prompt bar, waits a minute (any key starts the wait
again), and what you were typing when it came is put by and given back"""
import curses
import io
import threading
import time

import pytest

import sprint
import sprint_tui as tui
from sprint import Agent
from sprint_tui import Screen, flat
from test_popups import Scr
from test_sprint import scripted

ASK = "```ask\nWhich colour should the button be?\n```"
CHOICE = "```ask\nWhich colour?\n- red\n- blue\n- green\n```"


def screen(*replies, **kw):
    kw.setdefault("mode", "auto")
    return Screen(Agent(chat=scripted(*replies), out=io.StringIO(), **kw))


def typed(s, text):
    for ch in text:
        s.key(ch)


def until(test, secs=5.0):
    """wait for something another thread does"""
    end = time.time() + secs
    while not test():
        assert time.time() < end, "it never happened"
        time.sleep(0.01)


def put(s, block=ASK):
    """the model puts this question: on its own thread, as the agent does -> (the thread, where the answer lands)"""
    call = sprint.parse(block)
    s.show_call(call)
    got = []
    t = threading.Thread(target=lambda: got.append(s.question(*sprint.question_parts(call.body))), daemon=True)
    t.start()
    until(lambda: s.asking)
    return t, got


def drawn(s, h=30, w=110):
    scr = Scr(h, w)
    s.draw(scr)
    return [scr.text(y) for y in range(h)]


@pytest.fixture(autouse=True)
def no_curses(monkeypatch):
    monkeypatch.setattr(tui.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(tui.curses, "color_pair", lambda n: n << 8)


# ---------- answering ----------

def test_the_screen_takes_over_the_agents_questions():
    s = screen()
    assert s.agent.question == s.question


def test_what_you_type_and_send_is_the_answer():
    s = screen()
    t, got = put(s)
    assert s.items[-1]["state"] == "asking"
    typed(s, "navy blue")
    s.key("\r")
    t.join(2)
    assert got == ["navy blue"] and s.asking is None and s.buf == ""
    assert s.past == []  # an answer is not a message to recall later


def test_enter_with_nothing_typed_and_no_choices_sends_nothing():
    s = screen()
    t, got = put(s)
    s.key("\r")
    time.sleep(0.15)
    assert got == [] and s.asking
    s.interrupt()
    t.join(2)


def test_the_choices_are_on_offer_and_enter_takes_the_highlighted_one():
    s = screen()
    t, got = put(s, CHOICE)
    assert [name for name, _ in s.options()] == ["red", "blue", "green"]
    s.key(curses.KEY_DOWN)
    s.key(curses.KEY_DOWN)
    s.key(curses.KEY_DOWN)
    s.key(curses.KEY_DOWN)  # round to the top, and one down
    s.key("\r")
    t.join(2)
    assert got == ["blue"]


def test_your_own_words_beat_the_choices_and_tab_puts_one_in_the_bar_to_change():
    s = screen()
    t, got = put(s, CHOICE)
    s.key(curses.KEY_DOWN)
    s.key("\t")
    assert s.buf.strip() == "blue" and s.agent.mode == "auto"  # (tab did not change the mode)
    typed(s, "ish")
    assert s.options() == []  # typing your own: the list steps aside
    s.key("\r")
    t.join(2)
    assert got == ["blue ish"] or got == ["blueish"]


def test_escape_leaves_it_to_the_model_without_stopping_the_turn():
    s = screen()
    t, got = put(s)
    s.key("\x1b")
    t.join(2)
    assert got == [""] and s.agent.cancel is False


def test_ctrl_c_clears_what_is_typed_and_then_stops_the_turn():
    s = screen()
    s.worker = threading.current_thread()  # a turn is running
    t, got = put(s)
    typed(s, "nav")
    s.key("\x03")
    assert s.buf == "" and s.asking and got == []
    s.key("\x03")
    t.join(2)
    assert got == [None] and s.agent.cancel is True and s.asking is None


def test_a_question_is_not_an_approval():
    s = screen()
    t, got = put(s)
    typed(s, "yes")  # y, a and n are letters here, not answers
    assert s.buf == "yes" and got == [] and s.pending is None
    s.key("\r")
    t.join(2)
    assert got == ["yes"]


def test_slash_and_bang_are_only_text_in_an_answer():
    s = screen()
    t, got = put(s)
    typed(s, "/model")
    assert s.options() == []  # no command completions over an answer
    rows, _ = s.input_rows(80)
    assert all(role != "kw" for r in rows for _, role in r)
    s.key("\r")
    t.join(2)
    assert got == ["/model"] and s.popup is None  # sent as the answer, not run


# ---------- the wait ----------

def test_a_question_nobody_answers_gives_up_after_the_wait():
    s = screen()
    s.agent.question_timeout = 0.3
    call = sprint.parse(ASK)
    s.show_call(call)
    began = time.time()
    assert s.question("Which colour should the button be?") is None and 0.25 <= time.time() - began < 2
    assert s.asking is None and s.deadline is None


def test_any_key_starts_the_wait_again():
    s = screen()
    s.agent.question_timeout = 0.6
    t, got = put(s)
    for ch in "teal":  # 1.2 seconds of slow typing
        time.sleep(0.3)
        assert s.asking
        s.key(ch)
    s.key("\r")
    t.join(2)
    assert got == ["teal"]


def test_with_the_wait_switched_off_a_question_waits_for_ever():
    s = screen()
    s.agent.question_timeout = 0
    t, got = put(s)
    assert s.deadline is None
    s.key("x")
    assert s.deadline is None  # a key does not start one either
    time.sleep(0.2)
    assert got == []
    s.interrupt()
    t.join(2)


def test_the_wait_is_the_questions_own_setting_not_the_approvals():
    s = screen()
    s.agent.ask_timeout, s.agent.question_timeout = 5, 60
    t, got = put(s)
    assert 55 < s.deadline - time.time() <= 60
    s.key("x")
    assert 55 < s.deadline - time.time() <= 60
    s.interrupt()
    t.join(2)


# ---------- what you were typing ----------

def test_what_you_were_typing_is_put_by_and_comes_back():
    s = screen()
    typed(s, "then add tests")
    t, got = put(s)
    s.key("b")  # the first key meant for the answer
    assert s.buf == "b"
    typed(s, "lue")
    s.key("\r")
    t.join(2)
    s.advance()
    assert got == ["blue"] and s.buf == "then add tests" and s.cur == len(s.buf)


def test_it_comes_back_when_nobody_answers_too():
    s = screen()
    typed(s, "then add tests")
    s.agent.question_timeout = 0.2
    t, got = put(s)
    s.draw(Scr(30, 110))  # the screen has seen the question: the bar is the answer's now
    assert s.buf == ""
    t.join(2)
    s.advance()
    assert got == [None] and s.buf == "then add tests"


def test_an_answer_half_typed_when_the_wait_ran_out_is_not_thrown_away():
    s = screen()
    s.agent.question_timeout = 0.3
    t, got = put(s)
    typed(s, "probably teal")
    t.join(2)
    s.advance()
    assert got == [None] and s.buf == "probably teal"  # there to send as a message if you still mean it


def test_a_message_sent_while_it_works_still_queues_once_the_question_is_over():
    s = screen("ok")
    s.worker = threading.Thread(target=time.sleep, args=(0.4,))
    s.worker.start()
    t, got = put(s)
    typed(s, "red")
    s.key("\r")
    t.join(2)
    assert got == ["red"] and s.queue == []  # the answer went to the model, not into the queue
    typed(s, "and make it bigger")
    s.key("\r")
    assert s.queue == ["and make it bigger"]
    s.worker.join(2)
    s.queue.clear()


# ---------- how it looks ----------

def test_the_bar_says_it_is_an_answer_it_wants_and_how_long_it_waits():
    s = screen()
    s.items.append({"t": "user", "text": "style the button"})
    t, got = put(s)
    rows, _ = s.input_rows(80)
    assert "answer" in flat(rows[1]).lower()  # the placeholder
    text = "\n".join(drawn(s))
    assert "waits 60s" in text or "waits 59s" in text
    assert "esc" in text and "model decides" in text
    typed(s, "teal")
    rows, _ = s.input_rows(80)
    assert "teal" in flat(rows[1])
    s.interrupt()
    t.join(2)


def test_the_question_stands_in_the_conversation_in_full_with_its_choices():
    s = screen()
    s.items.append({"t": "user", "text": "style the button"})
    s.peek = 1
    t, got = put(s, "```ask\nWhich colour should the button be?\nIt shows on every page.\n- red\n- blue\n```")
    text = "\n".join(drawn(s))
    assert "Which colour should the button be?" in text and "It shows on every page." in text  # never folded away
    assert "red" in text and "blue" in text and "your answer?" in text
    s.key(curses.KEY_DOWN)
    s.key("\r")
    t.join(2)
    s.show_result(sprint.ANSWERED + "blue")
    text = "\n".join(drawn(s))
    assert s.items[-1]["state"] == "done" and "→ blue" in text and "answered" in text
    assert "The user answered" not in text and "waits" not in text


def test_the_choices_are_listed_over_the_bar_with_the_highlighted_one_marked():
    s = screen()
    s.items.append({"t": "user", "text": "style the button"})
    s.sidebar = s.history = False  # (so that a row holds nothing but the choice)
    t, got = put(s, CHOICE)
    scr = Scr(30, 110)
    s.draw(scr)
    rows = [y for y in range(30) if scr.text(y).strip() in ("red", "blue", "green")]
    assert len(rows) == 3 and rows == list(range(rows[0], rows[0] + 3))
    assert any(y == rows[0] and attr & curses.A_REVERSE for y, x, n, attr in scr.marks)
    typed(s, "t")
    scr = Scr(30, 110)
    s.draw(scr)
    assert not [y for y in range(30) if scr.text(y).strip() in ("red", "blue", "green")]
    s.interrupt()
    t.join(2)


def test_what_became_of_a_question_shows_on_its_window_and_in_the_activity_list():
    s = screen()
    call = sprint.parse(ASK)
    for result, state in ((sprint.ANSWERED + "blue", "done"),
                          ("no answer: the user did not reply within 60 seconds. Decide yourself.", "unanswered"),
                          ("The user leaves this to you. Decide yourself.", "skipped")):
        s.show_call(call)
        s.show_result(result)
        assert s.items[-1]["state"] == state
        assert "took" not in s.items[-1] and "secs" not in s.items[-1]  # the wait for you is no time of the tool's
    lines = [flat(spans) for _, spans in s.activity()]
    assert all("ask Which colour should the button be?" in line for line in lines)
    assert lines[0].startswith("✓") and not lines[1].startswith("✓") and not lines[2].startswith("✓")
    said = "\n".join(flat(spans) for _, spans, _ in s.lines(90))
    assert "nobody answered" in said and "left to the model" in said and "failed" not in said


def test_the_status_line_says_it_is_waiting_for_your_answer():
    s = screen()
    t, got = put(s)
    assert "your answer" in s.verb()
    s.interrupt()
    t.join(2)


def test_the_settings_popup_has_both_question_settings():
    s = screen()
    s.submit("/config")
    keys = [r["v"] for r in s.popup["rows"] if r.get("v")]
    assert "questions" in keys and "question_timeout" in keys
    s.config_pick("questions", True, keys.index("questions"))
    assert s.agent.questions is False and sprint.load_config()["questions"] is False


# ---------- the whole way through ----------

def finish(s):
    s.worker.join(10)
    assert not s.worker.is_alive()


def test_a_question_asked_and_answered_on_the_screen_reaches_the_model():
    s = screen(ASK, "Blue it is.")
    s.submit("style the button")
    until(lambda: s.asking)
    typed(s, "blue")
    s.key("\r")
    finish(s)
    sent = "\n".join(str(m["content"]) for m in s.agent.chat.seen[-1])
    assert "The user answered: blue" in sent
    assert [i["t"] for i in s.items] == ["user", "tool", "text"] and s.items[1]["state"] == "done"
    assert "Blue it is." in s.items[-1]["text"]


def test_it_works_while_the_model_reasons_first():
    s = screen(["<think>I cannot know the colour.</think>", ASK], ["<think>Blue then.</think>", "Blue it is."], think=True)
    s.submit("style the button")
    until(lambda: s.asking)
    assert [i["call"].tool for i in s.items if i["t"] == "tool"] == ["think", "ask"]
    typed(s, "blue")
    s.key("\r")
    finish(s)
    assert [i["call"].tool for i in s.items if i["t"] == "tool"] == ["think", "ask", "think"]
    assert "Blue it is." in s.items[-1]["text"]


def test_it_works_in_plan_mode_and_the_plan_stays_on_show():
    s = screen("1. Pick a colour\n2. Say it\n", ASK, "Picked.", "Blue it is.")
    s.plan = True
    s.submit("style the button")
    until(lambda: s.asking)
    assert s.plan_now() and s.plan_now()["at"] == 0  # the question does not hide the plan
    typed(s, "blue")
    s.key("\r")
    finish(s)
    assert s.plan_now()["at"] == 2 and "Blue it is." in s.items[-1]["text"]
    assert "The user answered: blue" in "\n".join(str(m["content"]) for m in s.agent.chat.seen[-1])


def test_stopping_the_turn_while_it_asks_ends_the_turn():
    s = screen(ASK, "never said")
    s.submit("style the button")
    until(lambda: s.asking)
    s.key("\x03")
    finish(s)
    assert s.asking is None and s.items[-1]["text"] == "interrupted" and len(s.agent.chat.seen) == 1


def test_nobody_answering_on_the_screen_lets_the_model_go_on():
    s = screen(ASK, "I chose grey.")
    s.agent.question_timeout = 0.2
    s.submit("style the button")
    finish(s)
    assert "I chose grey." in s.items[-1]["text"] and s.items[1]["state"] == "unanswered"
    assert "no answer" in "\n".join(str(m["content"]) for m in s.agent.chat.seen[-1])
