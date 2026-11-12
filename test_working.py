"""the line that says what is happening: it says so when the model has gone quiet to write its next tool call (a
call is held back until it is whole, so nothing arrives meanwhile), and a glint of colour crosses its words"""
import io
import time

import sprint
import sprint_tui as tui
from sprint import Agent
from sprint_tui import Screen, flat
from test_sprint import scripted


def screen(*replies, **kw):
    kw.setdefault("mode", "auto")
    return Screen(Agent(chat=scripted(*replies), out=io.StringIO(), **kw))


# ---------- when the last piece of the reply arrived ----------

def test_the_agent_knows_when_it_last_heard_from_the_model_only_while_a_reply_is_arriving():
    seen = []

    def chat(msgs):
        seen.append(a.heard)  # the request is out, nothing has come back yet
        yield "All "
        seen.append(a.heard)
        yield "done."
        seen.append(a.heard)

    a = Agent(chat=chat, out=io.StringIO())
    assert a.heard is None
    began = time.time()
    a.run("go")
    assert seen[0] is None and began <= seen[1] <= seen[2] <= time.time()
    assert a.heard is None  # the reply is over: there is nothing to have gone quiet


def test_a_stopped_or_broken_reply_leaves_nothing_behind_either():
    def chat(msgs):
        yield "All "
        raise RuntimeError("the server went away")

    a = Agent(chat=chat, out=io.StringIO())
    try:
        a.run("go")
    except RuntimeError:
        pass
    assert a.heard is None


# ---------- saying so ----------

def test_a_reply_gone_quiet_after_it_began_is_the_model_preparing_its_next_step():
    s = screen()
    s.agent.think = True
    s.show_thinking("it needs a new file")
    s.agent.heard = time.time()
    assert s.verb() == "thinking" and not s.quiet()
    s.agent.heard = time.time() - tui.QUIET - 0.5
    assert s.quiet() and s.verb() == "preparing the next step"
    assert " preparing the next step (" in flat(s.status_row())
    s.agent.heard = time.time()  # it is heard from again
    assert s.verb() == "thinking"


def test_a_slow_model_is_given_longer_before_its_silence_means_anything():
    s = screen()
    s.agent.heard = time.time() - 1.5
    assert s.quiet()  # no speed known yet, or a quick model: a second and a half of nothing is a call being written
    s.agent.rate = 2.0  # two tokens a second: half a second between them is its ordinary pace
    assert not s.quiet()
    s.agent.heard = time.time() - 3
    assert s.quiet()


def test_before_anything_has_arrived_and_between_replies_nothing_is_called_quiet():
    s = screen()
    assert s.agent.heard is None and not s.quiet() and s.verb() == "working"
    s.show_call(sprint.Call("shell", "", "make", 0, 0))  # a command running: the model is not the one being waited for
    assert s.verb() == "running"


def test_a_question_or_an_approval_waiting_for_you_is_said_first():
    s = screen()
    s.agent.heard = time.time() - 60
    s.asking = {"q": "Which?", "options": []}
    assert s.verb() == "waiting for your answer"
    s.asking, s.pending = None, sprint.Call("shell", "", "make", 0, 0)
    assert s.verb() == "waiting for you"


def test_the_thought_stops_saying_thinking_once_the_model_has_moved_on_to_its_call():
    s = screen()
    s.agent.think = True
    s.show_thinking("it needs a new file")
    s.agent.heard = time.time()
    assert "thinking" in "\n".join(flat(spans) for _, spans, _ in s.lines(80))
    s.agent.heard = time.time() - 30
    said = "\n".join(flat(spans) for _, spans, _ in s.lines(80))
    assert "thought" in said and "thinking" not in said
    s.agent.heard = time.time()  # more reasoning after all: it is thinking again
    assert "thinking" in "\n".join(flat(spans) for _, spans, _ in s.lines(80))


# ---------- the glint ----------

def test_held_still_the_words_are_plain_bold():
    assert tui.glint("thinking") == [("thinking", "bold")]


def test_a_glint_of_colour_crosses_the_words_from_left_to_right():
    word = "preparing the next step"
    lit = lambda t: [i for i, (ch, role) in enumerate(spread(tui.glint(word, t))) if role != "bold"]
    early, later = lit(0.1 * tui.GLINT_PASS), lit(0.6 * tui.GLINT_PASS)
    assert early and later and max(early) < max(later) and min(early) < min(later)
    assert all(len(lit(t / 20 * tui.GLINT_PASS)) <= tui.GLINT_LETTERS for t in range(20))
    assert max(lit(0.01)) == 0  # it comes in over the first letter
    assert flat(tui.glint(word, 0.3)) == word  # the words themselves never change


def spread(spans):
    """spans, a letter at a time"""
    return [(ch, role) for text, role in spans for ch in text]


def test_between_passes_the_words_rest_and_each_pass_is_in_the_runners_colour():
    word = "thinking"
    assert tui.glint(word, tui.GLINT_PASS + 0.1) == [(word, "bold")]  # crossed, and resting
    hue = lambda n: {role for _, role in tui.glint(word, n * tui.GLINT_EVERY_PASS + 0.2 * tui.GLINT_PASS)} - {"bold"}
    hues = [hue(n) for n in range(7)]
    assert all(len(h) == 1 for h in hues)
    names = [h.pop().split(".")[0] for h in hues]
    runner = lambda t: next(role for _, role in tui.dash(t) if role != "ghost").split(".")[0]
    assert names == [runner(n * tui.GLINT_EVERY_PASS + 0.2 * tui.GLINT_PASS) for n in range(7)]  # one colour on the line at a time
    assert set(names) == set(tui.HUES) and names[3] == names[0]  # and all three take their turn
    assert all(role == "bold" or role.endswith(".bold") for _, role in tui.glint(word, 0.2))  # lit letters stay bold


def test_the_status_line_carries_the_glint_while_motion_is_on_and_holds_still_without():
    s = screen()
    s.began = time.time() - 0.3 * tui.GLINT_PASS
    row = s.status_row()
    assert " working (" in flat(row) and any(role.endswith(".bold") and text.strip() for text, role in row[tui.RUN + 1:])
    s.motion = False
    row = s.status_row()
    assert (" working ", "bold") in row and not any(role.endswith(".bold") for _, role in row[tui.RUN + 1:])
