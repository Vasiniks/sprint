"""/usage: what this session and every session before it have cost, in tokens, time and tool calls. The screen is
laid out after Claude Code's stats: an overview (a heat map of the days, then the stats in pairs), the models (tokens
per day as a chart, then each model's share), and a page for the session in hand"""
import curses
import datetime
import io
import json
import re
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


def today_on_file():
    return json.loads(sprint.usage_file().read_text())["days"][time.strftime("%Y-%m-%d")]


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


# ---------- sessions, the longest of them, and the hour of the day ----------

def test_a_session_is_counted_once_however_many_messages_it_has():
    a = agent("One.", "Two.")
    a.run("first")
    a.run("second")
    assert today_on_file()["sessions"] == 1 and a.tally["sessions"] == 1
    agent("Three.").run("third")  # sprint opened again, the same day
    assert today_on_file()["sessions"] == 2


def test_opening_sprint_without_sending_anything_is_no_session():
    a = agent()
    sprint.usage_pages(a)
    assert a.tally["sessions"] == 0 and not sprint.usage_file().exists()


def test_the_longest_session_of_a_day_is_kept_not_added_up():
    a = agent("One.")
    a.started = time.time() - 600
    a.run("first")
    b = agent("Two.")
    b.started = time.time() - 100
    b.run("second")
    assert 599 <= today_on_file()["longest"] <= 610  # ten minutes, not ten plus the other one's
    assert 99 <= b.tally["longest"] <= 110


def test_the_hour_of_each_message_is_counted():
    a = agent("One.", "Two.")
    a.run("first")
    a.run("second")
    hours = today_on_file()["hours"]
    assert sum(hours.values()) == 2 and all(0 <= int(h) <= 23 for h in hours)


def test_the_steps_of_a_plan_are_not_messages_of_yours():
    a = agent("One.")
    a.run("do the next step", human=False)
    assert a.tally["sessions"] == 0 and a.tally["hours"] == {}


def test_a_model_raced_against_yours_is_no_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = agent("Alpha says hi.", "Beta says hi.")
    sprint.race(a, "beta:1b", "say hi")
    today = today_on_file()
    assert today["replies"] == 2 and today["sessions"] == 0 and today["hours"] == {}


def test_days_recorded_before_any_of_this_still_load():
    sprint.usage_file().parent.mkdir(parents=True, exist_ok=True)
    old = {"messages": 9, "replies": 30, "sent": 90000, "written": 6000, "reasoning": 500, "secs": 80.0, "tools": {"shell": 12},
           "failed": 2, "models": {"old-model": {"replies": 30, "sent": 90000, "written": 6000, "secs": 80.0}}}
    sprint.usage_file().write_text(json.dumps({"days": {"2026-01-05": old}}))
    day = sprint.load_usage()["2026-01-05"]
    assert (day["sessions"], day["longest"], day["hours"], day["replies"]) == (0, 0, {}, 30)


def test_a_damaged_lifetime_file_is_ignored_not_fatal():
    sprint.usage_file().parent.mkdir(parents=True, exist_ok=True)
    sprint.usage_file().write_text("{not json")
    a = agent([{"prompt": 10}, "Ok.", {"out": 2, "secs": 0.1}])
    a.run("hi")
    assert "Total tokens" in text(sprint.command(a, "/usage"))


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


# ---------- /usage in pages: the overview, the models, the session ----------

