"""the to-do list, after opencode's: the model has a tool to write a list of what a big job takes, and to tick the
items off as it goes. Most messages get no list at all. It replaced plan mode's list of steps asked for up front"""
import io
import json

import sprint
from sprint import Agent
from test_sprint import agent, fake_urlopen, scripted, texts

LIST = "```todo\n[x] Create index.html\n[>] Create style.css\n[ ] Check the page\n```"
DONE = json.dumps({"message": {"content": "ok"}, "done": True})


def model(*replies):
    """a scripted model that, having said its piece, says its last line again if it is asked anything more (it is
    reminded once about a list it left open; the tests of that reminder script it exactly instead)"""
    return scripted(*replies, *replies[-1:] * 2)


def native(todos):
    """a reply that is one native call of the to-do tool"""
    return [{"call": {"name": "todowrite", "arguments": {"todos": todos}}}]


# ---------- the list itself ----------

def test_the_model_writes_a_list_and_the_agent_keeps_it():
    a = agent(model(LIST, "On it."))
    assert a.run("make a page with styles") == "On it."
    assert a.todos == [("Create index.html", "completed"), ("Create style.css", "in_progress"), ("Check the page", "pending")]


def test_it_is_told_how_the_list_stands_the_way_opencode_says_it():
    chat = model(LIST, "On it.")
    a = agent(chat)
    a.run("make a page with styles")
    told = chat.seen[1][-1]["content"]
    assert 'Todos updated: 1/3 completed, 1 in progress, 1 pending. In progress: "Create style.css"' in told


def test_every_call_is_the_whole_list_and_replaces_the_one_before():
    a = agent(model(LIST, "```todo\n[x] Create index.html\n[x] Create style.css\n[>] Check the page\n```", "Nearly."))
    a.run("make a page with styles")
    assert [s for _, s in a.todos] == ["completed", "completed", "in_progress"] and len(a.todos) == 3


def test_an_empty_list_clears_it():
    chat = model(LIST, "```todo\n```", "Cleared.")
    a = agent(chat)
    a.run("make a page with styles")
    assert a.todos == [] and "Todos cleared" in chat.seen[2][-1]["content"]


def test_a_small_model_that_sends_only_what_is_left_does_not_lose_what_it_did(tmp_path, monkeypatch):
    """opencode's rule is the whole list every time. magi 1.2 2B sends what is still to do and leaves the finished
    item out, so the list shrank to nothing and never showed any progress"""
    monkeypatch.chdir(tmp_path)
    chat = model("```todo\n[>] Write a.txt\n[ ] Write b.txt\n[ ] Check them\n```", "```write a.txt\nhi\n```",
                    "```todo\n[>] Write b.txt\n[ ] Check them\n```", "```write b.txt\nhi\n```",
                    "```todo\n[>] Check them\n```", "Nearly there.")
    a = agent(chat)
    a.run("write two files and check them")
    assert a.todos == [("Write a.txt", "completed"), ("Write b.txt", "completed"), ("Check them", "in_progress")]
    assert "2/3 completed" in chat.seen[5][-1]["content"]  # and the model is told so


def test_an_item_reworded_before_any_work_was_done_is_only_reworded():
    a = agent(model("```todo\n[>] Make the styles\n[ ] Make the page\n```", "```todo\n[>] Create style.css\n[ ] Make the page\n```", "Ok."))
    a.run("make a page with styles")
    assert a.todos == [("Create style.css", "in_progress"), ("Make the page", "pending")]


def test_an_item_still_to_do_that_is_left_out_is_gone():
    a = agent(model("```todo\n[>] One\n[ ] Two\n[ ] Three\n```", "```todo\n[>] One\n[ ] Three\n```", "Ok."))
    a.run("do the three things")
    assert a.todos == [("One", "in_progress"), ("Three", "pending")]


def test_an_answer_given_with_items_still_open_gets_one_reminder_about_the_list(tmp_path, monkeypatch):
    """a small model writes its list, does the work in one go and says it is finished, with the list still showing
    the first item in hand. It is asked once to bring the list up to date, or to carry on if there is work left"""
    monkeypatch.chdir(tmp_path)
    chat = model("```todo\n[>] Write a.txt\n[ ] Check it\n```", "```write a.txt\nhi\n```", "All done.",
                    "```todo\n[x] Write a.txt\n[x] Check it\n```", "Done, and the list is up to date.")
    a = agent(chat)
    assert a.run("write a.txt and check it") == "Done, and the list is up to date."
    assert a.todos == [("Write a.txt", "completed"), ("Check it", "completed")]
    reminder = chat.seen[3][-1]["content"]
    assert "to-do list" in reminder and "Write a.txt" in reminder and "Check it" in reminder and "todowrite" in reminder


