"""the model can put a question to you, wait for the answer, and carry on by itself when none comes: the tool, what
the model is told, the settings, and how it sits with reasoning, native tool calls, plan mode and the summary"""
import io
import os
import sys
import threading
import time

import pytest

import sprint
from sprint import Agent
from test_sprint import scripted

ASK = "```ask\nWhich colour should the button be?\n```"


def asker(*replies, answer="blue", **kw):
    """an agent whose user answers every question with this (None: nobody answers; a list: one each, in order)"""
    kw.setdefault("mode", "auto")
    a = Agent(chat=scripted(*replies), out=io.StringIO(), **kw)
    a.put = []  # every question put: (the question, its choices)
    answers = iter(answer) if isinstance(answer, list) else None

    def question(text, options=()):
        a.put.append((text, list(options)))
        return next(answers) if answers else answer

    a.question = question
    return a


def told(a, n=-1):
    """everything the model was sent on its nth request, as one text"""
    return "\n".join(str(m.get("content", "")) for m in a.chat.seen[n])


# ---------- the tool ----------

def test_an_ask_block_is_a_tool_call_carrying_the_question():
    c = sprint.parse(ASK)
    assert (c.tool, c.arg, c.body.strip()) == ("ask", "", "Which colour should the button be?")
    assert sprint.parse("```ask Tabs or spaces?\n```").body.strip() == "Tabs or spaces?"  # on the fence line works too
    assert sprint.parse("```question\nTabs or spaces?\n```").tool == "ask"


def test_choices_written_as_a_list_under_the_question_are_taken_apart():
    assert sprint.question_parts("Which one?\n- red\n- blue\n") == ("Which one?", ["red", "blue"])
    assert sprint.question_parts("Which one?\n1. red\n2) blue\n3. green") == ("Which one?", ["red", "blue", "green"])
    assert sprint.question_parts("Shall I go on?") == ("Shall I go on?", [])
    assert sprint.question_parts("Two things:\n- only one bullet") == ("Two things:\n- only one bullet", [])  # one is no choice
    assert sprint.question_parts("- red\n- blue") == ("- red\n- blue", [])  # choices with no question are the question


def test_a_native_call_gives_the_question_and_its_choices_however_they_are_sent():
    call = lambda **args: sprint.from_args("ask", args, 0, 0)
    assert sprint.question_parts(call(question="Which?", options=["red", "blue"]).body) == ("Which?", ["red", "blue"])
    assert sprint.question_parts(call(question="Which?", options="red | blue").body) == ("Which?", ["red", "blue"])
    assert sprint.question_parts(call(question="Which?", options='["red", "blue"]').body) == ("Which?", ["red", "blue"])
    assert sprint.question_parts(call(question="Which?", options="red\nblue").body) == ("Which?", ["red", "blue"])
    assert sprint.question_parts(call(question="Which?", options="red, blue, dark green").body) == ("Which?", ["red", "blue", "dark green"])
    assert sprint.question_parts(call(question="Go on?").body) == ("Go on?", [])
    assert sprint.from_args("ask_user", {"text": "Go on?"}, 0, 0).body == "Go on?"


def test_what_a_model_puts_where_the_choices_go_is_dropped_when_it_is_no_list_of_choices():
    call = lambda **args: sprint.from_args("ask", args, 0, 0)
    # a 2B model sent exactly this with "Which color do you like best?", and it came up as two choices
    assert call(question="Which?", options="None, just a simple question").body == "Which?"
    assert call(question="Which?", options="none").body == "Which?"  # one choice is no choice
    assert call(question="Which?", options=["only this"]).body == "Which?"
    assert call(question="Which?", options={"a": 1}).body == "Which?"
    assert call(question="Which?", options="Use the old parser, which is slower but safe").body == "Which?"  # a sentence, not a list


def test_the_model_is_offered_the_tool_both_ways():
    assert "```ask" in sprint.SYSTEM
    ask = next(t["function"] for t in sprint.tools() if t["function"]["name"] == "ask")
    assert ask["parameters"]["required"] == ["question"] and "options" in ask["parameters"]["properties"]


# ---------- asking, and what the model gets back ----------

def test_the_answer_goes_back_to_the_model_and_the_work_goes_on():
    a = asker(ASK, "Blue it is.")
    assert a.run("style the button") == "Blue it is."
    assert a.put == [("Which colour should the button be?", [])]
    assert "The user answered: blue" in told(a) and "[ask result]" in told(a)
    assert a.tally["tools"] == {"ask": 1} and a.tally["failed"] == 0