def back(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


def on(replies, wrote=1000, model="old-model", **more):
    """one day's tally as it is on file: this many replies, this many tokens written (and twelve times that sent)"""
    return {"messages": max(1, replies // 3), "replies": replies, "sent": wrote * 12, "written": wrote, "reasoning": wrote // 5,
            "secs": wrote / 50, "tools": {"shell": 4}, "failed": 1,
            "models": {model: {"replies": replies, "sent": wrote * 12, "written": wrote, "secs": wrote / 50}}, **more}


def seeded(days=None):
    """an agent that has answered once today, with some earlier days on file: the busiest three days ago, a quieter
    one yesterday, a faint one nine days ago, and one long before"""
    sprint.usage_file().parent.mkdir(parents=True, exist_ok=True)
    sprint.usage_file().write_text(json.dumps({"days": days if days is not None else {
        back(40): on(6, 2000), back(9): on(2, 500), back(3): on(30, 4000), back(1): on(12, 1000, "other-model")}}))
    a = agent([{"prompt": 1200}, "<think>hm hm hm hm</think>Hello there.", {"out": 40, "secs": 0.4}], model="hf.co/acme/tiny-GGUF:Q4", think=True)
    a.run("hi")
    return a


def page(name, a=None, **kw):
    return dict(sprint.usage_pages(a or seeded(), **kw))[name]


def heat(rows):
    """the seven rows of the heat map, Sunday first: each (its label, its cells a week to a column)"""
    found = [text([r]) for r in rows if len(text([r])) > 8 and set(text([r])[6:]) <= set("·░▒▓█ ") and text([r])[6:].strip()]
    return [(line[:6].strip(), line[6:]) for line in found]


def cell(rows, days_ago):
    """the heat map's cell for the day this many days back"""
    grid, day = heat(rows), datetime.date.today() - datetime.timedelta(days=days_ago)
    this_sunday = datetime.date.today() - datetime.timedelta(days=(datetime.date.today().weekday() + 1) % 7)
    that_sunday = day - datetime.timedelta(days=(day.weekday() + 1) % 7)
    weeks_back = (this_sunday - that_sunday).days // 7
    line = grid[(day.weekday() + 1) % 7][1]
    return line[len(grid[0][1]) - 1 - weeks_back] if weeks_back < len(line) else None


def test_usage_comes_in_three_pages():
    pages = sprint.usage_pages(seeded())
    assert [name for name, _ in pages] == ["Overview", "Models", "Session"]
    assert all(len("".join(t for t, _ in r)) <= 70 for _, rows in pages for r in rows)
    joined = [r for n, (_, rows) in enumerate(pages) for r in ([[]] * bool(n) + rows)]
    assert len(sprint.usage_rows(seeded())) == len(joined)  # plain mode: one after another


def test_every_colour_on_the_pages_is_one_the_plain_session_can_print_too():
    for width in (36, 70, 88):
        for span in sprint.SPANS:
            roles = {role for _, rows in sprint.usage_pages(seeded(), span, width) for r in rows for _, role in r}
            assert roles <= set(sprint.ANSI), roles - set(sprint.ANSI)


def test_the_pages_fit_whatever_width_they_are_drawn_for():
    for width in (36, 44, 59, 60, 70, 88):
        for span in sprint.SPANS:
            for name, rows in sprint.usage_pages(seeded(), span, width):
                assert all(len(text([r])) <= width for r in rows), (width, span, name, [text([r]) for r in rows if len(text([r])) > width])


def test_usage_with_nothing_yet_says_so_and_does_not_fail():
    out = text(sprint.command(agent(), "/usage"))
    assert "No stats yet" in out and "nothing yet this session" in out
    assert "No model usage yet" in text(page("Models", agent()))


def test_the_overview_has_a_cell_for_every_day_a_week_to_a_column():
    rows = page("Overview")
    grid = heat(rows)
    assert len(grid) == 7 and [label for label, _ in grid] == ["", "Mon", "", "Wed", "", "Fri", ""]
    assert len({len(cells) for _, cells in grid}) == 1 and len(grid[0][1]) == 52  # a year of weeks where there is room
    assert cell(rows, 3) == "█" and cell(rows, 0) == "░" and cell(rows, 2) == "·"  # the busiest day, the quietest, an idle one
    assert cell(rows, 40) in "░▒▓█" and cell(rows, 41) == "·"
    after = (5 - datetime.date.today().weekday()) % 7  # days of this week still to come: no cell at all
    assert all(cells[-1] == " " for _, cells in grid[7 - after:]) and all(cells[-1] != " " for _, cells in grid[:7 - after])


def test_the_busier_a_day_the_darker_its_cell():
    rows = page("Overview")
    shade = "·░▒▓█".index
    assert shade(cell(rows, 0)) < shade(cell(rows, 9)) < shade(cell(rows, 40)) < shade(cell(rows, 3))  # 1 reply, 2, 6, 30
    sprint.usage_file().write_text(json.dumps({"days": {back(2): on(1)}}))
    assert cell(page("Overview", agent()), 2) == "█"  # the only day there is, is the busiest there is


def test_the_months_run_along_the_top_and_the_legend_sits_underneath():
    rows = [text([r]) for r in page("Overview")]
    months = next(n for n, line in enumerate(rows) if datetime.date.today().strftime("%b") in line and "·" not in line and ":" not in line)
    first = next(n for n, line in enumerate(rows) if set(line[6:]) <= set("·░▒▓█ ") and line[6:].strip())
    assert months == first - 1
    last_month = (datetime.date.today().replace(day=1) - datetime.timedelta(days=1)).strftime("%b")
    assert last_month in rows[months] and rows[months].index(last_month) < rows[months].index(datetime.date.today().strftime("%b"), rows[months].index(last_month) + 1)
    legend = rows[first + 7]
    assert "Less ░ ▒ ▓ █ More" in legend


def test_the_heat_map_shows_fewer_weeks_where_there_is_less_room():
    rows = page("Overview", width=36)
    grid = heat(rows)
    assert len(grid) == 7 and 20 <= len(grid[0][1]) < 52 and all(len(text([r])) <= 36 for r in rows)
    assert cell(rows, 3) == "█"  # and the newest weeks are the ones kept


def test_the_stats_come_in_pairs_in_two_columns():
    a = seeded()
    a.started = time.time() - 125
    a.count()  # (anything counted brings the session's length up to date)
    rows = [text([r]) for r in page("Overview", a)]
    pairs = [("Favourite model:", "Total tokens:"), ("Sessions:", "Longest session:"), ("Active days:", "Longest streak:"),
             ("Most active day:", "Current streak:"), ("Peak hour:", "Tool calls:")]
    lines = [next(line for line in rows if left in line) for left, _ in pairs]
    assert all(right in line for line, (_, right) in zip(lines, pairs))
    assert len({line.index(right) for line, (_, right) in zip(lines, pairs)}) == 1  # the second column starts in one place
    out = "\n".join(rows)
    total = 12 * 7500 + 7500 + 1200 + 40  # everything sent and written, every day
    assert "Favourite model: old-model" in out and f"Total tokens: {sprint.human(total)}" in out
    assert "Sessions: 5" in out  # a day on file from before sessions were counted is one, and so is today's
    assert "Longest session: 2m 0" in out
    assert "Active days: 5/41" in out and "Longest streak: 2 days" in out and "Current streak: 2 days" in out
    busiest = datetime.date.today() - datetime.timedelta(days=3)
    assert f"Most active day: {busiest:%b} {busiest.day}" in out
    hour = time.localtime().tm_hour
    assert f"Peak hour: {hour:02d}:00-{(hour + 1) % 24:02d}:00" in out or f"Peak hour: {(hour - 1) % 24:02d}:00" in out
    assert "Tool calls: 16 (4 failed)" in out


def test_what_was_never_recorded_is_a_dash_not_a_nought():
    sprint.usage_file().parent.mkdir(parents=True, exist_ok=True)
    sprint.usage_file().write_text(json.dumps({"days": {back(3): on(30, 4000), back(2): on(5, 100)}}))
    out = text(page("Overview", agent()))
    assert "Longest session: -" in out and "Peak hour: -" in out and "Sessions: 2" in out
    assert "Current streak: 0 days" in out and "Longest streak: 2 days" in out  # nothing today or yesterday


def test_a_streak_still_stands_on_a_day_not_yet_used():
    sprint.usage_file().parent.mkdir(parents=True, exist_ok=True)
    sprint.usage_file().write_text(json.dumps({"days": {back(3): on(3), back(2): on(3), back(1): on(3)}}))
    assert "Current streak: 3 days" in text(page("Overview", agent()))
    sprint.usage_file().write_text(json.dumps({"days": {back(1): on(3)}}))
    assert re.search(r"Current streak: 1 day\s*$", text(page("Overview", agent())), re.M)


def test_the_overview_ends_by_saying_what_the_tokens_amount_to():
    rows = [text([r]) for r in page("Overview")]
    assert "pages" in rows[-1] and "7.5k" in rows[-1]  # what the models wrote, as pages of a book
    sprint.usage_file().write_text(json.dumps({"days": {back(1): on(40, 600_000)}}))
    assert "novel" in text(page("Overview", agent()))


def test_a_range_keeps_the_stats_to_its_days():
    a = seeded()
    week, month, ever = (text(page("Overview", a, span=s)) for s in ("7d", "30d", "all"))
    assert f"Total tokens: {sprint.human(13 * 5000 + 1240)}" in week and "Active days: 3/7" in week  # three days ago, yesterday, today
    assert f"Total tokens: {sprint.human(13 * 5500 + 1240)}" in month and "Active days: 4/30" in month
    assert "Active days: 5/41" in ever
    assert "Longest streak: 2 days" in week
    names = lambda s: text(page("Models", a, span=s))
    assert "old-model" in names("7d") and "other-model" in names("7d")
    sprint.usage_file().write_text(json.dumps({"days": {back(40): on(6, 2000)}}))
    assert "old-model" not in text(page("Models", a, span="30d")) and "old-model" in text(page("Models", a, span="all"))


def test_the_range_in_force_is_marked_beside_the_legend():
    for span, name in (("all", "All time"), ("7d", "Last 7 days"), ("30d", "Last 30 days")):
        row = next(r for r in page("Overview", span=span) if "Less" in text([r]))
        marked = [t.strip() for t, role in row if role == "bold"]
        assert marked == [name] and all(other in text([row]) for other in ("All time", "Last 7 days", "Last 30 days"))


def test_a_line_is_drawn_from_day_to_day():
    grid = sprint.plot([[0, 0, 8, 8, 0]], 5)
    lines = ["".join(ch for ch, _ in row) for row in grid]
    assert len(lines) == 5 and all(len(line) == 5 for line in lines)
    assert lines[0] == "  ╭─╮" and lines[-1] == "──╯ ╰" and all(line == "  │ │" for line in lines[1:-1])
    assert {who for row in grid for ch, who in row if ch != " "} == {0}


def test_a_later_line_gives_way_to_an_earlier_one_where_they_cross():
    grid = sprint.plot([[4, 4, 4], [0, 4, 0]], 3)
    top = grid[0]
    assert "".join(ch for ch, _ in top) == "───" and {who for _, who in top} == {0}  # the first series is the one on top
    assert grid[-1] == [("─", 1), ("╯", 1), ("╰", 1)]


def test_a_line_lying_on_nought_gives_way_to_one_that_is_not():
    grid = sprint.plot([[100, 0, 0, 0, 0], [0, 0, 0, 1, 0]], 4)  # (one token in a day is still on the bottom row)
    assert [who for _, who in grid[-1]] == [1, 0, 0, 1, 1]  # the second line shows where it has something, and where the first is not


def test_the_models_page_charts_tokens_per_day():
    rows = page("Models")
    out = [text([r]) for r in rows]
    assert out[0].strip() == "Tokens per Day"
    chart = [line for line in out if "┤" in line or "┼" in line]
    assert len(chart) >= 6 and all(line.index("┤" if "┤" in line else "┼") == chart[0].index("┤" if "┤" in chart[0] else "┼") for line in chart)
    assert sprint.human(13 * 4000) in chart[0] and chart[-1].split()[0] == "0"  # the most in a day at the top, nought at the foot
    axis = out[out.index(chart[-1]) + 1]
    today = datetime.date.today()
    assert f"{today:%b} {today.day}" in axis  # days along the bottom, today last
    legend = out[out.index(chart[-1]) + 2]
    assert legend.count("●") == 3 and "old-model" in legend and "other-model" in legend and "tiny" in legend
    drawn = lambda rows: {role for r in rows for t, role in r if set(t) & set("─╭╮╰╯│")}
    assert {"magenta", "cyan"} <= drawn(rows)  # a colour to a model, the same as its bullet
    assert drawn(page("Models", span="7d")) == {"magenta", "cyan", "yellow"}  # with a day given more room, the smallest shows too


def test_the_models_page_lists_each_model_with_its_share_and_its_tokens_in_and_out():
    rows = page("Models")
    out = text(rows)
    assert "● old-model (85.6%)" in out and "In: 78.0k · Out: 6.5k" in out
    assert "● other-model (13.2%)" in out and "● tiny:Q4 (1.3%)" in out and "In: 1.2k · Out: 40" in out
    lines = [text([r]) for r in rows]
    first = next(line for line in lines if "● old-model (" in line)
    assert "● other-model (" in first  # two to a row, the bigger first
    under = lines[lines.index(first) + 1]
    assert under.index("In:") == first.index("●") + 2 and under.count("In:") == 2
    narrow = [text([r]) for r in page("Models", width=40)]
    assert all(len(line) <= 40 for line in narrow) and not any(line.count("●") > 1 and "(" in line for line in narrow)  # one to a row


def test_a_model_is_named_without_where_it_came_from_or_that_it_is_the_latest():
    sprint.usage_file().parent.mkdir(parents=True, exist_ok=True)
    sprint.usage_file().write_text(json.dumps({"days": {back(1): on(3, model="hf.co/GEHIRNS/magi-1.2-2B:latest")}}))
    assert re.search(r"Favourite model: magi-1\.2-2B\s", text(page("Overview", agent())))
    assert "● magi-1.2-2B (100.0%)" in text(page("Models", agent()))


def test_the_session_page_keeps_what_only_sprint_knows():
    out = text(page("Session"))
    for part in ("Session", "Messages:", "Replies:", "Tool calls:", "Generating:", "sent", "written", "reasoning", "Token analysis",
                 "Per reply:", "of what was written", "sent for every token written", "In the window now", "system prompt"):
        assert part in out, part
    assert "1.2k" in out and "30" in out  # 1200 sent for 40 written: thirty to one
    assert "nothing yet this session" in text(page("Session", agent()))


# ---------- the popup ----------

def opened(a=None):
    s = Screen(a or seeded())
    s.submit("/usage")
    return s, s.popup


def body(s, w=80, tall=30):
    return [flat(line) for line in s.popup_lines(w, tall)[0]]


def test_usage_opens_in_the_popup_a_tab_to_a_page():
    s, p = opened()
    assert p["title"] == "Usage" and "/usage" in dict(tui.COMMANDS) and "/usage" in dict(sprint.HELP)
    assert [name for name, _ in p["tabs"]] == ["Overview", "Models", "Session"] and p["tab"] == 0
    lines = body(s)
    assert "Overview" in lines[1] and "Models" in lines[1] and "Session" in lines[1]
    strip = s.popup_lines(80, 30)[0][1]
    assert ("Overview", "chosen") in [(t.strip(), r) for t, r in strip] and ("Models", "chosen") not in [(t.strip(), r) for t, r in strip]
    assert all(len(line) == 80 for line in lines)
    s.popup_key("\n")
    assert s.popup is p  # Enter chooses nothing here


def test_the_arrows_tab_and_the_numbers_turn_the_pages():
    s, p = opened()
    s.popup_key(curses.KEY_RIGHT)
    assert p["tab"] == 1 and any("Tokens per Day" in line for line in body(s))
    s.popup_key("\t")
    assert p["tab"] == 2 and any("In the window now" in line for line in body(s))
    s.popup_key(curses.KEY_RIGHT)
    assert p["tab"] == 0  # round again
    s.popup_key(curses.KEY_LEFT)
    assert p["tab"] == 2
    s.popup_key("2")
    assert p["tab"] == 1


def test_clicking_a_tab_turns_to_it(monkeypatch):
    s, p = opened()
    lines = s.popup_lines(80, 30)[0]
    s.popup["box"] = (3, 10, 80, len(lines))
    x = 10 + flat(lines[1]).index("Session") + 2
    s.pointer(x, 4, curses.BUTTON1_PRESSED)
    assert s.popup and s.popup["tab"] == 2


def test_the_foot_of_the_popup_says_what_the_keys_do():
    s, p = opened()
    foot = body(s)[-1]
    assert "esc to cancel · r to cycle dates · ctrl+s to copy" in foot and "tabs" in foot


def test_a_page_too_tall_for_the_popup_says_that_it_scrolls():
    s, p = opened()
    assert "scroll" not in body(s, 80, 30)[-1]
    assert "↑↓ scroll · " in body(s, 80, 12)[-1] and "esc" in body(s, 80, 12)[-1]
    assert "↑↓ scroll · r dates · esc" in body(s, 44, 12)[-1] and all(len(line) == 44 for line in body(s, 44, 12))


def test_r_goes_round_the_date_ranges_and_stays_on_the_page():
    s, p = opened()
    marked = lambda: next(t.strip() for line in s.popup_lines(80, 30)[0] if "Less" in flat(line) for t, role in line if role == "bold")
    assert marked() == "All time"
    s.popup_key(curses.KEY_RIGHT)
    for name in ("Last 7 days", "Last 30 days", "All time"):
        s.popup_key("r")
        assert s.popup is p and p["tab"] == 1
        s.popup_key(curses.KEY_LEFT)
        assert marked() == name
        s.popup_key(curses.KEY_RIGHT)


def test_ctrl_s_copies_the_page_on_show(monkeypatch):
    copied = []
    monkeypatch.setattr(sprint, "clipboard", lambda t: copied.append(t) or True)
    s, p = opened()
    s.popup_key("\x13")
    assert "Favourite model: old-model" in copied[0] and "Less ░ ▒ ▓ █ More" in copied[0] and "Tokens per Day" not in copied[0]
    assert "copied!" in body(s)[-1] and s.popup is p
    s.popup_key(curses.KEY_RIGHT)
    s.popup_key("\x13")
    assert "Tokens per Day" in copied[1]
    monkeypatch.setattr(sprint, "clipboard", lambda t: False)
    s.popup_key("\x13")
    assert "copy failed" in body(s)[-1]


def test_nothing_on_a_page_is_picked_out_and_up_and_down_scroll_it():
    s, p = opened()
    lines, sel_at, _ = s.popup_lines(80, 12)  # too short for the page
    assert sel_at is None and s.shown()[1] == []
    first = flat(lines[2])
    s.popup_key(curses.KEY_UP)
    assert flat(s.popup_lines(80, 12)[0][2]) == first  # already at the top
    s.popup_key(curses.KEY_DOWN)
    assert flat(s.popup_lines(80, 12)[0][2]) == flat(lines[3])
    for _ in range(60):
        s.popup_key(curses.KEY_DOWN)
    last = body(s, 80, 12)
    assert "pages" in last[-2] or "novel" in last[-2]  # stopped with the last line at the foot
    s.popup_key(curses.KEY_PPAGE)
    assert body(s, 80, 12)[2] != last[2]
    s.popup_key(curses.KEY_RIGHT)
    assert "Tokens per Day" in body(s, 80, 12)[2]  # a new page starts at its top


def test_the_wheel_scrolls_the_page(monkeypatch):
    monkeypatch.setattr(tui, "WHEEL_DOWN", 0x200000)
    s, p = opened()
    lines = s.popup_lines(80, 12)[0]
    p["box"] = (3, 10, 80, len(lines))
    s.pointer(20, 6, 0x200000)
    assert flat(s.popup_lines(80, 12)[0][2]) == flat(lines[3])
    s.pointer(20, 6, curses.BUTTON4_PRESSED)
    assert flat(s.popup_lines(80, 12)[0][2]) == flat(lines[2])


def test_the_pages_are_drawn_for_the_width_the_popup_has():
    s, p = opened()
    wide, narrow = body(s, 80), body(s, 44)
    assert all(len(line) == 80 for line in wide) and all(len(line) == 44 for line in narrow)
    weeks = lambda lines: max(len(line[8:].rstrip(" │")) for line in lines if set(line[8:-2]) <= set("·░▒▓█ ") and line[8:-2].strip())
    assert weeks(wide) == 52 and weeks(narrow) < 40 and not any("…" in line for line in narrow[2:11])