def test_the_reminder_comes_once_and_what_the_model_then_says_stands():
    chat = model(LIST, "On it.", "As I said: on it.")
    a = agent(chat)
    assert a.run("make a page with styles") == "As I said: on it."
    assert len(chat.seen) == 3 and len(a.todos) == 3  # the list is left as the model left it


def test_no_reminder_without_a_list_or_with_a_finished_one_or_with_plan_off():
    for replies, planning in ((("Hello.",), True), (("```todo\n[x] One\n[-] Two\n```", "Done."), True), ((LIST, "On it."), False)):
        chat = model(*replies)
        a = agent(chat)
        a.planning = planning
        a.run("do the two things")
        assert len(chat.seen) == len(replies)


def test_a_native_call_carries_the_list_as_items_with_a_status_each():
    a = agent(model(native([{"content": "Create index.html", "status": "completed"},
                               {"content": "Create style.css", "status": "in_progress"},
                               {"content": "Check the page", "status": "pending"},
                               {"content": "Add a map", "status": "cancelled"}]), "On it."))
    a.run("make a page with styles")
    assert a.todos == [("Create index.html", "completed"), ("Create style.css", "in_progress"),
                       ("Check the page", "pending"), ("Add a map", "cancelled")]


def test_a_small_model_s_ways_of_sending_the_list_are_understood():
    as_text = json.dumps([{"content": "One", "status": "done"}, {"task": "Two", "status": "in progress"}, {"content": "Three"}])
    for given in (as_text,                                        # the list typed out as a string
                  [{"content": "One", "status": "done"}, {"title": "Two", "status": "doing"}, "Three"],  # other names
                  "[x] One\n[>] Two\n- Three"):                   # or as plain lines
        a = agent(model(native(given), "Ok."))
        a.run("do the three things")
        assert a.todos == [("One", "completed"), ("Two", "in_progress"), ("Three", "pending")], given


def test_a_status_it_made_up_is_still_to_do_and_an_item_with_no_words_is_dropped():
    a = agent(model(native([{"content": "One", "status": "urgent"}, {"content": "", "status": "pending"}, {"status": "pending"}]), "Ok."))
    a.run("do the one thing please")
    assert a.todos == [("One", "pending")]


def test_the_list_is_never_asked_about_whatever_the_mode():
    asked = []
    a = agent(model(LIST, "On it."), mode="ask", ask=lambda call: asked.append(call) or False)
    a.run("make a page with styles")
    assert asked == [] and len(a.todos) == 3