def test_a_question_is_never_a_thing_to_approve_whatever_the_mode():
    for mode in sprint.MODES:
        a = asker(ASK, "ok", mode=mode)
        a.ask = lambda call: pytest.fail("a question was put up for approval")
        assert a.run("style the button") == "ok" and len(a.put) == 1


def test_nobody_answering_tells_the_model_to_decide_and_say_what_it_chose():
    a = asker(ASK, "I went with grey.", answer=None)
    a.question_timeout = 45
    assert a.run("style the button") == "I went with grey."
    said = told(a).lower()
    assert "no answer" in said and "45" in said and "decide" in said and "say what you chose" in said
    assert a.tally["failed"] == 0  # nothing went wrong: nobody was there


def test_an_empty_answer_leaves_it_to_the_model():
    a = asker(ASK, "Grey then.", answer="")
    a.run("style the button")
    assert "leaves this to you" in told(a) and "The user answered" not in told(a)


def test_an_ask_with_no_question_in_it_is_an_error_and_nobody_is_bothered():
    a = asker("```ask\n```", "ok")
    a.run("style the button")
    assert a.put == [] and "error: ask needs the question" in told(a)


def test_where_there_is_nobody_to_ask_the_model_is_told_at_once(monkeypatch):
    a = Agent(chat=scripted(ASK, "Grey."), out=io.StringIO(), mode="auto")
    began = time.monotonic()
    assert a.run("style the button") == "Grey."  # (under test the input is no terminal)
    assert time.monotonic() - began < 2 and "no answer" in told(a).lower()


# ---------- with reasoning, native tool calls and plan mode ----------

def test_a_model_that_reasons_first_can_ask_and_its_reasoning_is_shown_as_ever():
    a = asker(["<think>I cannot know the colour.</think>", ASK], ["<think>Blue.</think>", "Done in blue."], think=True)
    shown = []
    a.show_thought = shown.append
    assert a.run("style the button") == "Done in blue."
    assert shown == ["I cannot know the colour.", "Blue."] and a.put[0][0] == "Which colour should the button be?"
    assert "<think>" not in told(a)  # the reasoning is not sent back with the question


def test_a_native_ask_call_is_kept_in_the_models_own_format():
    call = {"name": "ask", "arguments": {"question": "Which colour?", "options": ["red", "blue"]}}
    a = asker([{"call": call}], "Red.", answer="red")
    assert a.run("style the button") == "Red."
    assert a.put == [("Which colour?", ["red", "blue"])]
    assert a.msgs[-3]["tool_calls"] == [{"function": call}]
    assert a.msgs[-2] == {"role": "tool", "tool_name": "ask", "content": "The user answered: red"}


def test_with_a_to_do_list_going_it_can_still_ask_and_the_answer_stays_in_front_of_it():
    a = asker("```todo\n[>] Pick a colour\n[ ] Write the file\n```", ASK,
              "```todo\n[x] Pick a colour\n[>] Write the file\n```", "```write b.css\nbutton { color: blue }\n```",
              "```todo\n[x] Pick a colour\n[x] Write the file\n```", "Written.")
    assert a.run("style the button") == "Written."
    assert len(a.put) == 1 and a.todos == [("Pick a colour", "completed"), ("Write the file", "completed")]
    assert "The user answered: blue" in told(a)  # still there when the file is written


# ---------- the summary keeps what you said ----------

def test_folding_the_conversation_keeps_your_answer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = asker(ASK, "```write b.css\nx\n```", "Done.")
    a.run("style the button")
    assert a.compact(everything=True)
    summary = a.msgs[1]["content"]
    assert "- asked you: Which colour should the button be? → blue" in summary


