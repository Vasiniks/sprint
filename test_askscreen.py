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


def test_the_first_choice_is_highlighted_and_enter_takes_the_highlighted_one():
    s = screen()
    t, got = put(s, CHOICE)
    assert s.asking["at"] == 0 and s.options() == []  # the choices are the question's own list, not completions
    for _ in range(5):  # red, blue, green, then the row for your own answer: round to the top, and one down
        s.key(curses.KEY_DOWN)
    assert s.asking["at"] == 1
    s.key("\r")
    t.join(2)
    assert got == ["blue"]


def test_up_from_the_top_goes_round_to_the_row_for_your_own_answer():
    s = screen()
    t, got = put(s, CHOICE)
    s.key(curses.KEY_UP)
    assert s.asking["at"] == 3
    s.key("\r")  # nothing written there: enter sends nothing
    time.sleep(0.1)
    assert got == [] and s.asking
    s.key(curses.KEY_UP)
    s.key("\r")
    t.join(2)
    assert got == ["green"]


def test_a_choices_number_takes_it_at_once():
    s = screen()
    t, got = put(s, CHOICE)
    s.key("3")
    t.join(2)
    assert got == ["green"] and s.buf == ""


def test_the_number_after_the_last_choice_goes_to_the_row_for_your_own_answer():
    s = screen()
    t, got = put(s, CHOICE)
    s.key("4")
    assert s.asking["at"] == 3 and s.buf == "" and got == []
    typed(s, "2 shades of teal")  # a number is only text there
    s.key("\r")
    t.join(2)
    assert got == ["2 shades of teal"]


def test_a_number_that_is_no_choice_begins_an_answer_of_your_own():
    s = screen()
    t, got = put(s, CHOICE)
    typed(s, "7 of them")
    assert s.buf == "7 of them" and got == []
    s.key("\r")
    t.join(2)
    assert got == ["7 of them"]


def test_with_no_choices_numbers_are_only_text():
    s = screen()
    t, got = put(s)
    typed(s, "12")
    s.key("\r")
    t.join(2)
    assert got == ["12"]


def test_typing_goes_to_the_row_for_your_own_answer_and_up_goes_back_to_the_choices():
    s = screen()
    t, got = put(s, CHOICE)
    typed(s, "teal")
    s.asked()
    assert s.asking["at"] == 3 and s.buf == "teal"
    s.key(curses.KEY_UP)
    assert s.asking["at"] == 2 and s.buf == "teal"  # what you wrote stays on its row
    s.key("\r")
    t.join(2)
    assert got == ["green"]  # the highlighted row is the answer


def test_what_is_pasted_is_an_answer_of_your_own():
    s = screen()
    t, got = put(s, CHOICE)
    s.paste("a soft teal")
    s.key("\r")
    t.join(2)
    assert got == ["a soft teal"]


def test_your_own_words_beat_the_choices_and_tab_puts_one_on_your_row_to_change():
    s = screen()
    t, got = put(s, CHOICE)
    s.key(curses.KEY_DOWN)
    s.key("\t")
    assert s.buf.strip() == "blue" and s.agent.mode == "auto"  # (tab did not change the mode)
    typed(s, "ish")
    s.key("\r")
    t.join(2)
    assert got == ["blueish"]


def test_a_click_on_a_choice_takes_it_and_one_on_your_own_row_goes_there():
    s = screen()
    s.items.append({"t": "user", "text": "style the button"})
    t, got = put(s, CHOICE)
    scr = Scr(30, 110)
    s.draw(scr)
    x = scr.text(scr.find("2. blue")).index("blue")
    s.click(x, scr.find("Something else"))
    assert s.asking["at"] == 3 and got == []
    s.click(x, scr.find("2. blue"))
    t.join(2)
    assert got == ["blue"]


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

def panel(s, bw=80):
    """the bar while a question waits, as text: its rows between the two rules"""
    rows, _ = s.input_rows(bw)
    assert set(flat(rows[0])[:20]) == {"─"} and set(flat(rows[-1])) == {"─"}
    return [flat(r).rstrip() for r in rows[1:-1]]


def test_the_question_takes_the_bars_place_with_a_row_for_your_answer_under_it():
    s = screen()
    s.items.append({"t": "user", "text": "style the button"})
    t, got = put(s)
    assert panel(s) == ["? Which colour should the button be?", "", tui.MARK + " Your answer…"]
    assert s.caret == (3, 2)  # the cursor: on the answer's row, after the mark
    text = "\n".join(drawn(s))
    assert "waits 60s" in text or "waits 59s" in text
    assert "esc" in text and "model decides" in text
    typed(s, "teal")
    assert panel(s)[-1] == tui.MARK + " teal" and s.caret == (3, 6)
    s.interrupt()
    t.join(2)


