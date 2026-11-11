import curses
import io

import pytest

import sprint
import sprint_tui as tui
from sprint import Agent
from sprint_tui import Screen, code, prose, wrap
from test_sprint import scripted


def flat(spans):
    return "".join(t for t, _ in spans)


def roles(spans):
    return {t.strip(): r for t, r in spans if t.strip()}


def screen(*replies, **kw):
    kw.setdefault("mode", "auto")
    return Screen(Agent(chat=scripted(*replies), out=io.StringIO(), **kw))


def finish(s):
    s.worker.join(10)
    assert not s.worker.is_alive()


# ---------- text layout ----------

def test_wrap_breaks_at_spaces_and_never_exceeds_the_width():
    lines = wrap([("the quick brown fox jumps over the lazy dog", "")], 12)
    assert all(len(flat(x)) <= 12 for x in lines)
    assert " ".join(flat(x).strip() for x in lines) == "the quick brown fox jumps over the lazy dog"
    assert flat(lines[0]).strip() == "the quick"


def test_wrap_splits_words_longer_than_the_line_and_keeps_roles():
    lines = wrap([("ab", "x"), ("cdefghij", "y")], 4)
    assert [flat(x) for x in lines] == ["abcd", "efgh", "ij"]
    assert lines[0] == [("ab", "x"), ("cd", "y")]


# ---------- highlighting ----------

def test_prose_colours_paths_inline_code_urls_and_bold():
    r = roles(prose("Edit `main()` in src/app.py, see https://example.com and **note** this.")[0])
    assert r["`main()`"] == "code" and r["src/app.py"] == "path"
    assert r["https://example.com"] == "url" and r["**note**"] == "bold"


def test_prose_marks_headings_and_short_title_lines():
    lines = prose("# Plan\nSteps:\n- first do a thing\nplain text here")
    assert lines[0][0][1] == "title" and lines[1][0][1] == "title"
    assert lines[2][0][1] == "bullet" and lines[3][0][1] == ""


def test_prose_highlights_code_inside_fences_as_code():
    lines = prose("Here:\n```python\ndef f():  # hi\n    return 'x'\n```\ndone def")
    assert roles(lines[2])["def"] == "kw" and roles(lines[2])["# hi"] == "comment"
    assert roles(lines[3])["'x'"] == "string"
    assert "kw" not in [r for _, r in lines[5]]


def test_code_colours_keywords_strings_numbers_and_comments():
    r = roles(code('x = 42 if ok else "no"  // why'))
    assert r["42"] == "number" and r["if"] == "kw" and r['"no"'] == "string" and r["// why"] == "comment"


# ---------- the conversation model ----------

def test_a_turn_becomes_a_user_block_a_tool_window_and_an_answer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("Looking.\n```shell\necho hi\n```", "All done.")
    s.submit("say hi")
    finish(s)
    kinds = [i["t"] for i in s.items]
    assert kinds == ["user", "text", "tool", "text"]
    tool = s.items[2]
    assert tool["call"].body == "echo hi" and tool["result"] == "hi" and tool["state"] == "done"
    assert s.items[1]["text"].strip() == "Looking." and s.items[3]["text"].strip() == "All done."


def test_failed_and_denied_calls_are_marked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```read nope.txt\n```", "```shell\nexit 3\n```", "ok")
    s.submit("go")
    finish(s)
    assert [i["state"] for i in s.items if i["t"] == "tool"] == ["failed", "failed"]


def test_approval_waits_for_a_key_and_always_allows_that_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\necho 1 > a.txt\n```", "ok", mode="ask")
    s.submit("go")
    for _ in range(200):
        if s.pending:
            break
        s.worker.join(0.02)
    assert s.pending.tool == "shell" and s.items[-1]["state"] == "waiting"
    assert not (tmp_path / "a.txt").exists()
    s.key("a")
    finish(s)
    assert (tmp_path / "a.txt").exists() and s.agent.mode == "ask" and s.pending is None
    assert s.agent.allow == ["shell echo 1 > a.txt"]  # "always" allows just this command from now on


def test_escape_rejects_a_pending_call_and_stops_the_turn(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\necho 1 > a.txt\n```", "never", mode="ask")
    s.submit("go")
    for _ in range(200):
        if s.pending:
            break
        s.worker.join(0.02)
    s.key("\x1b")
    finish(s)
    assert not (tmp_path / "a.txt").exists()
    assert any(i["t"] == "note" and "interrupted" in i["text"] for i in s.items)


# ---------- tool windows ----------

def window(s, item, w=60):
    return [flat(spans) for _, spans, _ in s.window(item, w)]


def test_tool_window_is_a_box_of_exact_width_with_tool_target_and_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\nseq 1 30\n```", "ok")
    s.submit("go")
    finish(s)
    box = window(s, s.items[1])
    assert all(len(x) == 60 for x in box)
    assert "shell" in box[0] and "seq 1 30" in box[0] and "done" in box[0]


def test_collapsed_window_shows_a_few_lines_and_how_many_are_hidden(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\nseq 1 30\n```", "ok")
    s.submit("go")
    finish(s)
    item = s.items[1]
    closed = window(s, item)
    assert len(closed) <= tui.PEEK + 2 and "+" in closed[-1] and "lines" in closed[-1]
    item["open"] = True
    opened = window(s, item)
    assert len(opened) == 31 + 2 and any("│ 30" in x for x in opened)