def test_your_answers_outlast_what_was_merely_looked_at_when_the_summary_is_cut_short(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reads = [f"```read f{n}.txt\n```" for n in range(12)]
    a = asker(ASK, *reads, "Done.", ctx=2048, effort="high")
    a.run("style the button")
    a.compact(everything=True)
    assert "- asked you: Which colour" in a.msgs[1]["content"]


# ---------- settings ----------

def test_how_long_a_question_waits_is_a_setting_one_minute_unless_changed():
    a = asker()
    assert a.question_timeout == 60 and sprint.SETTINGS["question_timeout"] is int
    sprint.command(a, "/config question_timeout 90")
    assert a.question_timeout == 90 and sprint.load_config()["question_timeout"] == 90
    assert any("question_timeout" in "".join(t for t, _ in r) for r in sprint.command(a, "/config"))
    b = sprint.build_agent(sprint.arguments([]))
    assert b.question_timeout == 90  # and it is what the next session starts with


def test_questions_can_be_switched_off_and_then_the_model_is_not_told_of_them():
    a = asker()
    assert a.questions is True
    a.tools = False
    assert "```ask" in a.system()
    before = a.system()
    sprint.command(a, "/config questions off")
    assert a.questions is False and sprint.load_config()["questions"] is False
    assert "```ask" not in a.system() and len(a.system()) < len(before)
    assert "```fetch" in a.system() and "NOT saved or run" in a.system()  # the rest of the prompt is whole
    a.tools = True
    assert "ask" not in [t["function"]["name"] for t in sprint.tools(False)]
    weight = a.extra()
    a.questions = True
    assert a.extra() > weight  # the window is counted with what is really sent
    assert sprint.build_agent(sprint.arguments([])).questions is False


def test_switched_off_the_tool_is_not_sent_to_the_server(monkeypatch):
    sent = []

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

    def urlopen(req, timeout=0):
        sent.append(sprint.json.loads(req.data))
        return Reply(b'{"message": {"content": "ok"}, "done": true}\n')

    monkeypatch.setattr(sprint.urllib.request, "urlopen", urlopen)
    a = Agent(out=io.StringIO(), mode="auto")
    a.run("hello")
    a.questions = False
    a.run("hello")
    names = [[t["function"]["name"] for t in body["tools"]] for body in sent]
    assert "ask" in names[0] and "ask" not in names[1] and "shell" in names[1]


def test_models_set_against_each_other_cannot_stop_to_ask():
    chat = scripted(ASK, "Grey.", ASK, "Grey.")
    a = Agent(chat=chat, out=io.StringIO(), mode="auto")
    a.question = lambda text, options=(): pytest.fail("a race stopped to ask")
    a.tools = False
    results = sprint.race(a, "other-model", "style the button")
    assert [r["answer"] for r in results] == ["Grey.", "Grey."]
    assert "```ask" not in str(chat.seen[0][0]["content"])  # and they are not told they could


# ---------- the plain terminal ----------

@pytest.fixture
def terminal(monkeypatch):
    """a terminal to ask on, where what is typed is whatever the test says: lines.append(...) before the question"""
    lines, waits = type("Lines", (list,), {})(), []
    monkeypatch.setattr(sys, "stdin", type("Tty", (), {"isatty": lambda self: True})())

    def timed_line(secs):
        waits.append(secs)
        return lines.pop(0) if lines else None

    monkeypatch.setattr(sprint, "timed_line", timed_line)
    lines.waits = waits
    return lines


def plain():
    a = Agent(chat=scripted(), out=io.StringIO(), mode="auto")
    a.color = False
    return a


def test_on_a_plain_terminal_the_answer_is_the_line_you_type(terminal):
    a = plain()
    terminal.append("  navy blue \n")
    assert a.question("Which colour?") == "navy blue" and terminal.waits == [60]


def test_a_choice_can_be_given_by_its_number_or_in_your_own_words(terminal):
    a = plain()
    terminal += ["2\n", "teal\n", "7\n"]
    a.show_call(sprint.parse("```ask\nWhich?\n- red\n- blue\n```"))
    assert a.question("Which?", ["red", "blue"]) == "blue"
    assert "1. red" in a.out.getvalue() and "2. blue" in a.out.getvalue()
    assert a.question("Which?", ["red", "blue"]) == "teal"
    assert a.question("Which?", ["red", "blue"]) == "7"  # no such choice: it is what you said


def test_a_line_that_never_comes_is_no_answer_and_the_wait_is_the_setting(terminal):
    a = plain()
    a.question_timeout = 5
    assert a.question("Which colour?") is None and terminal.waits == [5]
    assert "no answer" in a.out.getvalue()


def test_the_plain_terminal_shows_the_question_as_a_question():
    a = plain()
    a.show_call(sprint.parse(ASK))
    out = a.out.getvalue()
    assert "ask" in out and "Which colour should the button be?" in out and "+ Which" not in out  # not as a file's new lines


@pytest.mark.skipif(sprint.WINDOWS, reason="a Windows console is waited on key by key")
def test_a_typed_line_is_waited_for_only_so_long(monkeypatch):
    r, w = os.pipe()
    monkeypatch.setattr(sys, "stdin", os.fdopen(r))
    began = time.monotonic()
    assert sprint.timed_line(0.3) is None and 0.25 <= time.monotonic() - began < 2
    threading.Timer(0.2, lambda: os.write(w, b"teal\n")).start()
    assert sprint.timed_line(3) == "teal"
    os.write(w, b"at once\n")
    assert sprint.timed_line(0) == "at once"  # 0: for ever, which here is no wait at all
    os.close(w)