def test_the_choices_are_numbered_under_the_question_with_the_highlighted_one_marked():
    s = screen()
    t, got = put(s, CHOICE)
    m = tui.MARK
    assert panel(s) == ["? Which colour?", "", m + " 1. red", "  2. blue", "  3. green", "  4. Something else…"]
    s.key(curses.KEY_DOWN)
    assert panel(s)[2:] == ["  1. red", m + " 2. blue", "  3. green", "  4. Something else…"]
    rows, _ = s.input_rows(80)
    assert ("blue", "bold") in rows[4] and ("red", "") in rows[3]  # the highlighted one stands out
    typed(s, "teal")
    assert panel(s)[2:] == ["  1. red", "  2. blue", "  3. green", m + " 4. teal"]
    assert s.caret == (6, 9)  # after "❯ 4. teal"
    s.interrupt()
    t.join(2)


def lit(s, h=30, w=110):
    """draw, and say which rows of the question carry a band of reverse video -> (the screen, those rows' text)"""
    scr = Scr(h, w)
    s.draw(scr)
    top = next(y for y in range(h) if scr.text(y).strip(" │").startswith("? "))  # the question's first row, in the bar
    return scr, [scr.text(y).strip(" │") for y, x, n, attr in scr.marks if attr & curses.A_REVERSE and y > top]


def test_the_highlighted_choice_stands_on_a_band_across_the_bar():
    s = screen()
    s.items.append({"t": "user", "text": "style the button"})
    s.sidebar = s.history = False
    t, got = put(s, CHOICE)
    scr, rows = lit(s)
    assert rows == [tui.MARK + " 1. red"]
    y, x, n, attr = next(m for m in scr.marks if m[3] & curses.A_REVERSE and "1. red" in scr.text(m[0]))
    assert x == scr.text(y).index(tui.MARK) and n == len(scr.text(scr.find("───")).strip())  # as wide as the bar
    s.key(curses.KEY_DOWN)
    assert lit(s)[1] == [tui.MARK + " 2. blue"]  # it moves with the highlight
    s.interrupt()
    t.join(2)


def test_a_highlighted_choice_of_several_rows_is_banded_on_all_of_them():
    long = "Create a new module for the helpers and move every shared function into it, and the tests with them"
    s = screen()
    s.items.append({"t": "user", "text": "tidy up"})
    t, got = put(s, f"```ask\nWhere should the helpers go?\n- utils.py\n- {long}\n```")
    s.key(curses.KEY_DOWN)
    rows = lit(s)[1]
    assert len(rows) >= 2 and " ".join(rows) == tui.MARK + " 2. " + long
    s.interrupt()
    t.join(2)


def test_the_row_for_your_own_answer_is_banded_until_you_write_on_it():
    s = screen()
    s.items.append({"t": "user", "text": "style the button"})
    t, got = put(s, CHOICE)
    s.key(curses.KEY_UP)
    assert lit(s)[1] == [tui.MARK + " 4. Something else…"]
    typed(s, "teal")
    assert lit(s)[1] == []  # what you type is plain text, with the cursor in it
    s.key(curses.KEY_UP)
    assert lit(s)[1] == [tui.MARK + " 3. green"]
    s.interrupt()
    t.join(2)


def test_a_question_with_no_choices_has_nothing_to_band():
    s = screen()
    s.items.append({"t": "user", "text": "style the button"})
    t, got = put(s)
    assert lit(s)[1] == []
    s.interrupt()
    t.join(2)


def test_the_cursor_is_hidden_while_a_choice_is_highlighted(monkeypatch):
    shown = []
    monkeypatch.setattr(tui.curses, "curs_set", shown.append)
    s = screen()
    s.items.append({"t": "user", "text": "style the button"})
    t, got = put(s, CHOICE)
    s.draw(Scr(30, 110))
    assert shown[-1] == 0
    s.key(curses.KEY_UP)  # round to your own row
    s.draw(Scr(30, 110))
    assert shown[-1] == 1
    s.interrupt()
    t.join(2)


def test_the_bars_rules_take_the_modes_colour_while_it_asks():
    s = screen()
    rows, _ = s.input_rows(80)
    assert rows[0][0][1] == "frame" and rows[-1][0][1] == "frame"
    t, got = put(s)
    rows, _ = s.input_rows(80)
    assert rows[0][0][1] == "auto" and rows[-1][0][1] == "auto"
    s.interrupt()
    t.join(2)