def test_ctrl_o_expands_then_collapses_every_window(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\necho a\n```", "```shell\necho b\n```", "ok")
    s.submit("go")
    finish(s)
    tools = [i for i in s.items if i["t"] == "tool"]
    s.key("\x0f")
    assert all(i["open"] for i in tools)
    s.key("\x0f")
    assert not any(i["open"] for i in tools)


def test_edit_window_shows_a_diff_and_write_flags_a_replaced_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    s = screen("```edit a.py\nx = 1\n====\nx = 2\n```", "```write a.py\nx = 3\n```", "ok")
    s.submit("go")
    finish(s)
    edit, write = [i for i in s.items if i["t"] == "tool"]
    lines = s.window(edit, 60)
    assert any(("- x = 1", "del") in spans for _, spans, _ in lines) and any(("+ x = 2", "add") in spans for _, spans, _ in lines)
    assert "replaces" in flat(s.window(write, 60)[0][1])


# ---------- input line ----------

def typed(s, text):
    for ch in text:
        s.key(ch)


def test_typing_editing_and_history():
    s = screen("one", "two")
    typed(s, "helo")
    s.key(curses.KEY_LEFT)
    s.key("l")
    assert s.buf == "hello" and s.cur == 4
    s.key(curses.KEY_BACKSPACE)
    assert s.buf == "helo"
    s.key("\x15")
    assert s.buf == ""
    typed(s, "first")
    s.key("\n")
    finish(s)
    assert s.buf == "" and s.items[0] == {"t": "user", "text": "first"}
    s.key(curses.KEY_UP)
    assert s.buf == "first"


def test_slash_commands_autocomplete_and_tab_fills_them_in():
    s = screen()
    typed(s, "/mo")
    assert [o for o, _ in s.options()] == ["/model", "/mode", "/mouse"]
    s.key(curses.KEY_DOWN)
    s.key("\t")
    assert s.buf == "/mode " and s.agent.mode == "auto"
    assert [o for o, _ in s.options()] == ["ask", "edits", "auto"]
    typed(s, "e")
    assert [o for o, _ in s.options()] == ["edits"]
    s.key("\n")
    assert s.agent.mode == "edits" and s.buf == ""


def test_tab_cycles_the_mode_when_nothing_is_being_completed():
    s = screen(mode="ask")
    s.key("\t")
    assert s.agent.mode == "edits"
    s.key(curses.KEY_BTAB)
    assert s.agent.mode == "auto"


def test_model_names_complete_after_slash_model():
    s = screen()
    s.models = ["qwen3.5:0.8b", "smollm2:135m"]
    typed(s, "/model sm")
    assert [o for o, _ in s.options()] == ["smollm2:135m"]
    s.key("\n")
    assert s.agent.model == "smollm2:135m"


def test_new_clears_the_conversation():
    s = screen("hi")
    s.submit("hello")
    finish(s)
    s.submit("/new")
    assert s.items == [] and len(s.agent.msgs) == 1


def test_lines_cover_every_item_and_fit_the_width(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("# Plan\nI will run it.\n```shell\necho hi\n```", "Done with `echo`.")
    s.submit("please run echo")
    finish(s)
    lines = s.lines(50)
    text = "\n".join(flat(spans) for _, spans, _ in lines)
    assert all(len(flat(spans)) <= 50 for _, spans, _ in lines)
    assert "please run echo" in text and "Plan" in text and "$ echo hi" in text and "Done with" in text


def letters(rows):
    return ["".join(t for t, _ in row) for row in rows]


def test_logo_is_five_equal_rows_of_shade_characters():
    still = letters(tui.logo_rows())
    assert len(still) == 5 and len({len(r) for r in still}) == 1 and len(still[0]) <= 82
    assert set("".join(still)) <= set(" ░▒▓█")


def test_each_logo_shade_has_its_own_colour():
    for rows in (tui.logo_rows(), tui.logo_rows(3.0)):
        seen = {text[0]: role for row in rows for text, role in row if text.strip()}
        assert seen == {"░": "yellow", "▒": "cyan", "▓": "magenta", "█": "bold"}


def test_logo_held_still_goes_from_light_shade_to_solid():
    top = letters(tui.logo_rows())[0].replace(" ", "")
    assert "".join(dict.fromkeys(top)) == "░▒▓█" and top[0] == "░" and top[-1] == "█"


def test_wave_moves_smoothly_one_shade_step_at_a_time():
    level = " ░▒▓█".index
    frames = [letters(tui.logo_rows(t * 0.08)) for t in range(400)]  # 32 seconds at the screen's redraw rate
    for before, after in zip(frames, frames[1:]):
        assert all(abs(level(a) - level(b)) <= 1 for ra, rb in zip(before, after) for a, b in zip(ra, rb))
    assert len({tuple(f) for f in frames}) == tui.LOGO_W  # it really moves: one new picture per column the crest crosses
    first = [f[0].split()[0][0] for f in frames]
    assert "█" in first and "░" in first  # every letter gets its turn at solid and at faint


def test_wave_is_slow():
    assert tui.WAVE_SECONDS >= 10
    start = letters(tui.logo_rows(0.0))
    assert letters(tui.logo_rows(tui.WAVE_SECONDS)) == start and letters(tui.logo_rows(tui.WAVE_SECONDS / 2)) != start


def test_motion_can_be_switched_off_in_settings():
    import sprint
    s = screen()
    assert s.motion is True
    s.submit("/config motion off")
    assert s.motion is False and sprint.load_config()["motion"] is False


def test_window_header_says_what_changed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```write a.py\nx = 1\ny = 2\n```", "```edit a.py\nx = 1\n====\nx = 5\nz = 0\n```", "ok")
    s.submit("go")
    finish(s)
    write, edit = [i for i in s.items if i["t"] == "tool"]
    assert "new file" in flat(s.window(write, 70)[0][1])
    assert "+2 -1" in flat(s.window(edit, 70)[0][1])


def test_user_message_leads_with_the_trail_and_prose_is_indented():
    s = screen("Sure thing.")
    s.submit("hello there")
    finish(s)
    lines = s.lines(40)
    assert lines[0][0] == "user" and flat(lines[0][1]) == "░▒▓█ hello there"
    assert "┃" not in "".join(flat(spans) for _, spans, _ in lines)
    assert any(flat(spans) == "  Sure thing." for _, spans, _ in lines)


# ---------- more commands ----------

def test_sidebar_command_toggles_and_is_remembered():
    import sprint
    s = screen()
    assert s.sidebar is True
    s.submit("/sidebar")
    assert s.sidebar is False and sprint.load_config()["sidebar"] is False
    assert Screen(Agent(chat=scripted(), out=io.StringIO())).sidebar is False


def test_context_command_shows_the_coloured_grid_in_a_popup():
    s = screen("hi")
    s.submit("hello")
    finish(s)
    s.submit("/context")
    lines = s.popup_lines(80)[0]
    text = "\n".join(flat(spans) for spans in lines)
    assert "system prompt" in text and "free" in text and "tokens in use" in text
    assert {"cyan", "blue", "green"} <= {role for spans in lines for _, role in spans}
    assert all(i["t"] != "rows" for i in s.items)


def test_thinking_shows_up_as_its_own_window_before_the_answer():
    s = screen(["<think>weigh it up\nthen decide</think>", "Four."])
    s.submit("/think")
    assert s.agent.think is True
    s.submit("2+2")
    finish(s)
    assert [i["t"] for i in s.items][-2:] == ["tool", "text"]
    thought = s.items[-2]
    assert thought["call"].tool == "think" and "weigh it up" in thought["result"]
    head = flat(s.window(thought, 60)[0][1])
    assert "think" in head and "5 words" in head


def test_new_commands_complete_with_their_arguments():
    s = screen()
    typed(s, "/")
    assert {"/sidebar", "/think", "/effort", "/context", "/permissions", "/config", "/model", "/tools", "/mouse"} <= {o for o, _ in s.options()}
    s.buf = "/effort "
    assert [o for o, _ in s.options()] == ["low", "medium", "high"]
    s.buf = "/permissions "
    assert [o for o, _ in s.options()] == ["ask", "edits", "auto"]
    s.buf = "/config c"
    assert [o for o, _ in s.options()] == ["ctx"]


def test_tools_command_expands_and_collapses_like_ctrl_o(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\necho a\n```", "ok")
    s.submit("go")
    finish(s)
    s.submit("/tools")
    assert all(i["open"] for i in s.items if i["t"] == "tool")
    s.submit("/tools")
    assert not any(i["open"] for i in s.items if i["t"] == "tool")


def test_effort_and_permissions_commands_reach_the_agent():
    s = screen(mode="ask")
    s.submit("/effort high")
    s.submit("/permissions edits")
    assert s.agent.effort == "high" and s.agent.mode == "edits"
    assert s.items == [] and s.popup is None


# ---------- three panels: message list, conversation, sidebar ----------

PRESS, RELEASE, MOVE = curses.BUTTON1_PRESSED, curses.BUTTON1_RELEASED, curses.REPORT_MOUSE_POSITION


def test_outline_lists_your_messages_with_what_each_one_took(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```write a.txt\nhi\n```", "```shell\ncat a.txt\n```", "done", "Sure.")
    s.submit("make a.txt and show it")
    finish(s)
    s.submit("thanks")
    finish(s)
    rows = s.outline()
    assert [(flat(title), flat(meta)) for _, title, meta in rows] == [("1 make a.txt and show it", "  2 steps · 1 file"), ("2 thanks", "  no tools")]
    assert rows[0][0] is s.items[0]


def test_activity_logs_each_tool_call_with_its_outcome(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```write a.txt\nhi\nho\n```", "```shell\nexit 2\n```", "```edit a.txt\nhi\n====\nhey\n```", "ok")
    s.submit("go")
    finish(s)
    log = [flat(spans) for _, spans in s.activity()]
    assert log == ["✓ write a.txt +2", "✗ shell exit 2", "✓ edit a.txt +1 -1"]
    assert [it["call"].tool for it, _ in s.activity()] == ["write", "shell", "edit"]


def test_dragging_the_right_divider_resizes_the_sidebar_and_saves_it():
    import sprint
    s = screen()
    s.geo, s.side_w = (120, 26, 32), 32
    s.pointer(87, 5, PRESS)
    assert s.drag == "right"
    s.pointer(69, 9, MOVE)
    assert s.side_w == 50
    s.pointer(200, 9, MOVE)
    assert s.side_w == 20  # never narrower than this
    s.pointer(79, 9, RELEASE)
    assert s.drag is None and s.side_w == 40 and sprint.load_config()["sidebar_w"] == 40


def test_dragging_the_left_divider_resizes_the_message_list():
    import sprint
    s = screen()
    s.geo = (120, 26, 32)
    s.pointer(26, 3, PRESS)
    assert s.drag == "left"
    s.pointer(34, 3, MOVE)
    s.pointer(34, 3, RELEASE)
    assert s.left_w == 34 and sprint.load_config()["history_w"] == 34


def test_clicking_a_message_or_an_activity_line_jumps_to_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\necho a\n```", "ok")
    s.submit("go")
    finish(s)
    s.geo = (120, 26, 32)
    user, tool = s.items[0], s.items[1]
    s.hits = {("left", 3): user, ("side", 12): tool, ("main", 8): tool}
    s.pointer(5, 3, PRESS)
    s.pointer(5, 3, RELEASE)
    assert s.jump is user
    s.pointer(100, 12, PRESS)
    s.pointer(100, 12, RELEASE)
    assert s.jump is tool
    s.pointer(50, 8, PRESS)
    s.pointer(50, 8, RELEASE)  # a click is a press and release in the same place
    assert tool["open"] is True and s.drag is None


def test_history_and_sidebar_commands_toggle_and_size_the_panels():
    import sprint
    s = screen()
    assert s.history is True
    s.submit("/history")
    assert s.history is False and sprint.load_config()["history"] is False
    s.submit("/history 30")
    assert s.history is True and s.left_w == 30
    s.submit("/sidebar 44")
    assert s.sidebar is True and s.side_w == 44 and sprint.load_config()["sidebar_w"] == 44


def test_every_row_of_a_user_message_knows_its_item():
    s = screen("ok")
    s.submit("hello")
    finish(s)
    assert all(item is s.items[0] for kind, _, item in s.lines(40) if kind == "user")



# ---------- the name, and an input area of its own ----------

def test_the_name_is_sprint():
    import inspect
    assert tui.WORD == "sprint" and tui.LOGO_W == 60
    assert "sprinter" not in inspect.getsource(tui)


def test_input_area_is_a_titled_rule_and_a_trail_prompt_not_a_box():
    s = screen(mode="ask")
    s.buf = "make it so"
    rows, typed = s.input_rows(60, used=7)
    text = [flat(r) for r in rows]
    assert len(rows) == 2 and len(text[0]) == 60
    assert "ask" in text[0] and "Qwen3.5-0.8B" in text[0] and "7%" in text[0]
    assert text[1].startswith("░▒▓█ make it so") and "┃" not in "".join(text)


def test_empty_input_shows_a_hint_and_long_input_wraps_under_the_text():
    s = screen()
    rows, _ = s.input_rows(40)
    assert "Ask anything" in flat(rows[1])
    s.buf = "x" * 50
    rows, typed = s.input_rows(40)
    assert len(typed) == 2 and flat(rows[2]).startswith("     x")


def test_permission_prompt_names_the_call_and_the_keys(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\necho 1\n```", "ok", mode="ask")
    s.submit("go")
    for _ in range(200):
        if s.pending:
            break
        s.worker.join(0.02)
    text = "\n".join(flat(r) for r in s.input_rows(70)[0])
    assert "allow" in text and "shell" in text and "echo 1" in text and "always" in text and "┃" not in text
    s.key("n")
    finish(s)


def test_trail_ripples_only_while_working(monkeypatch):
    s = screen()
    assert flat(s.trail()) == "░▒▓█"
    monkeypatch.setattr(s, "busy", lambda: True)
    seen = set()
    for tick in range(8):
        monkeypatch.setattr(tui.time, "time", lambda tick=tick: tick / 8)
        seen.add(flat(s.trail()))
    assert len(seen) == 4 and all(sorted(x) == sorted("░▒▓█") for x in seen)
    s.motion = False
    assert flat(s.trail()) == "░▒▓█"



# ---------- one kind of popup for everything a command brings up ----------

import json

from test_sprint import FakeResponse

TAGS = {"models": [
    {"name": "qwen3.5:0.8b", "size": 737_000_000, "details": {"parameter_size": "752M", "quantization_level": "Q4_K_M"}},
    {"name": "smollm2:135m", "size": 92_000_000, "details": {"parameter_size": "135M", "quantization_level": "Q4_0"}}]}


def net(monkeypatch, routes):
    """fake network: routes are (part of the URL, JSON-able payload or raw bytes); returns the requests made"""
    seen = []

    def urlopen(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        seen.append((url, None if isinstance(req, str) or req.data is None else json.loads(req.data)))
        for part, payload in routes:
            if part in url:
                return FakeResponse(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        raise OSError("no route to " + url)

    monkeypatch.setattr(tui.urllib.request, "urlopen", urlopen)
    return seen


def popup_text(s, w=70):
    return [flat(spans) for spans in s.popup_lines(w)[0]]


def test_model_command_opens_a_picker_instead_of_printing(monkeypatch):
    net(monkeypatch, [("/api/tags", TAGS)])
    s = screen()
    s.submit("/model")
    text = popup_text(s)
    assert s.items == [] and s.popup["title"] == "Model"
    assert any("qwen3.5:0.8b" in t and "752M" in t and "737 MB" in t for t in text) and any("smollm2:135m" in t for t in text)
    assert any("Hugging Face" in t for t in text) and any("by name" in t for t in text)
    assert text[0].startswith("╭─ Model") and text[-1].startswith("╰─") and "esc" in text[-1] and all(len(t) == 70 for t in text)
    s.key(curses.KEY_DOWN)
    s.key("\n")
    assert s.agent.model == "smollm2:135m" and s.popup is None and s.items == []


def test_typing_in_the_picker_filters_the_list_but_keeps_the_pull_options(monkeypatch):
    net(monkeypatch, [("/api/tags", TAGS)])
    s = screen()
    s.submit("/model")
    typed(s, "smol")
    text = popup_text(s)
    assert any("smollm2" in t for t in text) and not any("qwen3.5:0.8b" in t for t in text)
    assert any("Hugging Face" in t for t in text)
    s.key("\n")
    assert s.agent.model == "smollm2:135m"


def test_escape_closes_a_popup_without_interrupting_anything(monkeypatch):
    net(monkeypatch, [("/api/tags", TAGS)])
    s = screen()
    s.submit("/model")
    s.key("\x1b")
    assert s.popup is None and s.agent.cancel is False and s.quit is False


def test_pull_by_name_downloads_then_switches_to_the_model(monkeypatch):
    stream = "\n".join(json.dumps(x) for x in [{"status": "pulling manifest"}, {"status": "pulling 1a2b", "total": 100, "completed": 40},
                                                {"status": "success"}]).encode()
    seen = net(monkeypatch, [("/api/tags", TAGS), ("/api/pull", stream)])
    s = screen()
    s.submit("/model")
    s.key(curses.KEY_UP)  # from the first row up wraps to the last: pull by name
    s.key("\n")
    assert s.popup["field"] is not None
    typed(s, "gemma3:270m")
    s.key("\n")
    s.job.join(5)
    assert ("http://localhost:11434/api/pull", {"model": "gemma3:270m", "stream": True}) in seen
    assert s.agent.model == "gemma3:270m" and s.popup is None and "gemma3:270m" in s.note_text()


def test_pull_from_hugging_face_searches_then_offers_quantizations(monkeypatch):
    hits = [{"id": "unsloth/Qwen3.5-0.8B-GGUF", "downloads": 338302}, {"id": "bartowski/Qwen_Qwen3.5-0.8B-GGUF", "downloads": 39003}]
    files = [{"path": "Qwen3.5-0.8B-Q8_0.gguf", "size": 812_000_000}, {"path": "Qwen3.5-0.8B-Q4_K_M.gguf", "size": 533_000_000},
             {"path": "mmproj-F16.gguf", "size": 205_000_000}, {"path": "README.md", "size": 10}]
    seen = net(monkeypatch, [("/api/tags", TAGS), ("huggingface.co/api/models?", hits), ("/tree/main", files),
                             ("/api/pull", json.dumps({"status": "success"}).encode())])
    s = screen()
    s.submit("/model")
    s.key(curses.KEY_UP)
    s.key(curses.KEY_UP)  # the row above pull-by-name: Hugging Face
    s.key("\n")
    typed(s, "qwen3.5 0.8b")
    s.key("\n")
    s.job.join(5)
    assert any("unsloth/Qwen3.5-0.8B-GGUF" in t and "338" in t for t in popup_text(s))
    assert any("search=qwen3.5+0.8b" in url and "filter=gguf" in url for url, _ in seen)
    s.key("\n")
    s.job.join(5)
    text = popup_text(s)
    assert any("Q4_K_M" in t and "533 MB" in t for t in text) and any("Q8_0" in t for t in text) and not any("mmproj" in t for t in text)
    s.key("\n")
    s.job.join(5)
    name = "hf.co/unsloth/Qwen3.5-0.8B-GGUF:Q4_K_M"
    assert ("http://localhost:11434/api/pull", {"model": name, "stream": True}) in seen and s.agent.model == name


def test_a_failed_pull_says_why_in_the_popup(monkeypatch):
    net(monkeypatch, [("/api/tags", TAGS), ("/api/pull", json.dumps({"error": "pull model manifest: file does not exist"}).encode())])
    s = screen()
    before = s.agent.model
    s.pull("nope:1b")
    s.job.join(5)
    assert s.agent.model == before and any("file does not exist" in t for t in popup_text(s))


def test_every_command_that_shows_something_uses_the_same_popup():
    s = screen("hi", mode="ask")
    for cmd, title in (("/mode", "Mode"), ("/permissions", "Permissions"), ("/effort", "Effort"), ("/context", "Context"),
                       ("/config", "Settings"), ("/help", "Help")):
        s.submit(cmd)
        text = popup_text(s, 76)
        assert s.popup["title"] == title and text[0].startswith("╭─ " + title) and text[-1].startswith("╰─"), cmd
        assert all(len(t) == 76 for t in text) and "esc" in text[-1], cmd
        s.key("\x1b")
        assert s.popup is None
    assert s.items == []


def test_choosing_in_the_mode_and_effort_popups_applies_it():
    s = screen(mode="ask")
    s.submit("/mode")
    s.key(curses.KEY_DOWN)
    s.key("\n")
    assert s.agent.mode == "edits" and s.popup is None
    s.submit("/effort")
    s.key(curses.KEY_DOWN)  # it opens on the current level, medium
    s.key("\n")
    assert s.agent.effort == "high" and s.popup is None


def test_settings_popup_flips_switches_and_edits_values_in_place():
    import sprint
    s = screen()
    s.submit("/config")
    for _ in range(5):
        s.key(curses.KEY_DOWN)  # model, url, ctx, steps, mode -> think
    s.key("\n")
    assert s.agent.think is True and s.popup["title"] == "Settings"
    for _ in range(3):
        s.key(curses.KEY_UP)  # back up to ctx
    s.key("\n")
    assert s.popup["field"] is not None and s.popup["field"]["text"] == "8192"
    for _ in range(4):
        s.key(curses.KEY_BACKSPACE)
    typed(s, "4096")
    s.key("\n")
    assert s.agent.ctx == 4096 and sprint.load_config()["ctx"] == 4096 and s.popup["title"] == "Settings"


def test_switches_answer_with_a_brief_note_not_a_conversation_entry():
    s = screen()
    s.submit("/think")
    assert s.items == [] and "thinking on" in s.note_text()
    s.submit("/effort low")
    assert s.items == [] and "low" in s.note_text()
    s.submit("/nonsense")
    assert s.popup["title"] == "Help"


# ---------- customising: system prompt editor, themes, more settings ----------

def test_systemprompt_opens_an_editor_holding_the_current_prompt():
    import sprint
    s = screen()
    s.submit("/systemprompt")
    assert s.popup["title"] == "System prompt" and s.popup["edit"]["lines"][0].startswith("You are a coding agent")
    text = popup_text(s, 76)
    assert any("coding agent" in t for t in text) and "save" in text[-1] and all(len(t) == 76 for t in text)
    assert sprint.load_prompt() == ""


def test_editing_the_prompt_and_saving_applies_from_the_next_message():
    import sprint
    chat = scripted("ok")
    s = Screen(Agent(chat=chat, out=io.StringIO(), mode="auto"))
    s.submit("/systemprompt")
    s.key("\x15")            # ctrl+u clears the line the cursor is on
    typed(s, "You are terse.")
    s.key("\x13")            # ctrl+s
    assert s.popup is None and s.agent.custom.startswith("You are terse.") and sprint.load_prompt().startswith("You are terse.")
    s.submit("hi")
    finish(s)
    assert chat.seen[0][0]["content"].startswith("You are terse.")


def test_editor_keys_split_join_and_move():
    s = screen()
    s.submit("/systemprompt")
    e = s.popup["edit"]
    e["lines"], e["row"], e["col"] = ["hello world"], 0, 5
    s.key("\n")
    assert e["lines"] == ["hello", " world"] and (e["row"], e["col"]) == (1, 0)
    s.key(curses.KEY_BACKSPACE)
    assert e["lines"] == ["hello world"] and (e["row"], e["col"]) == (0, 5)
    s.key(curses.KEY_RIGHT)
    s.key("!")
    assert e["lines"] == ["hello !world"]
    s.key(curses.KEY_HOME)
    s.key(curses.KEY_DC)
    assert e["lines"] == ["ello !world"]
    s.key("\x1b")
    assert s.popup is None and s.agent.custom == ""


def test_ctrl_r_brings_back_the_default_prompt_and_saving_it_clears_the_custom_one():
    import sprint
    s = screen()
    sprint.save_prompt("Be odd.")
    s.agent.custom = "Be odd."
    s.submit("/systemprompt")
    assert s.popup["edit"]["lines"] == ["Be odd."]
    s.key("\x12")            # ctrl+r
    assert s.popup["edit"]["lines"][0].startswith("You are a coding agent")
    s.key("\x13")
    assert s.agent.custom == "" and sprint.load_prompt() == ""


def test_theme_popup_previews_as_you_move_and_esc_puts_it_back():
    import sprint
    s = screen()
    s.submit("/theme")
    names = [n for n in tui.THEMES]
    assert s.popup["title"] == "Theme" and names[0] == "default" and len(names) >= 5 and "mono" in names
    s.key(curses.KEY_DOWN)
    assert s.theme == names[1]
    s.key("\x1b")
    assert s.theme == "default" and "theme" not in sprint.load_config()
    s.submit("/theme")
    s.key(curses.KEY_DOWN)
    s.key("\n")
    assert s.theme == names[1] and sprint.load_config()["theme"] == names[1] and s.popup is None


def test_an_unknown_theme_in_the_settings_file_falls_back_to_default():
    import sprint
    sprint.save_config(theme="nope")
    assert screen().theme == "default"


def test_settings_popup_offers_theme_peek_and_timeout(tmp_path, monkeypatch):
    import sprint
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\nseq 1 30\n```", "ok")
    s.submit("go")
    finish(s)
    s.submit("/config")
    keys = [flat(r["s"]).split()[0] for r in s.popup["rows"]]
    assert {"theme", "peek", "timeout"} <= set(keys)
    s.popup["sel"] = keys.index("peek")
    s.key("\n")
    s.key(curses.KEY_BACKSPACE)
    typed(s, "3")
    s.key("\n")
    s.key("\x1b")
    assert s.peek == 3 and sprint.load_config()["peek"] == 3
    assert len(s.window(s.items[1], 60)) == 3 + 2


# ---------- @file and !command ----------

def test_typing_at_offers_matching_files_and_tab_fills_one_in(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "notebook.py").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "notes").write_text("x")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "notes.js").write_text("x")
    s = screen("ok")
    typed(s, "what is in @not")
    assert [o for o, _ in s.options()] == ["@notes.txt", "@src/notebook.py"]
    s.key("\t")
    assert s.buf == "what is in @notes.txt " and s.options() == []
    typed(s, "and @src/n")
    s.key("\n")  # enter picks the file; it does not send yet
    assert s.buf == "what is in @notes.txt and @src/notebook.py " and s.items == []
    s.key("\n")
    finish(s)
    assert s.items[0] == {"t": "user", "text": "what is in @notes.txt and @src/notebook.py"}


def test_a_message_ending_in_a_space_still_sends():
    s = screen("ok")
    typed(s, "hello ")
    s.key("\n")
    finish(s)
    assert s.items[0]["text"] == "hello"


def test_bang_runs_the_command_in_a_tool_window_without_the_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("It said hi.", mode="ask")
    typed(s, "!echo hi")
    s.key("\n")
    finish(s)
    assert [i["t"] for i in s.items] == ["user", "tool"] and s.items[0]["text"] == "!echo hi"
    assert s.items[1]["state"] == "done" and s.items[1]["result"] == "hi" and s.pending is None
    typed(s, "what did it say?")
    s.key("\n")
    finish(s)
    assert "$ echo hi\nhi" in s.agent.msgs[1]["content"] and s.items[-1]["text"].strip() == "It said hi."



# ---------- speed, allow list, pasted files, race, plan mode ----------

def test_sidebar_has_a_speed_line_once_the_model_has_replied():
    s = screen(["hi", {"out": 50, "secs": 2.0}])
    assert s.speed_text() == ""
    s.submit("go")
    finish(s)
    assert "25 tok/s" in s.speed_text()


def test_permissions_popup_lists_rules_and_lets_you_remove_and_add_them():
    import sprint
    s = screen(mode="ask")
    s.agent.allow = ["shell git status*"]
    s.submit("/permissions")
    text = popup_text(s, 76)
    assert any("git status*" in t for t in text) and any("Add a rule" in t for t in text)
    s.popup["sel"] = 3  # after the three modes: the rule
    s.key("\n")
    assert s.agent.allow == [] and s.popup["title"] == "Permissions"
    s.popup["sel"] = 3  # now that is "Add a rule"
    s.key("\n")
    typed(s, "write notes/*")
    s.key("\n")
    assert s.agent.allow == ["write notes/*"] and sprint.load_config()["allow"] == ["write notes/*"]


def test_pasting_file_paths_makes_numbered_chips(tmp_path):
    for name in ("a.png", "b.jpg", "doc.pdf", "my notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    s = screen()
    s.paste(str(tmp_path / "a.png"))
    s.paste(f"'{tmp_path / 'b.jpg'}'")
    s.paste(str(tmp_path / "doc.pdf"))
    s.paste(str(tmp_path / "my notes.txt").replace(" ", "\\ "))
    assert s.buf == "[Image #1] [Image #2] [PDF #1] [File #1] "
    assert s.chips["[Image #2]"].endswith("b.jpg") and s.chips["[File #1]"].endswith("my notes.txt")
    s.paste("just some\nwords")
    assert s.buf.endswith("[File #1] just some words")


def test_a_pasted_image_goes_with_the_message_and_backspace_removes_a_whole_chip(tmp_path):
    (tmp_path / "a.png").write_bytes(b"png")
    chat = scripted("A cat.")
    s = Screen(Agent(chat=chat, out=io.StringIO(), mode="auto"))
    s.paste(str(tmp_path / "a.png"))
    s.paste(str(tmp_path / "a.png"))
    assert s.buf == "[Image #1] [Image #2] "
    s.key(curses.KEY_BACKSPACE)  # the space
    s.key(curses.KEY_BACKSPACE)  # the whole chip
    assert s.buf == "[Image #1] "
    typed(s, "what is this?")
    s.key("\n")
    finish(s)
    assert chat.seen[0][1]["images"] == ["cG5n"] and s.items[0]["text"] == "[Image #1] what is this?" and s.chips == {}


def test_a_terminal_paste_is_collected_whole_and_a_lone_escape_still_works():
    s = screen()
    for k in ["\x1b", "[", "2", "0", "0", "~", "h", "i", "\n", "!", "\x1b", "[", "2", "0", "1", "~"]:
        s.take(k)
    assert s.buf == "hi !" and s.items == []  # the newline inside a paste does not send
    s.submit("/help")
    s.take("\x1b")
    assert s.popup is not None  # held until it is clear this is not the start of a paste
    s.idle()
    assert s.popup is None
    s.take("\x1b")
    s.take("x")
    assert s.buf == "hi !x"


def test_race_shows_both_answers_side_by_side_and_can_switch_model(monkeypatch):
    net(monkeypatch, [("/api/tags", TAGS)])
    chat = scripted(["Alpha says hi.", {"out": 40, "secs": 2.0}], ["Beta says yo.", {"out": 90, "secs": 1.0}])
    s = Screen(Agent(chat=chat, out=io.StringIO(), mode="auto"))
    s.agent.model = "qwen3.5:0.8b"
    s.submit("/race")
    assert s.popup["title"] == "Race against" and not any("qwen3.5:0.8b" in t for t in popup_text(s))
    s.key("\n")  # the only rival: smollm2
    assert s.popup["field"] is not None
    typed(s, "say hi")
    s.key("\n")
    s.job.join(5)
    text = "\n".join(popup_text(s, 78))
    assert s.popup["title"] == "Race" and "Alpha says hi." in text and "Beta says yo." in text
    assert "20 tok/s" in text and "90 tok/s" in text and s.items == []
    s.key(curses.KEY_DOWN)
    s.key("\n")  # the second choice: switch to the rival
    assert s.agent.model == "smollm2:135m" and s.popup is None


def test_plan_mode_is_a_toggle_and_shows_its_steps_as_a_checklist(tmp_path, monkeypatch):
    import sprint
    monkeypatch.chdir(tmp_path)
    s = screen("1. Make the file\n2. Show it", "```write a.txt\nhi\n```", "Made.", "```shell\ncat a.txt\n```", "Shown.")
    assert s.plan is False
    s.submit("/plan")
    assert s.plan is True and sprint.load_config()["plan"] is True and "plan" in s.note_text()
    s.submit("make a.txt and show it")
    finish(s)
    plans = [i for i in s.items if i["t"] == "plan"]
    assert len(plans) == 1 and plans[0]["steps"] == ["Make the file", "Show it"] and plans[0]["at"] == 2
    text = "\n".join(flat(spans) for _, spans, _ in s.lines(60))
    assert "✓ Make the file" in text and "✓ Show it" in text and s.items[-1]["text"].strip() == "Shown."


# ---------- /directory ----------

def test_directory_popup_browses_folders_and_only_moves_when_you_say_so(tmp_path, monkeypatch):
    import os
    monkeypatch.chdir(tmp_path)
    for name in ("app/src", "docs", ".git"):
        (tmp_path / name).mkdir(parents=True)
    s = screen()
    s.submit("/directory")
    text = popup_text(s, 76)
    assert s.popup["title"] == "Directory" and any("work here" in t for t in text)
    assert any("app/" in t for t in text) and any("docs/" in t for t in text) and not any(".git" in t for t in text)
    s.key(curses.KEY_DOWN)   # ../
    s.key(curses.KEY_DOWN)   # app/
    s.key("\n")
    assert any("src/" in t for t in popup_text(s, 76)) and os.path.realpath(os.getcwd()) == os.path.realpath(tmp_path)
    s.key("\n")              # the first row of the new view: work here
    assert os.path.realpath(os.getcwd()) == os.path.realpath(tmp_path / "app") and s.popup is None
    assert "working in" in s.note_text() and s.items == []
    s.submit("/directory")
    assert any("src/" in t for t in popup_text(s, 76))  # it now starts from the new folder
    s.key("\x1b")
    s.submit("/directory " + str(tmp_path / "docs"))
    assert os.path.realpath(os.getcwd()) == os.path.realpath(tmp_path / "docs") and s.popup is None
    s.submit("/directory")
    rows = [flat(r["s"]) for r in s.popup["rows"]]
    assert "recent" in rows and any(r.strip().endswith("app") for r in rows)  # where you were is one keypress away


def test_directory_does_not_change_while_the_agent_is_working(tmp_path, monkeypatch):
    import os
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app").mkdir()
    s = screen()
    monkeypatch.setattr(s, "busy", lambda: True)
    s.submit("/directory app")
    assert os.path.realpath(os.getcwd()) == os.path.realpath(tmp_path) and "finish" in s.note_text()



# ---------- drag to copy, escape, highlighted messages ----------

def test_dragging_over_the_conversation_copies_that_text(monkeypatch):
    import sprint
    held = []
    monkeypatch.setattr(sprint, "clipboard", lambda text: held.append(text) or True)
    s = screen()
    s.geo = (100, 0, 0)
    s.screen = {4: (2, "  Here is the plan:"), 5: (2, "│ $ python3 hello.py        │"), 6: (2, "│ hi                        │")}
    s.pointer(4, 4, PRESS)
    assert s.select is not None
    s.pointer(9, 6, MOVE)
    assert s.select["b"] == (6, 9)
    s.pointer(9, 6, RELEASE)
    assert held == ["Here is the plan:\n$ python3 hello.py\nhi"] and s.select is None and "copied" in s.note_text()


def test_a_drag_within_one_line_copies_just_that_stretch(monkeypatch):
    import sprint
    held = []
    monkeypatch.setattr(sprint, "clipboard", lambda text: held.append(text) or True)
    s = screen()
    s.geo = (100, 0, 0)
    s.screen = {4: (2, "  run pytest -q to check")}
    s.pointer(8, 4, PRESS)
    s.pointer(16, 4, MOVE)
    s.pointer(16, 4, RELEASE)
    assert held == ["pytest -q"]


def test_escape_closes_the_connection_so_a_silent_model_stops_at_once():
    import threading
    waiting, shut = threading.Event(), threading.Event()

    class Sock:
        def shutdown(self, how):
            shut.set()

    def chat(msgs):
        import sprint
        sprint.SOCKETS[threading.get_ident()] = Sock()  # what a real request records once it has connected
        waiting.set()
        shut.wait(5)  # like a model writing a long tool call: nothing arrives until it is done
        yield "too late"

    a = Agent(chat=chat, out=io.StringIO(), mode="auto")
    s = Screen(a)
    s.submit("write something long")
    assert waiting.wait(5)
    s.take("\x1b")
    s.idle()
    finish(s)
    assert shut.is_set() and any(i["t"] == "note" and "interrupted" in i["text"] for i in s.items)
    assert not any(i["t"] == "text" and "too late" in i["text"] for i in s.items)


def test_user_messages_are_marked_for_highlighting():
    s = screen("ok")
    s.submit("hello there")
    finish(s)
    kinds = [kind for kind, _, _ in s.lines(40)]
    assert kinds[0] == "user" and "user" not in kinds[1:]



# ---------- /clear, and not redrawing when nothing changes ----------

def test_clear_is_a_listed_command_and_ctrl_l_does_the_same():
    s = screen("hi", "hello again")
    typed(s, "/cl")
    assert [o for o, _ in s.options()] == ["/clear"]
    s.key("\x15")
    s.submit("hello")
    finish(s)
    s.submit("/clear")
    assert s.items == [] and len(s.agent.msgs) == 1
    s.submit("hello")
    finish(s)
    s.key("\x0c")
    assert s.items == [] and len(s.agent.msgs) == 1


def test_the_screen_only_counts_as_active_when_something_is_changing():
    s = screen("ok")
    assert s.active() is True  # the opening screen's wave
    s.motion = False
    assert s.active() is False
    s.motion = True
    s.submit("hi")
    finish(s)
    assert s.active() is False  # a finished conversation just sits there
    s.tell("a note")
    assert s.active() is True  # until the note fades



def test_an_image_path_typed_or_dropped_into_a_message_becomes_a_chip_when_sent(tmp_path):
    (tmp_path / "shot one.png").write_bytes(b"png")
    (tmp_path / "notes.txt").write_text("x")
    chat = scripted("A cat.")
    s = Screen(Agent(chat=chat, out=io.StringIO(), mode="auto"))
    s.submit(f'what is "{tmp_path / "shot one.png"}" and {tmp_path / "notes.txt"} and missing.png')
    finish(s)
    assert s.items[0]["text"] == f"what is [Image #1] and {tmp_path / 'notes.txt'} and missing.png"  # only pictures and PDFs
    assert chat.seen[0][1]["images"] == ["cG5n"]


def test_paths_in_your_own_commands_are_left_alone(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "shot.png").write_bytes(b"png")
    s = screen()
    s.submit(f"!ls {tmp_path / 'shot.png'}")
    finish(s)
    assert s.items[0]["text"] == f"!ls {tmp_path / 'shot.png'}" and s.chips == {}


# ---------- a large paste becomes a chip ----------

def test_a_large_paste_becomes_a_chip_and_a_small_one_stays_inline():
    s = screen()
    s.paste("one\ntwo\nthree\nfour\nfive")
    assert s.buf == "[Pasted #1 +5 lines] " and s.pastes["[Pasted #1 +5 lines]"] == "one\ntwo\nthree\nfour\nfive"
    s.paste("x" * 900)
    assert s.buf.endswith("[Pasted #2 900 chars] ")
    s.paste("just two\nlines")
    assert s.buf.endswith("[Pasted #2 900 chars] just two lines")


def test_the_model_gets_the_pasted_text_while_the_screen_keeps_the_chip():
    chat = scripted("It has five lines.")
    s = Screen(Agent(chat=chat, out=io.StringIO(), mode="auto"))
    typed(s, "how long is ")
    s.paste("one\ntwo\nthree\nfour\nfive")
    typed(s, "?")
    s.key("\n")
    finish(s)
    assert s.items[0]["text"] == "how long is [Pasted #1 +5 lines] ?" and s.pastes == {}
    sent = chat.seen[0][1]["content"]
    assert "one\ntwo\nthree\nfour\nfive" in sent and "[Pasted #1" not in sent and sent.startswith("how long is")


def test_backspace_removes_a_pasted_chip_whole_and_it_is_then_not_sent():
    chat = scripted("ok")
    s = Screen(Agent(chat=chat, out=io.StringIO(), mode="auto"))
    s.paste("one\ntwo\nthree\nfour\nfive")
    s.key(curses.KEY_BACKSPACE)
    s.key(curses.KEY_BACKSPACE)
    assert s.buf == ""
    typed(s, "hello")
    s.key("\n")
    finish(s)
    assert chat.seen[0][1]["content"] == "hello"


def test_a_huge_paste_is_trimmed_to_what_the_window_can_take():
    chat = scripted("ok")
    s = Screen(Agent(chat=chat, out=io.StringIO(), mode="auto"))
    s.paste("\n".join(f"row {i} " + "x" * 60 for i in range(2000)))
    s.key("\n")
    finish(s)
    sent = chat.seen[0][1]["content"]
    assert len(sent) < 10_000 and "row 0 " in sent and "row 1999 " in sent and "cut" in sent


# ---------- pastes the terminal does not mark (Windows), and pictures from the clipboard ----------

def test_keys_that_arrive_together_are_a_paste_and_its_line_breaks_do_not_send():
    s = screen()
    s.burst(list("first line\rsecond line\r"))
    assert s.worker is None and "first line second line" in s.buf  # nothing was sent; the text is on the prompt


def test_a_big_unmarked_paste_becomes_one_chip():
    s = screen()
    s.burst(list("\r".join(f"line {i}" for i in range(40))))
    assert s.buf == "[Pasted #1 +40 lines] " and s.pastes["[Pasted #1 +40 lines]"].count("\n") == 39


def test_a_few_typed_keys_are_still_keys_so_enter_sends():
    s = screen("ok")
    s.burst(list("hi") + ["\n"])
    finish(s)
    assert s.items[0] == {"t": "user", "text": "hi"}


def test_an_unmarked_paste_cannot_answer_an_approval(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\necho 1\n```", "ok", mode="ask")
    s.submit("go")
    for _ in range(200):
        if s.pending:
            break
        s.worker.join(0.02)
    s.burst(list("yes always\ry\r"))
    assert s.pending and not s.gate.is_set()
    s.key("n")
    finish(s)


def test_a_marked_paste_arriving_in_one_go_is_still_read_by_its_marks():
    s = screen()
    s.burst(list("\x1b[200~one\ntwo\x1b[201~"))
    assert s.buf == "one two" and s.worker is None


class Keys:
    """a stand-in for the curses window: hands out the keys it was given, then says there are none"""
    def __init__(self, *keys):
        self.keys, self.waits = list(keys), []

    def timeout(self, ms):
        self.waits.append(ms)

    def get_wch(self):
        if not self.keys:
            raise tui.curses.error("no input")
        return self.keys.pop(0)


def test_reading_keys_takes_everything_that_is_already_waiting():
    s = screen()
    assert s.read_keys(Keys("a", "b", "\r", "c")) == ["a", "b", "\r", "c"]
    assert s.read_keys(Keys("x")) == ["x"]
    with pytest.raises(tui.curses.error):
        s.read_keys(Keys())


def test_ctrl_v_puts_the_clipboard_picture_on_the_prompt_as_a_chip(tmp_path, monkeypatch):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG....")
    monkeypatch.setattr(sprint, "clipboard_image", lambda: str(shot))
    s = screen()
    s.key("\x16")
    assert s.buf == "[Image #1] " and s.chips == {"[Image #1]": str(shot)}


def test_alt_v_and_the_paste_command_do_the_same_and_say_so_when_there_is_no_picture(tmp_path, monkeypatch):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG....")
    monkeypatch.setattr(sprint, "clipboard_image", lambda: str(shot))
    s = screen()
    s.take("\x1b")
    s.take("v")
    s.submit("/paste")
    assert s.buf == "[Image #1] [Image #2] " and s.worker is None
    monkeypatch.setattr(sprint, "clipboard_image", lambda: "")
    s.key("\x16")
    assert "no picture" in s.note_text() and s.buf == "[Image #1] [Image #2] "
