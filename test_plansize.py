"""a plan is as long as the job: none at all for a greeting or a question, two steps for a small change, eight or more
for a whole website. It used to be asked for as "at most 5" steps, and a small model then wrote five for everything,
"hi" included"""
import io

import sprint
from sprint import Agent
from test_sprint import agent, scripted

EIGHT = "\n".join(f"{n}. Make part {n}" for n in range(1, 9))


def asked(chat, n=0):
    """the nth request the model was sent: its messages"""
    return chat.seen[n]


def test_the_model_is_asked_for_as_many_steps_as_the_job_needs_not_for_five():
    chat = scripted("NONE", "Done.")
    agent(chat).run_plan("make me a website for my bakery")
    said = " ".join(m["content"] for m in asked(chat))
    assert "at most 5" not in said and "as many as the job needs" in said
    assert asked(chat)[0]["role"] == "system" and asked(chat)[-1] == {"role": "user", "content": "make me a website for my bakery"}


def test_it_is_shown_what_a_long_plan_a_short_plan_and_no_plan_look_like():
    chat = scripted("NONE", "Done.")
    agent(chat).run_plan("make me a website for my bakery")
    shown = [m["content"] for m in asked(chat) if m["role"] == "assistant"]
    sizes = sorted(len(sprint.steps_in(reply)) for reply in shown)
    assert "NONE" in shown and sizes[0] == 0 and 2 in sizes and sizes[-1] >= 8  # no plan, a short one, a long one
    assert len(asked(chat)) == 2 + 2 * len(shown)  # each with the request it answers


def test_a_big_job_keeps_every_step_it_was_given(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted(EIGHT, *[f"Part {n} made." for n in range(1, 9)])
    a = agent(chat)
    shown = []
    a.show_plan = lambda steps, at: shown.append((len(steps), at))
    assert a.run_plan("build a website with eight pages") == "Part 8 made."
    asks = [m["content"] for m in a.msgs if m["role"] == "user"]
    assert "step 1 of 8" in asks[0] and "step 8 of 8: Make part 8" in asks[-1] and shown[-1] == (8, 8)


def test_none_means_no_plan_and_the_message_is_just_answered():
    chat = scripted("NONE", "It reads the file a line at a time.")
    a = agent(chat)
    assert a.run_plan("how does the parser work?") == "It reads the file a line at a time."
    assert [m["content"] for m in a.msgs if m.get("human")] == ["how does the parser work?"]


def test_a_word_or_two_is_never_planned_and_costs_no_request():
    for said in ("hi", "thanks!", "continue", "ok go"):
        chat = scripted("Hello.")
        a = agent(chat)
        assert a.run_plan(said) == "Hello." and len(chat.seen) == 1, said
        assert asked(chat)[0]["content"] == a.msgs[0]["content"]  # the one request was the conversation itself


def test_a_list_that_runs_away_is_cut_off_and_not_waited_for():
    took = []

    def chat(msgs):
        for n in range(1, 200):
            took.append(n)
            yield f"{n}. Import module {n}\n"

    a = Agent(chat=chat, out=io.StringIO())
    steps = a.plan("write a snake game with pygame")
    assert len(steps) == sprint.PLAN_MOST and steps[-1] == f"Import module {sprint.PLAN_MOST}"
    assert len(took) <= sprint.PLAN_MOST + 2  # it stopped listening once it had enough


def test_a_step_said_twice_counts_once():
    a = agent(scripted("1. Create index.html\n2. Add the menu\n3. Create index.html\n4. add the menu\n5. Check it"))
    assert a.plan("make a page with a menu") == ["Create index.html", "Add the menu", "Check it"]


def test_one_step_over_and_over_is_no_plan():
    a = agent(scripted("1. Add the link\n2. Add the link\n3. Add the link"))
    assert a.plan("put a link in the menu") == []


def test_a_follow_up_is_planned_knowing_what_came_before():
    chat = scripted("NONE", "I wrote csv_to_json.py.", "1. Create test_csv_to_json.py\n2. Run the tests", "Made.", "They pass.")
    a = agent(chat)
    a.run_plan("write a tool that converts csv to json")
    a.run_plan("now add tests for it")
    sent = asked(chat, 2)
    assert sent[-1]["content"] == "now add tests for it"
    assert [m["content"] for m in sent[-3:-1]] == ["write a tool that converts csv to json", "I wrote csv_to_json.py."]
    assert [m["role"] for m in sent[-3:]] == ["user", "assistant", "user"]


def test_what_came_before_is_given_in_brief():
    chat = scripted("NONE", "x" * 5000, "NONE", "Fine.")
    a = agent(chat)
    a.run_plan("write a very long story about a parser " * 40)
    a.run_plan("now make it shorter please")
    before = asked(chat, 2)[-3:-1]
    assert all(len(m["content"]) <= 400 for m in before)


def test_a_first_message_has_nothing_before_it():
    chat = scripted("NONE", "Done.")
    agent(chat).run_plan("make me a website for my bakery")
    assert asked(chat)[-2]["role"] == "assistant" and asked(chat)[-2]["content"] in [t for pair in sprint.PLAN_SHOWN for t in pair]


def test_a_plan_is_asked_for_coolly_whatever_temperature_you_set():
    seen = []

    def chat(msgs):
        seen.append(dict(a.sampling))
        yield "NONE"

    a = Agent(chat=chat, out=io.StringIO())
    a.plan("make me a website for my bakery")
    a.sampling = {"temperature": 1.4, "seed": 3}
    a.plan("make me a website for my bakery")
    a.sampling = {"temperature": 0}
    a.plan("make me a website for my bakery")
    assert seen == [{"temperature": 0.3}, {"temperature": 0.3, "seed": 3}, {"temperature": 0}]
    assert a.sampling == {"temperature": 0}  # and yours is back for the work itself