def test_keeping_the_list_does_not_use_up_the_messages_steps(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    turns = []
    for n in range(4):  # four files, the list brought up to date before each
        marks = "\n".join(f"[{'x' if k < n else '>' if k == n else ' '}] Write f{k}.txt" for k in range(4))
        turns += [f"```todo\n{marks}\n```", f"```write f{n}.txt\nhello\n```"]
    turns += ["```todo\n" + "\n".join(f"[x] Write f{k}.txt" for k in range(4)) + "\n```"]  # and once more at the end
    a = agent(model(*turns, "All four written."), steps=5)
    assert a.run("write four files, f0.txt to f3.txt") == "All four written."
    assert all((tmp_path / f"f{n}.txt").exists() for n in range(4))


def test_a_model_that_only_rewrites_its_list_is_stopped_all_the_same():
    lists = [f"```todo\n[ ] Step {n}\n[ ] Another\n```" for n in range(60)]
    a = agent(model(*lists), steps=4)
    assert a.run("do the two things for me").startswith("stopped:")


def test_the_tally_counts_it_as_a_tool_call():
    a = agent(model(LIST, "On it."))
    a.run("make a page with styles")
    assert a.tally["tools"] == {"todo": 1}


# ---------- when there is a list, and when not ----------

def test_a_finished_list_is_put_away_when_you_send_the_next_message():
    a = agent(model("```todo\n[x] One\n[x] Two\n[-] Three\n```", "All done.", "Hello."))
    a.run("do the three things")
    assert len(a.todos) == 3  # still there to look at
    a.run("thanks a lot")
    assert a.todos == []


def test_a_list_with_work_left_stays_for_the_next_message():
    a = agent(model(LIST, "Stopping here.", "Carrying on."))
    a.run("make a page with styles")
    a.run("carry on please")
    assert len(a.todos) == 3


def test_a_new_conversation_starts_without_one():
    a = agent(model(LIST, "On it."))
    a.run("make a page with styles")
    a.reset()
    assert a.todos == []


def test_folding_the_conversation_keeps_the_list_in_front_of_the_model():
    a = agent(model(LIST, "```read a.txt\n```", "```read b.txt\n```", "Stopping."))
    a.run("make a page with styles")
    assert a.compact(everything=True)
    summary = a.msgs[1]["content"]
    assert "[x] Create index.html" in summary and "[>] Create style.css" in summary and "[ ] Check the page" in summary
    assert "Todos updated" not in summary  # (the calls themselves are not listed: the list as it stands says it all)


# ---------- what the model is offered ----------

def names(a):
    return [t["function"]["name"] for t in sprint.tools(a.questions, a.planning)]


def test_the_tool_is_offered_with_the_others_and_says_when_to_use_it():
    a = agent(model())
    assert a.planning is True and names(a)[-1] == "todowrite"
    tool = sprint.tools()[-1]["function"]
    about, items = tool["description"], tool["parameters"]["properties"]["todos"]["items"]
    assert "3 or more" in about and "full list" in about
    assert items["properties"]["status"]["enum"] == ["pending", "in_progress", "completed", "cancelled"]


def test_the_request_carries_it(monkeypatch):
    sent = fake_urlopen(monkeypatch, DONE)
    a = Agent(model="tiny", url="http://localhost:11434", out=io.StringIO())
    list(a.chat([{"role": "user", "content": "hi"}]))
    assert "todowrite" in [t["function"]["name"] for t in sent["body"]["tools"]]


def test_plan_off_takes_the_tool_away_and_on_brings_it_back(monkeypatch):
    a = agent(model())
    assert "off" in texts(sprint.command(a, "/plan"))[0]
    assert a.planning is False and "todowrite" not in names(a) and sprint.load_config()["plan"] is False
    sent = fake_urlopen(monkeypatch, DONE)
    b = Agent(model="tiny", url="http://localhost:11434", out=io.StringIO())
    b.planning = False
    list(b.chat([{"role": "user", "content": "hi"}]))
    assert "todowrite" not in [t["function"]["name"] for t in sent["body"]["tools"]]
    assert "on" in texts(sprint.command(a, "/plan"))[0] and a.planning is True and "todowrite" in names(a)


def test_the_setting_is_remembered_and_can_be_set_by_name():
    a = agent(model())
    sprint.command(a, "/config plan off")
    assert a.planning is False
    assert sprint.build_agent(sprint.arguments([])).planning is False
    sprint.command(a, "/config plan on")
    assert sprint.build_agent(sprint.arguments([])).planning is True


def test_a_model_that_writes_blocks_is_shown_the_block_and_not_with_plan_off():
    a = agent(model())
    a.tools = False
    assert "```todo" in a.system()
    a.planning = False
    assert "```todo" not in a.system()


def test_a_model_that_calls_tools_is_told_in_a_line_when_a_list_is_wanted_and_not_with_plan_off():
    a = agent(model())
    assert a.tools and "todowrite first" in a.system() and "Small jobs and questions need no list" in a.system()
    a.planning = False
    assert "todowrite" not in a.system()


def test_the_window_counts_the_tools_it_is_really_offered():
    a = agent(model())
    with_it = a.extra()
    a.planning = False
    assert 0 < a.extra() < with_it


def test_models_set_against_each_other_keep_no_list():
    made = []
    real = sprint.Agent

    class Spy(real):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            made.append(self)

    a = agent(model("Alpha.", "Beta."))
    sprint.Agent = Spy
    try:
        sprint.race(a, "beta:1b", "say hi to me")
    finally:
        sprint.Agent = real
    assert len(made) == 2 and not any(r.planning for r in made) and a.planning is True


def test_the_plain_screen_prints_the_list_with_its_marks():
    out = io.StringIO()
    a = Agent(chat=model(LIST, "On it."), out=out, mode="auto")
    a.run("make a page with styles")
    shown = out.getvalue()
    assert "todo" in shown and "[x] Create index.html" in shown and "[>] Create style.css" in shown and "[ ] Check the page" in shown