def test_a_question_of_several_lines_is_wrapped_under_its_mark():
    s = screen()
    t, got = put(s, "```ask\nWhich colour should the button be?\nIt shows on every page of the site, and on the sign-in form as well.\n```")
    rows = panel(s, 50)
    assert rows[0] == "? Which colour should the button be?"
    assert rows[1].startswith("  It shows on every page") and rows[2].startswith("  ") and "as well." in rows[2]
    assert all(len(r) <= 50 for r in rows)
    s.interrupt()
    t.join(2)


def test_a_long_choice_is_cut_to_the_bar_until_it_is_the_highlighted_one():
    long = "Create a new module for the helpers and move every shared function into it"
    s = screen()
    t, got = put(s, f"```ask\nWhere should the helpers go?\n- utils.py\n- {long}\n```")
    rows = panel(s, 50)
    assert rows[3].startswith("  2. Create a new module") and rows[3].endswith("…") and len(rows) == 5
    s.key(curses.KEY_DOWN)
    rows = panel(s, 50)
    assert len(rows) == 6 and " ".join(r.strip() for r in rows[3:5]) == tui.MARK + " 2. " + long  # whole, over two rows
    s.interrupt()
    t.join(2)


def test_on_a_short_screen_the_choices_scroll_with_the_highlight_and_the_conversation_keeps_its_room():
    s = screen()
    s.items.append({"t": "user", "text": "pick a number"})
    t, got = put(s, "```ask\nWhich one?\n" + "\n".join(f"- choice {n}" for n in range(1, 10)) + "\n```")
    scr = Scr(16, 110)
    s.draw(scr)
    seen = "\n".join(scr.text(y) for y in range(16))
    assert "Which one?" in seen and "1. choice 1" in seen and "9. choice 9" not in seen
    assert "pick a number" in seen  # the conversation is still there
    for _ in range(8):
        s.key(curses.KEY_DOWN)
    scr = Scr(16, 110)
    s.draw(scr)
    seen = "\n".join(scr.text(y) for y in range(16))
    assert tui.MARK + " 9. choice 9" in seen and "1. choice 1" not in seen and "Which one?" in seen
    s.interrupt()
    t.join(2)


def test_while_it_waits_the_question_is_on_the_screen_once_and_afterwards_stands_in_the_conversation():
    s = screen()
    s.items.append({"t": "user", "text": "style the button"})
    t, got = put(s, "```ask\nWhich colour should the button be?\nIt shows on every page.\n- red\n- blue\n```")
    text = "\n".join(drawn(s))
    assert text.count("Which colour should the button be?") == 1 and "It shows on every page." in text
    assert "1. red" in text and "2. blue" in text
    assert not any(kind == "" and item is s.items[-1] for kind, _, item in s.lines(90))  # no window of its own yet
    s.key(curses.KEY_DOWN)
    s.key("\r")
    t.join(2)
    s.show_result(sprint.ANSWERED + "blue")
    text = "\n".join(drawn(s))
    assert s.items[-1]["state"] == "done" and "→ blue" in text and "answered" in text
    assert text.count("Which colour should the button be?") == 1 and "It shows on every page." in text
    assert "The user answered" not in text and "waits" not in text and "Something else" not in text


def test_a_question_too_long_for_the_bar_is_cut_there_and_stands_whole_in_the_conversation():
    s = screen()
    s.items.append({"t": "user", "text": "style the button"})
    lines = [f"Reason number {n} for asking." for n in range(1, 31)]
    t, got = put(s, "```ask\n" + "\n".join(lines) + "\n- red\n- blue\n```")
    text = "\n".join(drawn(s, 24))
    assert "Reason number 1 for asking." in text and "1. red" in text and "2. blue" in text  # the choices keep their room
    assert any(item is s.items[-1] for _, _, item in s.lines(90))
    assert "Reason number 30 for asking." in "\n".join(flat(spans) for _, spans, _ in s.lines(90))
    s.interrupt()
    t.join(2)


def test_the_keys_under_the_bar_are_the_ones_that_answer():
    s = screen()
    s.items.append({"t": "user", "text": "style the button"})
    s.sidebar = s.history = False
    t, got = put(s, CHOICE)
    last = drawn(s)[-1]
    assert "enter select" in last and "1-3" in last and "esc the model decides" in last
    s.key(curses.KEY_UP)
    last = drawn(s)[-1]
    assert "enter answer" in last and "enter select" not in last
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
