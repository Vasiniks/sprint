"""/usage: what this session and every session before it have cost, in tokens, time and tool calls"""
import io
import json
import time

import sprint
import sprint_tui as tui
from sprint import Agent
from sprint_tui import Screen, flat
from test_sprint import scripted


def agent(*replies, **kw):
    kw.setdefault("mode", "auto")
    return Agent(chat=scripted(*replies), out=io.StringIO(), **kw)


def text(rows):
    return "\n".join("".join(t for t, _ in r) for r in rows)


def test_a_reply_is_counted_with_the_tokens_the_server_reports():
    a = agent([{"prompt": 120}, "Hello there.", {"out": 7, "secs": 0.5}])
    a.run("hi")
    t = a.tally
    assert (t["messages"], t["replies"], t["sent"], t["written"]) == (1, 1, 120, 7) and t["secs"] == 0.5
    assert t["models"][a.model] == {"replies": 1, "sent": 120, "written": 7, "secs": 0.5}


def test_without_server_counts_the_tokens_are_estimated():
    a = agent("A plain answer of some length.")
    a.run("hi")
    assert a.tally["sent"] > 20 and 3 <= a.tally["written"] <= 12


def test_reasoning_is_counted_as_part_of_what_was_written():
    a = agent([{"prompt": 100}, "<think>" + "weigh it up " * 20 + "</think>", "Four.", {"out": 70, "secs": 1.0}])
    a.run("2+2")
    assert 40 <= a.tally["reasoning"] <= 70 and a.tally["written"] == 70


def test_tool_calls_are_counted_by_tool_and_failures_apart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("one\n")
    a = agent("```read a.txt\n```", "```read missing.txt\n```", "```shell\nexit 3\n```", "Done.")
    a.run("look")
    assert a.tally["tools"] == {"read": 2, "shell": 1} and a.tally["failed"] == 2 and a.tally["replies"] == 4


def test_every_message_adds_to_a_lifetime_file_kept_by_day():
    a = agent([{"prompt": 100}, "One.", {"out": 5, "secs": 0.2}], [{"prompt": 150}, "Two.", {"out": 6, "secs": 0.3}])
    a.run("first")
    a.run("second")
    days = json.loads(sprint.usage_file().read_text())["days"]
    today = days[time.strftime("%Y-%m-%d")]
    assert (today["messages"], today["replies"], today["sent"], today["written"]) == (2, 2, 250, 11)
    b = agent([{"prompt": 50}, "Three.", {"out": 4, "secs": 0.1}])  # another session, the same day
    b.run("third")
    today = json.loads(sprint.usage_file().read_text())["days"][time.strftime("%Y-%m-%d")]
    assert (today["messages"], today["sent"], today["written"]) == (3, 300, 15) and b.tally["messages"] == 1


def test_usage_shows_the_session_the_token_types_the_window_the_days_and_the_lifetime():
    sprint.usage_file().parent.mkdir(parents=True, exist_ok=True)
    old = {"messages": 9, "replies": 30, "sent": 90000, "written": 6000, "reasoning": 500, "secs": 80.0, "tools": {"shell": 12},
           "failed": 2, "models": {"old-model": {"replies": 30, "sent": 90000, "written": 6000, "secs": 80.0}}}
    sprint.usage_file().write_text(json.dumps({"days": {"2026-01-05": old}}))
    a = agent([{"prompt": 1200}, "Hello.", {"out": 40, "secs": 0.4}], model="hf.co/acme/tiny-GGUF:Q4")
    a.run("hi")
    rows = sprint.command(a, "/usage")
    out = text(rows)
    for part in ("Session", "sent", "written", "reasoning", "1.2k", "100 tok/s", "In the window now", "system prompt",
                 "Last 14 days", "Models", "old-model", "tiny", "Lifetime", "2026-01-05", "91.2k", "shell 12"):
        assert part in out, part
    assert all(len("".join(t for t, _ in r)) <= 74 for r in rows)
    assert any(set(t) <= set("▁▂▃▄▅▆▇█ ") and len(t.strip()) >= 1 and len(t) == 14 for r in rows for t, _ in r)  # the 14-day chart


def test_usage_with_nothing_yet_says_so_and_does_not_fail():
    out = text(sprint.command(agent(), "/usage"))
    assert "Session" in out and "nothing yet" in out


def test_a_damaged_lifetime_file_is_ignored_not_fatal():
    sprint.usage_file().parent.mkdir(parents=True, exist_ok=True)
    sprint.usage_file().write_text("{not json")
    a = agent([{"prompt": 10}, "Ok.", {"out": 2, "secs": 0.1}])
    a.run("hi")
    assert "Lifetime" in text(sprint.command(a, "/usage"))


def test_usage_opens_in_the_popup_and_can_be_scrolled():
    s = Screen(agent([{"prompt": 10}, "Ok.", {"out": 2, "secs": 0.1}]))
    s.submit("/usage")
    assert s.popup and s.popup["title"] == "Usage" and "/usage" in dict(tui.COMMANDS) and "/usage" in tui.HELP and "/usage" in sprint.HELP
    _, can = s.shown()
    assert len(can) > 10  # every line can be moved to, so a long report scrolls
    s.popup_key("\n")
    assert s.popup  # Enter chooses nothing here


# ---------- after each answer: what it cost ----------

def test_the_agent_can_say_what_one_message_cost():
    a = agent([{"prompt": 1200}, "Hello.", {"out": 40, "secs": 0.4}])
    before, began = dict(a.tally), time.time() - 12.6
    a.run("hi")
    assert a.receipt(before, began) == "↑ 1.2k ↓ 40 tokens | 12s"
    assert a.receipt(dict(a.tally), time.time() - 3.21) == "↑ 0 ↓ 0 tokens | 3.2s"  # short times keep a decimal


def test_every_answer_on_the_screen_ends_with_its_tokens_and_time():
    s = Screen(agent([{"prompt": 300}, "One.", {"out": 5, "secs": 0.1}], [{"prompt": 420}, "Two.", {"out": 9, "secs": 0.1}]))
    for message in ("first", "second"):
        s.submit(message)
        s.worker.join(10)
    answers = [it for it in s.items if it["t"] == "text"]
    assert [it["cost"].split(" | ")[0] for it in answers] == ["↑ 300 ↓ 5 tokens", "↑ 420 ↓ 9 tokens"]
    assert [it["t"] for it in s.items] == ["user", "text", "user", "text"]  # it rides on the answer, not as a line of its own
    rows = [(flat(spans), spans) for kind, spans, item in s.lines(60)]
    cost = [spans for text, spans in rows if "tokens | " in text]
    assert len(cost) == 2 and cost[0][-1][1] == "ghost" and flat(cost[0]).startswith("  ↑ 300 ↓ 5 tokens | ")
    at = [text for text, _ in rows]
    assert at.index("  One.") + 1 == at.index(flat(cost[0]))  # directly under its answer


def test_a_command_you_ran_yourself_has_no_cost_line(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Screen(agent())
    s.submit("!echo hi")
    s.worker.join(10)
    assert not any("cost" in it for it in s.items)
