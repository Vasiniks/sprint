import io
import json
import os
import time

import pytest

import sprint
from sprint import Agent, edit, fit, html_text, parse, read, shell, write


# ---------- parse: model reply -> tool call ----------

def test_parse_shell_block_after_prose():
    c = parse("I'll look around.\n```shell\nls -la\n```")
    assert (c.tool, c.body) == ("shell", "ls -la")


def test_parse_bash_fence_is_shell():
    assert parse("```bash\npwd\n```").tool == "shell"


def test_parse_write_takes_path_from_fence_line():
    c = parse("```write a/b.py\nprint(1)\nprint(2)\n```")
    assert (c.tool, c.arg, c.body) == ("write", "a/b.py", "print(1)\nprint(2)\n")


def test_parse_write_takes_path_from_first_body_line_when_fence_has_none():
    c = parse("```write\na.txt\nhello\n```")
    assert (c.arg, c.body) == ("a.txt", "hello\n")


def test_parse_read_target_on_fence_line_or_in_body():
    assert parse("```read src/x.py\n```").arg == "src/x.py"
    assert parse("```read\nsrc/x.py\n```").arg == "src/x.py"


def test_parse_ignores_code_blocks_that_are_not_tools():
    assert parse("Here you go:\n```python\nprint(1)\n```") is None


def test_parse_plain_text_is_not_a_call():
    assert parse("The answer is 4.") is None


def test_parse_returns_only_the_first_block_and_where_it_ends():
    text = "```shell\nls\n```\nOutput: a.txt\n```shell\nrm a.txt\n```"
    c = parse(text)
    assert c.body == "ls"
    assert text[: c.end] == "```shell\nls\n```\n"


def test_parse_keeps_nested_code_fences_inside_a_write():
    text = "```write README.md\n# Hi\n```bash\npip install x\n```\nbye\n```\ntrailing"
    c = parse(text)
    assert c.body == "# Hi\n```bash\npip install x\n```\nbye\n"
    assert text[c.end :] == "trailing"


def test_parse_unclosed_block_counts_only_once_the_reply_is_final():
    assert parse("```shell\nls") is None
    assert parse("```shell\nls", final=True).body == "ls"


def test_parse_falls_back_to_json_tool_calls():
    c = parse('<tool_call>\n{"name": "shell", "arguments": {"command": "ls -la"}}\n</tool_call>', final=True)
    assert (c.tool, c.body) == ("shell", "ls -la")
    c = parse('{"tool":"write","args":{"path":"a.txt","content":"hi\\n"}}', final=True)
    assert (c.tool, c.arg, c.body) == ("write", "a.txt", "hi\n")


# ---------- tools ----------

def test_read_returns_file_text(tmp_path):
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    assert read(str(tmp_path / "a.txt")) == "one\ntwo"


def test_read_pages_long_files_and_says_how_to_continue(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("".join(f"line {i}\n" for i in range(1, 501)))
    out = read(str(f), cap=200)
    assert out.startswith("line 1\nline 2\n")
    assert len(out) < 400
    last = int(out.split("[lines 1-")[1].split(" ")[0])
    assert f"of 500; next: read {f} {last + 1}]" in out
    assert read(f"{f} {last + 1}", cap=200).startswith(f"line {last + 1}\n")


def test_read_makes_progress_on_a_single_huge_line(tmp_path):
    f = tmp_path / "min.js"
    f.write_text("x" * 5000 + "\nnext\n")
    out = read(str(f), cap=200)
    assert "next: read" in out and len(out) < 500


def test_read_lists_directories(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "b.txt").write_text("")
    assert read(str(tmp_path)) == "b.txt\nsub/"


def test_write_creates_parent_dirs(tmp_path):
    out = write(str(tmp_path / "x" / "y.txt"), "hi\n")
    assert (tmp_path / "x" / "y.txt").read_text() == "hi\n"
    assert "y.txt" in out


def test_edit_replaces_exact_text(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("a = 1\nb = 2\n")
    edit(str(f), "b = 2\n====\nb = 3\n")
    assert f.read_text() == "a = 1\nb = 3\n"


def test_edit_refuses_ambiguous_match_and_leaves_file_alone(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\nx = 1\n")
    assert "2 places" in edit(str(f), "x = 1\n====\nx = 2\n")
    assert f.read_text() == "x = 1\nx = 1\n"


def test_edit_reports_text_not_found(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    assert "not found" in edit(str(f), "y = 1\n====\ny = 2\n")


def test_edit_without_separator_explains_the_format(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    assert "====" in edit(str(f), "x = 2\n")


def test_edit_matches_despite_wrong_indentation_and_reindents(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("def f():\n    if x:\n        return 1\n    return 2\n")
    edit(str(f), "if x:\n    return 1\n====\nif y:\n    return 5\n")
    assert f.read_text() == "def f():\n    if y:\n        return 5\n    return 2\n"


def test_shell_returns_output_and_nonzero_exit_code():
    assert shell("echo hi") == "hi"
    assert shell("echo oops >&2; exit 3") == "oops\n[exit 3]"


def test_shell_does_not_hang_waiting_for_stdin():
    assert shell("cat", timeout=5) == "(no output)"


def test_shell_kills_commands_that_run_too_long():
    t = time.time()
    out = shell("sleep 30", timeout=1)
    assert "timed out" in out and time.time() - t < 10


def test_shell_cd_persists_to_later_tools(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    shell("cd sub")
    assert os.path.realpath(os.getcwd()) == os.path.realpath(tmp_path / "sub")
    assert os.path.realpath(shell("pwd")) == os.path.realpath(tmp_path / "sub")


def test_shell_clips_long_output_keeping_head_and_tail():
    out = shell("seq 1 5000", cap=300)
    assert out.startswith("1\n2\n") and out.endswith("5000") and len(out) < 500


def test_html_text_drops_scripts_and_tags():
    page = "<html><head><title>t</title><style>p{}</style></head><body><script>x()</script><h1>Hi &amp; bye</h1><p>para  one</p></body></html>"
    assert html_text(page) == "Hi & bye\npara one"


def test_fetch_refuses_non_http_urls():
    with pytest.raises(ValueError):
        sprint.fetch("file:///etc/hosts")


# ---------- fit: keep the conversation inside the context budget ----------

def convo(pairs, size=400):
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "the task", "human": True}]
    for i in range(pairs):
        m += [{"role": "assistant", "content": f"call {i} " + "a" * size}, {"role": "user", "content": f"result {i} " + "r" * size}]
    return m


def tokens(m, cpt=3.5):
    return sum(len(x["content"]) / cpt + 4 for x in m)


def test_fit_leaves_small_conversations_untouched():
    m = convo(3)
    before = json.dumps(m)
    fit(m, budget=10_000)
    assert json.dumps(m) == before


def test_fit_clips_old_turns_before_dropping_anything():
    m = convo(6)
    fit(m, budget=tokens(m) - 150)
    assert len(m) == 14
    assert len(m[2]["content"]) < 300 and m[2]["content"].startswith("call 0")
    assert [len(x["content"]) for x in m[-4:]] == [len(x["content"]) for x in convo(6)[-4:]]


def test_fit_drops_oldest_turns_when_clipping_is_not_enough():
    m = convo(20)
    fit(m, budget=900)
    assert tokens(m) <= 900
    assert m[0]["content"] == "sys" and m[1]["content"] == "the task"
    assert [x["role"] for x in m[1:]] == ["user"] + ["assistant", "user"] * ((len(m) - 2) // 2)
    assert m[-1]["content"].startswith("result 19")


def test_fit_makes_a_newer_human_message_the_anchor_when_its_turn_is_dropped():
    m = convo(2) + [{"role": "assistant", "content": "answer"}, {"role": "user", "content": "second task", "human": True}] + convo(12)[2:]
    fit(m, budget=900)
    assert m[1]["content"] == "second task"


# ---------- agent loop ----------

def scripted(*replies):
    """fake model: each call plays the next reply (a str or a list of chunks)"""
    seen, it = [], iter(replies)

    def chat(msgs):
        seen.append([dict(m) for m in msgs])
        r = next(it)
        yield from ([r] if isinstance(r, str) else r)

    chat.seen = seen
    return chat


def agent(chat, **kw):
    kw.setdefault("mode", "auto")
    return Agent(chat=chat, out=io.StringIO(), **kw)


def test_agent_runs_a_tool_and_feeds_the_result_back(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("secret 42\n")
    chat = scripted("```read a.txt\n```", "It says 42.")
    assert agent(chat).run("what is in a.txt?") == "It says 42."
    second = chat.seen[1]
    assert second[1]["content"] == "what is in a.txt?"
    assert second[-1]["role"] == "user" and "secret 42" in second[-1]["content"]


def test_agent_sends_only_role_and_content_to_the_model():
    chat = scripted("hi")
    agent(chat).run("hello")
    assert all(set(m) == {"role", "content"} for m in chat.seen[0])


def test_agent_cuts_off_a_model_that_keeps_talking_after_its_tool_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pulled = []

    def chat(msgs):
        if len(msgs) > 2:
            yield "done"
            return
        for c in ["```shell\necho real\n```\n", "Output:", " fake", " more", " more"]:
            pulled.append(c)
            yield c

    a = agent(chat)
    a.run("go")
    assert pulled == ["```shell\necho real\n```\n", "Output:"]
    assert a.msgs[2]["content"] == "```shell\necho real\n```"
    assert "real" in a.msgs[3]["content"] and "fake" not in a.msgs[3]["content"]


def test_agent_does_not_run_a_denied_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted("```write a.txt\nhi\n```", "ok")
    agent(chat, mode="ask", ask=lambda call: False).run("go")
    assert not (tmp_path / "a.txt").exists()
    assert "denied" in chat.seen[1][-1]["content"]


def test_agent_never_asks_before_reading(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    asked = []
    chat = scripted("```read .\n```", "ok")
    agent(chat, mode="ask", ask=lambda call: asked.append(call) or False).run("go")
    assert asked == []


def test_edits_mode_writes_without_asking_but_still_asks_for_shell(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    asked = []
    chat = scripted("```write a.txt\nhi\n```", "```shell\necho x > b.txt\n```", "ok")
    agent(chat, mode="edits", ask=lambda c: asked.append(c.tool) or False).run("go")
    assert (tmp_path / "a.txt").exists() and not (tmp_path / "b.txt").exists()
    assert asked == ["shell"]


def test_auto_mode_never_asks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    asked = []
    chat = scripted("```shell\necho x > b.txt\n```", "ok")
    agent(chat, mode="auto", ask=lambda c: asked.append(c) or False).run("go")
    assert (tmp_path / "b.txt").exists() and asked == []


def test_answering_always_allows_just_that_command_from_then_on(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    asked = []
    chat = scripted("```shell\necho 1\n```", "```shell\necho 2\n```", "```shell\necho 1\n```", "ok")
    a = agent(chat, mode="ask", ask=lambda c: asked.append(c.body) or "always")
    a.run("go")
    assert asked == ["echo 1", "echo 2"] and a.mode == "ask"
    assert a.allow == ["shell echo 1", "shell echo 2"] and sprint.load_config()["allow"] == a.allow


def test_cycle_steps_through_ask_edits_auto():
    a = agent(scripted(), mode="ask")
    assert [a.cycle(), a.cycle(), a.cycle()] == ["edits", "auto", "ask"]


# ---------- what the user sees ----------

def test_ui_shows_the_call_and_its_result_instead_of_the_raw_fence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = agent(scripted(["Let me look.\n", "```sh", "ell\necho hi\n", "```"], "All good."))
    a.run("go")
    seen = a.out.getvalue()
    assert "```" not in seen
    assert "Let me look." in seen and "$ echo hi" in seen and "│ hi" in seen and "All good." in seen


def test_ui_shows_an_edit_as_a_diff(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    a = agent(scripted("```edit a.py\nx = 1\n====\nx = 2\n```", "ok"))
    a.run("go")
    assert "- x = 1" in a.out.getvalue() and "+ x = 2" in a.out.getvalue()


def test_ui_still_shows_ordinary_code_blocks():
    a = agent(scripted("```python\nx = 1\n```", "```python\nx = 1\n```"))
    a.run("go")
    assert "```python\nx = 1\n```" in a.out.getvalue()


def test_ui_prints_streamed_text_exactly_once():
    a = agent(scripted(["Hel", "lo ", "the", "re.\n", "`ls` lists", " files"]))
    a.run("go")
    seen = a.out.getvalue()
    assert seen.count("Hello there.") == 1 and seen.count("`ls` lists files") == 1


def test_ui_hides_thinking():
    a = agent(scripted(["<think>secret plan", "</think>", "Four."]))
    a.run("2+2")
    assert "secret plan" not in a.out.getvalue() and "Four." in a.out.getvalue()


def test_agent_stops_a_model_stuck_repeating_the_same_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted(*["```shell\necho x >> log\n```"] * 10)
    out = agent(chat).run("go")
    assert "repeating" in out
    assert (tmp_path / "log").read_text() == "x\n"


def test_agent_stops_a_model_cycling_through_the_same_calls(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("a\n")
    (tmp_path / "b.txt").write_text("b\n")
    chat = scripted(*["```read a.txt\n```", "```read b.txt\n```"] * 10)
    assert "repeating" in agent(chat).run("go")
    assert len(chat.seen) <= 5


def test_agent_allows_rerunning_a_command_after_real_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    steps = []
    for i in range(6):
        steps += [f"```write n.txt\n{i}\n```", "```shell\ncat n.txt\n```"]
    assert agent(scripted(*steps, "done")).run("go") == "done"


def test_agent_stops_at_the_step_limit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted(*[f"```shell\necho {i}\n```" for i in range(10)])
    assert "step limit" in agent(chat, steps=3).run("go")
    assert len(chat.seen) == 3


def test_agent_does_not_execute_a_reply_cut_off_by_the_token_limit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted(["```write a.txt\nhalf a fi", {"cut": True}], "gave up")
    assert agent(chat).run("go") == "gave up"
    assert not (tmp_path / "a.txt").exists() and "cut off" in chat.seen[1][-1]["content"]


def test_agent_reports_tool_errors_to_the_model_instead_of_crashing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted("```read nope.txt\n```", "missing")
    assert agent(chat).run("go") == "missing"
    assert "error" in chat.seen[1][-1]["content"] and "nope.txt" in chat.seen[1][-1]["content"]


def test_agent_keeps_thinking_out_of_the_history_and_the_answer():
    chat = scripted("<think>\n```shell\nrm -rf /\n```\nhmm</think>The answer is 4.")
    a = agent(chat)
    assert a.run("2+2?") == "The answer is 4."
    assert a.msgs[-1]["content"] == "The answer is 4."


def test_agent_nudges_then_gives_up_on_empty_replies():
    chat = scripted("", "", "", "")
    out = agent(chat).run("go")
    assert "empty" in out and len(chat.seen) == 3


def test_agent_nudges_once_when_code_is_shown_but_not_saved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted("```python\nprint('hi')\n```", "```write hello.py\nprint('hi')\n```", "Created hello.py.")
    assert agent(chat).run("make hello.py") == "Created hello.py."
    assert (tmp_path / "hello.py").read_text() == "print('hi')\n"
    assert "not saved" in chat.seen[1][-1]["content"]


def test_agent_accepts_a_code_answer_the_second_time():
    chat = scripted("```python\nx = 1\n```", "```python\nx = 1\n```", "never asked")
    assert agent(chat).run("show me code") == "```python\nx = 1\n```"
    assert len(chat.seen) == 2


def test_empty_write_saves_the_code_block_shown_just_before_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted("```python\nprint('hi')\n```\n```write hello.py\n```", "done")
    agent(chat).run("make hello.py")
    assert (tmp_path / "hello.py").read_text() == "print('hi')\n"


def test_empty_write_saves_the_code_block_from_the_previous_reply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted("```python\nprint('hi')\n```", "```write hello.py\n```", "done")
    agent(chat).run("make hello.py")
    assert (tmp_path / "hello.py").read_text() == "print('hi')\n"


def test_write_with_no_content_is_an_error_not_an_empty_file(tmp_path):
    assert "error" in write(str(tmp_path / "x.txt"), "")
    assert not (tmp_path / "x.txt").exists()


def test_agent_learns_a_denser_token_ratio_from_reported_usage():
    chat = scripted(["hi", {"prompt": 10_000}])
    a = agent(chat)
    before = a.cpt
    a.run("go")
    assert a.cpt < before


def test_agent_ignores_usage_that_would_make_the_estimate_less_safe():
    chat = scripted(["hi", {"prompt": 1}])
    a = agent(chat)
    before = a.cpt
    a.run("go")
    assert a.cpt == before


def test_agent_keeps_prompt_within_budget_over_a_long_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "big.txt").write_text("word " * 20_000)
    chat = scripted(*[f"```shell\ncat big.txt; echo {i}\n```" for i in range(25)], "done")
    a = agent(chat, ctx=2048, steps=40)
    assert a.run("go") == "done"
    for sent in chat.seen:
        assert sum(len(m["content"]) / 3.5 + 4 for m in sent) <= 2048 - a.max_out
    anchor = chat.seen[-1][1]["content"]  # older steps are folded into a summary rather than just dropped
    assert "Summary" in anchor and "current request: go" in anchor and "shell cat big.txt; echo" in anchor


def test_agent_remembers_earlier_turns_on_a_second_run():
    chat = scripted("Hello Sam.", "Sam.")
    a = agent(chat)
    a.run("I am Sam")
    a.run("who am I?")
    assert [m["content"] for m in chat.seen[1]][1:] == ["I am Sam", "Hello Sam.", "who am I?"]


def test_agent_reset_forgets_everything_but_the_system_prompt():
    a = agent(scripted("hi"))
    a.run("hello")
    a.reset()
    assert len(a.msgs) == 1 and a.msgs[0]["role"] == "system"


def test_system_prompt_stays_tiny():
    assert len(agent(scripted()).msgs[0]["content"]) < 700


# ---------- model backends (HTTP faked at the socket boundary) ----------

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def fake_urlopen(monkeypatch, payload):
    sent = {}

    def urlopen(req, timeout=None):
        sent["url"], sent["body"], sent["headers"] = req.full_url, json.loads(req.data), dict(req.header_items())
        return FakeResponse(payload.encode())

    monkeypatch.setattr(sprint.urllib.request, "urlopen", urlopen)
    return sent


def test_ollama_backend_sets_context_size_and_parses_the_stream(monkeypatch):
    sent = fake_urlopen(monkeypatch, "\n".join([
        json.dumps({"message": {"content": "hel"}}),
        json.dumps({"message": {"content": "lo"}}),
        json.dumps({"message": {"content": ""}, "done": True, "done_reason": "length", "prompt_eval_count": 77}),
    ]))
    a = Agent(model="tiny", url="http://localhost:11434", ctx=4096, out=io.StringIO())
    chunks = list(a.chat([{"role": "user", "content": "hi"}]))
    assert sent["url"] == "http://localhost:11434/api/chat"
    assert sent["body"]["options"]["num_ctx"] == 4096 and sent["body"]["model"] == "tiny"
    assert "".join(c for c in chunks if isinstance(c, str)) == "hello"
    meta = {k: v for c in chunks if isinstance(c, dict) for k, v in c.items()}
    assert meta == {"prompt": 77, "cut": True}


def test_ollama_backend_wraps_separate_thinking_in_think_tags(monkeypatch):
    fake_urlopen(monkeypatch, "\n".join([
        json.dumps({"message": {"thinking": "let me see"}}),
        json.dumps({"message": {"content": "4"}}),
        json.dumps({"done": True, "done_reason": "stop"}),
    ]))
    a = Agent(model="tiny", url="http://localhost:11434", out=io.StringIO())
    text = "".join(c for c in a.chat([]) if isinstance(c, str))
    assert text == "<think>let me see</think>4"


def test_openai_backend_is_chosen_by_a_v1_url_and_parses_sse(monkeypatch):
    sent = fake_urlopen(monkeypatch, "\n\n".join([
        "data: " + json.dumps({"choices": [{"delta": {"content": "hel"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}),
        "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 55}}),
        "data: [DONE]",
    ]))
    a = Agent(model="tiny", url="http://localhost:8080/v1", key="k", out=io.StringIO())
    chunks = list(a.chat([{"role": "user", "content": "hi"}]))
    assert sent["url"] == "http://localhost:8080/v1/chat/completions"
    assert sent["body"]["max_tokens"] == a.max_out
    assert sent["headers"]["Authorization"] == "Bearer k"
    assert "".join(c for c in chunks if isinstance(c, str)) == "hello"
    assert {"prompt": 55} in chunks


# ---------- starting Ollama when it is not running ----------

def fake_server(monkeypatch, up_after_spawn=True, up=False, tags=()):
    """pretend network + process table: returns the list of commands sprint tried to spawn"""
    state, spawned = {"up": up}, []

    def urlopen(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        if not state["up"]:
            raise sprint.urllib.error.URLError("connection refused")
        return FakeResponse(json.dumps({"models": [{"name": t} for t in tags]}).encode() if url.endswith("/api/tags") else b"{}")

    def popen(cmd, **kw):
        spawned.append((cmd, kw.get("env", {}).get("OLLAMA_HOST")))
        state["up"] = up_after_spawn

    monkeypatch.setattr(sprint.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sprint.subprocess, "Popen", popen)
    monkeypatch.setattr(sprint.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(sprint.time, "sleep", lambda s: None)
    return spawned


def test_serve_leaves_a_running_server_alone(monkeypatch):
    spawned = fake_server(monkeypatch, up=True)
    assert sprint.serve("http://localhost:11434") is True and spawned == []


def test_serve_starts_ollama_when_nothing_is_listening(monkeypatch):
    spawned = fake_server(monkeypatch)
    assert sprint.serve("http://localhost:11434") is True
    assert spawned == [(["ollama", "serve"], "localhost:11434")]


def test_serve_gives_up_if_ollama_never_comes_up(monkeypatch):
    fake_server(monkeypatch, up_after_spawn=False)
    assert sprint.serve("http://localhost:11434") is False


def test_serve_never_starts_anything_for_a_remote_or_openai_url(monkeypatch):
    spawned = fake_server(monkeypatch)
    assert sprint.serve("http://gpu-box:11434") is False
    assert sprint.serve("http://localhost:8080/v1") is False
    assert spawned == []


def test_serve_does_nothing_without_the_ollama_binary(monkeypatch):
    spawned = fake_server(monkeypatch)
    monkeypatch.setattr(sprint.shutil, "which", lambda name: None)
    assert sprint.serve("http://localhost:11434") is False and spawned == []


def test_installed_matches_exact_and_implicit_latest_tags(monkeypatch):
    fake_server(monkeypatch, up=True, tags=["qwen3.5:0.8b", "smollm2:latest"])
    assert sprint.installed("http://localhost:11434", "qwen3.5:0.8b")
    assert sprint.installed("http://localhost:11434", "smollm2")
    assert not sprint.installed("http://localhost:11434", "qwen3.5:2b")


# ---------- native tool calls (models trained for them do far better this way) ----------

def test_native_tool_call_runs_and_is_recorded_in_the_models_own_format(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    call = {"name": "write", "arguments": {"path": "a.txt", "content": "hi\n"}}
    chat = scripted(["Saving it.", {"call": call}], "done")
    assert agent(chat).run("make a.txt") == "done"
    assert (tmp_path / "a.txt").read_text() == "hi\n"
    assert chat.seen[1][-2] == {"role": "assistant", "content": "Saving it.", "tool_calls": [{"function": call}]}
    assert chat.seen[1][-1] == {"role": "tool", "content": "wrote a.txt (1 line). Now do the next step of the task.",
                                "tool_name": "write"}  # the nudge stops tiny models re-sending the same write


def test_native_edit_and_shell_calls_use_the_same_tools(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    chat = scripted([{"call": {"name": "edit", "arguments": {"path": "a.py", "old": "x = 1", "new": "x = 2"}}}],
                    [{"call": {"name": "shell", "arguments": {"command": "cat a.py"}}}], "ok")
    agent(chat).run("go")
    assert (tmp_path / "a.py").read_text() == "x = 2\n"
    assert "x = 2" in chat.seen[2][-1]["content"]


def test_native_call_arguments_may_arrive_as_a_json_string(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted([{"call": {"name": "write", "arguments": '{"path": "a.txt", "content": "hi"}'}}], "ok")
    agent(chat).run("go")
    assert (tmp_path / "a.txt").read_text() == "hi\n"


def test_ollama_backend_offers_tools_turns_thinking_off_and_reports_calls(monkeypatch):
    fn = {"name": "read", "arguments": {"path": "a"}}
    sent = fake_urlopen(monkeypatch, "\n".join([
        json.dumps({"message": {"content": "", "tool_calls": [{"function": fn}]}}),
        json.dumps({"done": True, "done_reason": "stop"}),
    ]))
    a = Agent(model="tiny", url="http://localhost:11434", out=io.StringIO())
    chunks = list(a.chat([{"role": "user", "content": "hi"}]))
    assert [t["function"]["name"] for t in sent["body"]["tools"]] == ["shell", "read", "write", "edit", "fetch"]
    assert sent["body"]["think"] is False
    assert {"call": fn} in chunks


def test_openai_backend_sticks_to_fenced_blocks(monkeypatch):
    sent = fake_urlopen(monkeypatch, "data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]}))
    a = Agent(model="tiny", url="http://localhost:8080/v1", out=io.StringIO())
    list(a.chat([{"role": "user", "content": "hi"}]))
    assert "tools" not in sent["body"] and "```shell" in a.msgs[0]["content"]


def test_model_without_tool_support_falls_back_to_fenced_blocks(monkeypatch):
    bodies = []

    def urlopen(req, timeout=None):
        bodies.append(json.loads(req.data))
        if "tools" in bodies[-1]:
            raise sprint.urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                                io.BytesIO(b'{"error":"tiny does not support tools"}'))
        return FakeResponse((json.dumps({"message": {"content": "hi"}}) + "\n" + json.dumps({"done": True})).encode())

    monkeypatch.setattr(sprint.urllib.request, "urlopen", urlopen)
    a = Agent(model="tiny", url="http://localhost:11434", out=io.StringIO())
    assert a.run("hello") == "hi"
    assert "tools" not in bodies[1] and "```shell" in bodies[1]["messages"][0]["content"]
    a.run("again")
    assert len(bodies) == 3 and "tools" not in bodies[2]


def test_fit_counts_and_clips_native_tool_call_arguments():
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task", "human": True}]
    for _ in range(6):
        m += [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "write", "arguments": {"path": "f", "content": "x" * 3000}}}]},
              {"role": "tool", "content": "wrote f", "tool_name": "write"}]
    fit(m, budget=2500)
    sizes = [len(x["tool_calls"][0]["function"]["arguments"]["content"]) for x in m if "tool_calls" in x]
    assert len(m) == 14 and sizes[-2:] == [3000, 3000] and max(sizes[:4]) < 300


def test_tool_schemas_count_toward_the_measured_prompt_size():
    a = agent(scripted())
    chars = len(a.msgs[0]["content"]) + len("go") + len(json.dumps(sprint.TOOLS))
    a.chat = scripted(["hi", {"prompt": int(chars / 3.5)}])
    a.run("go")
    assert a.cpt > 3.3  # the server counted the schemas too; that must not look like dense text
    assert a.used() > len(json.dumps(sprint.TOOLS)) / 3.5


def test_system_prompt_names_the_files_in_the_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "src").mkdir()
    (tmp_path / ".git").mkdir()
    chat = scripted("ok")
    agent(chat).run("hi")
    prompt = chat.seen[0][0]["content"]
    assert "notes.txt" in prompt and "src/" in prompt and ".git" not in prompt


def test_file_listing_in_the_prompt_is_capped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for i in range(300):
        (tmp_path / f"some_long_file_name_{i}.txt").write_text("")
    assert len(sprint.here()) < 500


def test_ui_flags_a_write_that_replaces_an_existing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("old\n")
    a = agent(scripted("```write a.txt\nnew\n```", "```write b.txt\nnew\n```", "ok"))
    a.run("go")
    assert a.out.getvalue().count("replaces existing file") == 1


# ---------- hooks a full-screen front end needs ----------

def test_display_can_be_redirected_through_emit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    got = []
    a = agent(scripted("Looking.\n```shell\necho hi\n```", "Done."))
    a.emit = lambda s, style: got.append((s, style))
    a.run("go")
    text = "".join(s for s, _ in got)
    assert "Looking." in text and "$ echo hi" in text and "│ hi" in text and "Done." in text
    assert a.out.getvalue() == ""


def test_cancel_stops_a_turn_mid_stream():
    def chat(msgs):
        yield "one "
        a.cancel = True
        yield "two "
        yield "three"

    a = agent(chat)
    with pytest.raises(KeyboardInterrupt):
        a.run("go")
    assert "three" not in a.out.getvalue()


def test_cancel_is_cleared_when_the_next_message_starts():
    a = agent(scripted("hi"))
    a.cancel = True
    assert a.run("go") == "hi"


def test_cancel_kills_a_running_shell_command():
    import threading
    t = time.time()
    threading.Timer(0.5, sprint.kill_running).start()
    out = shell("sleep 30", timeout=20)
    assert time.time() - t < 10 and "exit" in out


def test_agent_remembers_which_files_it_changed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "b.txt").write_text("x\n")
    a = agent(scripted("```write a.txt\nhi\n```", "```edit b.txt\nx\n====\ny\n```", "```write a.txt\nho\n```",
                       "```edit nope.txt\nx\n====\ny\n```", "ok"))
    a.run("go")
    assert a.touched == ["a.txt", "b.txt"]


def test_a_code_block_in_the_answer_is_fine_once_the_model_has_used_a_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("beta\n")
    chat = scripted("```read a.txt\n```", "It says:\n```\nbeta\n```", "never asked")
    assert agent(chat).run("what is in a.txt?") == "It says:\n```\nbeta\n```"
    assert len(chat.seen) == 2


def test_agent_pushes_back_once_when_the_model_claims_it_cannot_access_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("beta\n")
    chat = scripted("I don't have access to your files, so I cannot read a.txt.", "```read a.txt\n```", "It says beta.")
    assert agent(chat).run("what is in a.txt?") == "It says beta."
    assert "tools" in chat.seen[1][-1]["content"]


def test_a_second_refusal_is_accepted_as_the_answer():
    chat = scripted("I cannot access that file.", "I really can't read it.", "never asked")
    assert agent(chat).run("go") == "I really can't read it."


# ---------- thinking, effort, context breakdown, settings ----------

def test_effort_sets_the_reply_budget_and_step_limit():
    a = agent(scripted(), ctx=8192)
    assert (a.effort, a.max_out, a.steps) == ("medium", 1024, 16)
    a.set_effort("high")
    assert (a.max_out, a.steps) == (2048, 32)
    a.set_effort("low")
    assert (a.max_out, a.steps) == (512, 8)


def test_thinking_is_requested_at_the_effort_level_only_when_switched_on(monkeypatch):
    sent = fake_urlopen(monkeypatch, json.dumps({"done": True}))
    a = Agent(model="tiny", url="http://localhost:11434", out=io.StringIO())
    list(a.chat([]))
    assert sent["body"]["think"] is False
    a.think = True
    a.set_effort("high")
    list(a.chat([]))
    assert sent["body"]["think"] == "high" and sent["body"]["options"]["num_predict"] == 2048


def test_a_model_that_cannot_think_gets_thinking_switched_off(monkeypatch):
    bodies = []

    def urlopen(req, timeout=None):
        bodies.append(json.loads(req.data))
        if bodies[-1]["think"]:
            raise sprint.urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                                io.BytesIO(b'{"error":"\\"tiny\\" does not support thinking"}'))
        return FakeResponse((json.dumps({"message": {"content": "hi"}}) + "\n" + json.dumps({"done": True})).encode())

    monkeypatch.setattr(sprint.urllib.request, "urlopen", urlopen)
    a = Agent(model="tiny", url="http://localhost:11434", out=io.StringIO())
    a.think = True
    assert a.run("hello") == "hi" and a.think is False and len(bodies) == 2


def test_thoughts_are_shown_only_when_thinking_is_on():
    a = agent(scripted(["<think>weigh the options", "</think>", "Four."], ["<think>weigh again</think>Five."]))
    a.run("2+2")
    assert "weigh the options" not in a.out.getvalue()
    a.think = True
    a.run("2+3")
    assert "weigh again" in a.out.getvalue() and a.msgs[-1]["content"] == "Five."


def test_breakdown_accounts_for_the_whole_window(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("word " * 300)
    a = agent(scripted([{"call": {"name": "read", "arguments": {"path": "a.txt"}}}], "It is full of words."))
    a.run("what is in a.txt?")
    parts = dict(a.breakdown())
    assert list(parts) == ["system prompt", "tool definitions", "your messages", "model replies", "tool results",
                           "reserved for the reply", "free"]
    assert sum(parts.values()) == a.ctx and parts["reserved for the reply"] == a.max_out
    assert parts["tool results"] > parts["your messages"] > 0 and parts["tool definitions"] > 0


def test_agent_counts_the_turns_it_had_to_forget(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "big.txt").write_text("word " * 20_000)
    a = agent(scripted(*[f"```shell\ncat big.txt; echo {i}\n```" for i in range(25)], "done"), ctx=2048)
    a.run("go")
    assert a.forgotten > 0


def texts(rows):
    return ["".join(t for t, _ in r) for r in rows]


def test_command_toggles_thinking_and_sets_effort():
    a = agent(scripted())
    assert "on" in texts(sprint.command(a, "/think"))[0] and a.think is True
    assert "off" in texts(sprint.command(a, "/think"))[0] and a.think is False
    sprint.command(a, "/effort high")
    assert a.effort == "high"
    assert any("low" in t and "medium" in t and "high" in t for t in texts(sprint.command(a, "/effort")))
    assert a.effort == "high"


def test_command_context_draws_a_grid_of_the_window_with_a_legend():
    a = agent(scripted("hello"))
    a.run("hi")
    rows = sprint.command(a, "/context")
    cells = sum(t.count("█") + t.count("▒") + t.count("·") for r in rows for t, c in r if set(t) <= set("█▒· "))
    assert cells == 200
    joined = "\n".join(texts(rows))
    assert "system prompt" in joined and "free" in joined and "%" in joined and "8,192" in joined
    assert len({c for r in rows for t, c in r if "█" in t}) >= 3  # categories are told apart by colour


def test_command_permissions_lists_tools_and_changes_mode():
    a = agent(scripted(), mode="ask")
    joined = "\n".join(texts(sprint.command(a, "/permissions")))
    assert "shell" in joined and "read" in joined and "asks" in joined
    sprint.command(a, "/permissions edits")
    assert a.mode == "edits"
    sprint.command(a, "/mode auto")
    assert a.mode == "auto"


def test_command_config_shows_changes_and_saves_settings():
    a = agent(scripted())
    assert any("ctx" in t and "8192" in t for t in texts(sprint.command(a, "/config")))
    sprint.command(a, "/config ctx 4096")
    assert a.ctx == 4096 and a.max_out == 512
    sprint.command(a, "/config steps 5")
    sprint.command(a, "/model tiny:1b")
    saved = sprint.load_config()
    assert saved["ctx"] == 4096 and saved["steps"] == 5 and saved["model"] == "tiny:1b" and a.model == "tiny:1b"
    assert "error" in texts(sprint.command(a, "/config ctx lots"))[0] or "number" in texts(sprint.command(a, "/config ctx lots"))[0]


def test_command_leaves_other_input_alone():
    a = agent(scripted())
    assert sprint.command(a, "/sidebar") is None and sprint.command(a, "hello /think") is None


def test_settings_file_survives_being_missing_or_broken():
    assert sprint.load_config() == {}
    sprint.CONFIG.write_text("{not json")
    assert sprint.load_config() == {}
    sprint.save_config(effort="low")
    assert sprint.load_config() == {"effort": "low"}


def test_context_cells_scale_the_window_to_any_number_of_cells():
    a = agent(scripted("hello"))
    a.run("hi")
    for n in (200, 58, 12):
        cells = sprint.context_cells(a, n)
        assert len(cells) == n and cells[0][0] == "█" and cells[-1] == ("▒", "dim")
        assert ("·", "dim") in cells


# ---------- customising: your own system prompt, command timeout ----------

def test_a_custom_system_prompt_replaces_the_default_but_keeps_the_file_listing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("x")
    chat = scripted("Arr.")
    a = agent(chat)
    a.custom = "You are a pirate who writes Rust."
    a.run("hi")
    prompt = chat.seen[0][0]["content"]
    assert prompt.startswith("You are a pirate who writes Rust.") and "notes.txt" in prompt
    assert "coding agent" not in prompt


def test_a_custom_prompt_still_teaches_the_block_format_to_models_without_tool_calls():
    a = agent(scripted())
    a.tools, a.custom = False, "You are a pirate."
    prompt = a.system()
    assert prompt.startswith("You are a pirate.") and "```shell" in prompt and "coding agent" not in prompt


def test_the_system_prompt_file_round_trips_and_empty_removes_it():
    assert sprint.load_prompt() == ""
    sprint.save_prompt("Be brief.\nUse tabs.")
    assert sprint.load_prompt() == "Be brief.\nUse tabs." and sprint.prompt_file().exists()
    sprint.save_prompt("  ")
    assert sprint.load_prompt() == "" and not sprint.prompt_file().exists()


def test_systemprompt_command_shows_it_and_can_reset_it():
    a = agent(scripted())
    assert any("coding agent" in t for t in texts(sprint.command(a, "/systemprompt")))
    sprint.save_prompt("Be brief.")
    a.custom = "Be brief."
    assert any("Be brief." in t for t in texts(sprint.command(a, "/systemprompt")))
    sprint.command(a, "/systemprompt reset")
    assert a.custom == "" and sprint.load_prompt() == ""


def test_command_timeout_is_a_setting(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = []
    monkeypatch.setattr(sprint, "shell", lambda cmd, cap, timeout: seen.append(timeout) or "ok")
    a = agent(scripted("```shell\necho 1\n```", "```shell\necho 2\n```", "done"))
    a.run("go")
    sprint.command(a, "/config timeout 5")
    a.chat = scripted("```shell\necho 3\n```", "done")
    a.run("again")
    assert seen == [60, 60, 5] and sprint.load_config()["timeout"] == 5


# ---------- @file attaches a file to your message; !command runs one yourself ----------

def test_attach_adds_the_contents_of_mentioned_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("alpha\nbeta\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print(1)\n")
    out, images = sprint.attach("compare @notes.txt with @src/app.py, and ignore @nobody and me@example.com.")
    assert images == []
    assert out.startswith("compare @notes.txt with @src/app.py, and ignore @nobody and me@example.com.")
    assert "[notes.txt]\nalpha\nbeta" in out and "[src/app.py]\nprint(1)" in out
    assert "[nobody]" not in out and "example.com]" not in out


def test_attach_copes_with_trailing_punctuation_and_repeats(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("one\n")
    out, _ = sprint.attach("read @a.txt, then @a.txt.")
    assert out.count("[a.txt]") == 1 and "one" in out


def test_attached_files_share_the_room_between_them(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("word\n" * 5000)
    out, _ = sprint.attach("see @a.txt and @b.txt", cap=2000)
    assert len(out) < 2600 and "next: read a.txt" in out and "next: read b.txt" in out


def test_a_message_with_a_mention_reaches_the_model_with_the_file_in_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("alpha\nbeta\ngamma\n")
    chat = scripted("Line 2 is beta.")
    assert agent(chat).run("what is line 2 of @notes.txt?") == "Line 2 is beta."
    sent = chat.seen[0][1]["content"]
    assert sent.startswith("what is line 2 of @notes.txt?") and "alpha\nbeta\ngamma" in sent


def test_bang_runs_your_command_and_tells_the_model_about_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted("It printed 42.")
    a = agent(chat, mode="ask", ask=lambda c: 1 / 0)  # your own command needs nobody's approval
    assert a.bang("echo 42") == "42"
    assert "$ echo 42" in a.out.getvalue() and "│ 42" in a.out.getvalue()
    a.run("what did that print?")
    said = chat.seen[0]
    assert [m["role"] for m in said] == ["system", "user"]
    assert "$ echo 42\n42" in said[1]["content"] and said[1]["content"].endswith("what did that print?")



# ---------- speed, allow list, pasted files, plan mode, race ----------

def test_agent_records_how_fast_the_model_wrote():
    a = agent(scripted(["hi", {"out": 50, "secs": 2.0}]))
    a.run("go")
    assert a.rate == 25.0 and a.took >= 0


def test_ollama_backend_reports_tokens_written_and_time_taken(monkeypatch):
    fake_urlopen(monkeypatch, json.dumps({"message": {"content": "hi"}}) + "\n" +
                 json.dumps({"done": True, "eval_count": 30, "eval_duration": 1_500_000_000}))
    a = Agent(model="tiny", url="http://localhost:11434", out=io.StringIO())
    assert {"out": 30, "secs": 1.5} in list(a.chat([]))


def test_allow_rules_match_by_pattern_and_only_for_their_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    asked = []
    chat = scripted("```shell\ngit status -s\n```", "```shell\ngit push\n```", "```write git status\nx\n```", "ok")
    a = agent(chat, mode="ask", ask=lambda c: asked.append(c.tool + " " + (c.body.strip() if c.tool == "shell" else c.arg)) or False)
    a.allow = ["shell git status*"]
    a.run("go")
    assert asked == ["shell git push", "write git status"]


def test_always_on_a_fetch_allows_the_whole_site_and_odd_characters_are_taken_literally():
    a = agent(scripted())
    assert a.rule_for(sprint.Call("fetch", "https://example.com/a/b?q=1", "", 0, 0)) == "fetch https://example.com/*"
    rule = a.rule_for(sprint.Call("shell", "", "ls *.py", 0, 0))
    a.allow = [rule]
    assert a.allowed(sprint.Call("shell", "", "ls *.py", 0, 0)) and not a.allowed(sprint.Call("shell", "", "ls x.py", 0, 0))


def test_permissions_command_lists_adds_and_forgets_rules():
    a = agent(scripted())
    sprint.command(a, "/permissions allow shell git status*")
    sprint.command(a, "/permissions allow write notes/*")
    assert a.allow == ["shell git status*", "write notes/*"] and sprint.load_config()["allow"] == a.allow
    assert any("git status*" in t for t in texts(sprint.command(a, "/permissions")))
    sprint.command(a, "/permissions forget 1")
    assert a.allow == ["write notes/*"]
    assert "cannot" in texts(sprint.command(a, "/permissions allow dance now"))[0]


def test_pasted_files_are_attached_by_their_chip_names(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "shot.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "data.csv").write_text("a,b\n1,2\n")
    text, images = sprint.attach("what is in [Image #1] and [File #1]?", chips={"[Image #1]": str(tmp_path / "shot.png"),
                                                                              "[File #1]": str(tmp_path / "data.csv"), "[File #2]": "/nope"})
    import base64
    assert images == [base64.b64encode(b"\x89PNG fake").decode()]
    assert "[Image #1] is a picture you can see directly" in text and "[File #1]\na,b\n1,2" in text and "[File #2]" not in text


def test_pdf_text_is_read_even_without_a_pdf_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(sprint.shutil, "which", lambda name: None)
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nstream\nBT /F1 12 Tf (Hello) Tj [(PD) -20 (F wor) 5 (ld)] TJ ET\nendstream\nendobj\n%%EOF")
    assert "Hello" in sprint.pdf_text(str(f)) and "PDF world" in sprint.pdf_text(str(f))
    text, _ = sprint.attach("sum up [PDF #1]", chips={"[PDF #1]": str(f)})
    assert "[PDF #1]\n" in text and "PDF world" in text


def test_images_travel_on_the_message_to_a_vision_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "shot.png").write_bytes(b"png")
    chat = scripted("A cat.")
    a = agent(chat)
    a.run("what is [Image #1]?", chips={"[Image #1]": str(tmp_path / "shot.png")})
    user = chat.seen[0][1]
    assert user["images"] == ["cG5n"] and "[Image #1]" in user["content"]


def test_plan_mode_asks_for_steps_then_does_them_one_at_a_time(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted("Sure:\n1. Create hello.py\n2. Run it with python", "```write hello.py\nprint('hi')\n```", "Created.",
                    "```shell\necho ran\n```", "It ran.")
    a = agent(chat)
    shown = []
    a.show_plan = lambda steps, at: shown.append((list(steps), at))
    assert a.run_plan("make hello.py and run it") == "It ran."
    assert (tmp_path / "hello.py").exists()
    assert chat.seen[0][0]["role"] == "system" and "list" in chat.seen[0][0]["content"] and len(chat.seen[0]) == 2
    asks = [m["content"] for m in a.msgs if m.get("human")]
    assert "step 1 of 2" in asks[0] and "Create hello.py" in asks[0] and "step 2 of 2" in asks[1] and "Run it" in asks[1]
    assert shown[0] == (["Create hello.py", "Run it with python"], 0) and shown[-1][1] == 2


def test_plan_mode_just_runs_when_there_is_nothing_to_split():
    chat = scripted("It is one thing.", "Four.")
    a = agent(chat)
    assert a.run_plan("2+2?") == "Four."
    assert [m["content"] for m in a.msgs if m.get("human")] == ["2+2?"]


def test_race_runs_both_models_read_only_and_reports_each(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted(["Alpha says hi.", {"out": 40, "secs": 2.0}], "```write x.txt\nboo\n```", ["Beta gave up.", {"out": 10, "secs": 1.0}])
    a = agent(chat)
    a.model = "alpha:1b"
    told = []
    first, second = sprint.race(a, "beta:1b", "say hi", told.append)
    assert (first["model"], first["answer"], first["rate"]) == ("alpha:1b", "Alpha says hi.", 20.0)
    assert (second["model"], second["answer"], second["steps"]) == ("beta:1b", "Beta gave up.", 1)
    assert not (tmp_path / "x.txt").exists() and told == ["alpha:1b", "beta:1b"] and a.msgs == [a.msgs[0]]


# ---------- /directory: the folder sprint works in ----------

def test_directory_command_moves_shows_and_remembers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("x")
    chat = scripted("ok")
    a = agent(chat)
    assert "working in" in texts(sprint.command(a, "/directory"))[0]
    sprint.command(a, "/directory app")
    assert os.path.realpath(os.getcwd()) == os.path.realpath(tmp_path / "app")
    assert [os.path.realpath(p) for p in sprint.load_config()["recent"]] == [os.path.realpath(tmp_path / "app")]
    a.run("hi")
    assert "main.py" in chat.seen[0][0]["content"]  # the model is told about the new folder's files
    assert "cannot" in texts(sprint.command(a, "/directory nowhere"))[0]
    assert os.path.realpath(os.getcwd()) == os.path.realpath(tmp_path / "app")



# ---------- replies that run out of room; export, copy, log ----------

TRUNCATED = '500 from http://localhost:11434: {"error":"llama-server returned invalid tool call arguments for \\"write\\": unexpected end of JSON input"}'


def test_a_reply_may_use_the_free_room_up_to_half_the_window(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    limits = []

    def chat(msgs):
        limits.append(a.limit)
        yield "ok"

    a = agent(chat, ctx=8192)
    a.run("write something long")
    assert limits[0] == 4096 and a.max_out == 1024  # half the window: four times the small reserve it always keeps


def test_ollama_is_given_that_room_as_its_reply_limit(monkeypatch):
    sent = fake_urlopen(monkeypatch, json.dumps({"message": {"content": "hi"}}) + "\n" + json.dumps({"done": True}))
    a = Agent(model="tiny", url="http://localhost:11434", out=io.StringIO())
    a.run("hello")
    assert sent["body"]["options"]["num_predict"] == 4096


def test_a_tool_call_cut_off_mid_way_is_not_run_and_the_model_is_told_to_write_less(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    def chat(msgs):
        calls.append([dict(m) for m in msgs])
        if len(calls) == 1:
            raise RuntimeError(TRUNCATED)
        if len(calls) == 2:
            yield {"call": {"name": "write", "arguments": {"path": "small.txt", "content": "x\n"}}}
        else:
            yield "Written."

    a = agent(chat)
    assert a.run("write a big file") == "Written." and (tmp_path / "small.txt").exists()
    assert "cut off" in calls[1][-1]["content"] and "too long" in calls[1][-1]["content"]


def test_a_reply_that_keeps_running_out_of_room_stops_with_advice():
    chat = scripted(*[["a", {"cut": True}]] * 6)
    out = agent(chat).run("go")
    assert out.startswith("stopped:") and "ctx" in out and len(chat.seen) == 3


def test_other_server_errors_still_reach_you():
    def chat(msgs):
        raise RuntimeError("404 from http://localhost:11434: model not found")
        yield

    with pytest.raises(RuntimeError):
        agent(chat).run("go")


def test_a_call_stopped_for_repeating_still_gets_its_result_shown(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = agent(scripted(*["```shell\necho x\n```"] * 10))
    shown = []
    a.show_call, a.show_result = lambda c: shown.append("call"), lambda r: shown.append("result")
    a.run("go")
    assert shown.count("call") == shown.count("result")


def test_export_writes_the_conversation_as_markdown(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = agent(scripted([{"call": {"name": "write", "arguments": {"path": "a.txt", "content": "hi\n"}}}], "Made it."))
    a.run("make a.txt")
    said = texts(sprint.command(a, "/export talk.md"))[0]
    text = (tmp_path / "talk.md").read_text()
    assert "talk.md" in said and text.startswith("# sprint conversation")
    assert "## You" in text and "make a.txt" in text and "**write** `a.txt`" in text and "wrote a.txt" in text and "Made it." in text
    sprint.command(a, "/export")
    assert len(list(tmp_path.glob("sprint-*.md"))) == 1


def test_copy_puts_the_last_answer_code_or_everything_on_the_clipboard(monkeypatch):
    held = []
    monkeypatch.setattr(sprint, "clipboard", lambda text: held.append(text) or True)
    a = agent(scripted(*["Use this:\n```python\nprint(1)\n```\nDone."] * 2))  # the agent asks once whether to save it
    assert "nothing" in texts(sprint.command(a, "/copy"))[0]
    a.run("show code")
    assert "copied" in texts(sprint.command(a, "/copy"))[0] and held[-1].startswith("Use this:")
    sprint.command(a, "/copy code")
    assert held[-1] == "print(1)\n"
    sprint.command(a, "/copy all")
    assert held[-1].startswith("# sprint conversation") and "show code" in held[-1]
    monkeypatch.setattr(sprint, "clipboard", lambda text: False)
    assert "cannot" in texts(sprint.command(a, "/copy"))[0]


def test_what_happens_is_logged_to_a_file_you_can_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = agent(scripted("```shell\necho hi\n```", "ok"))
    a.run("say hi")

    def chat(msgs):
        raise RuntimeError("404 from http://localhost:11434: model not found")
        yield

    a.chat = chat
    with pytest.raises(RuntimeError):
        a.run("again")
    lines = [json.loads(x) for x in sprint.log_file().read_text().splitlines()]
    kinds = [x["kind"] for x in lines]
    assert kinds == ["message", "tool", "message", "error"]
    assert lines[1]["tool"] == "shell" and lines[1]["target"] == "echo hi" and lines[1]["result"] == "hi"
    assert "model not found" in lines[3]["error"]
    assert any("model not found" in t for t in texts(sprint.command(a, "/log")))



# ---------- compacting: fold old conversation into a summary, then carry on ----------

def steps_agent(tmp_path, *more):
    (tmp_path / "a.txt").write_text("alpha\n")
    chat = scripted([{"call": {"name": "read", "arguments": {"path": "a.txt"}}}],
                    [{"call": {"name": "write", "arguments": {"path": "b.txt", "content": "beta\n"}}}],
                    [{"call": {"name": "shell", "arguments": {"command": "cat b.txt"}}}], "All done.", *more)
    return agent(chat), chat


def test_compact_folds_older_messages_into_a_summary_of_what_was_asked_and_done(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a, _ = steps_agent(tmp_path)
    a.run("copy a.txt into b.txt and show it")
    before = a.used()
    assert a.compact() == 6 and a.used() < before
    assert [m["role"] for m in a.msgs] == ["system", "user", "assistant"] and a.msgs[-1]["content"] == "All done."
    summary = a.msgs[1]["content"]
    assert summary.startswith("Summary of the conversation so far")
    assert "read a.txt" in summary and "write b.txt" in summary and "wrote b.txt" in summary and "shell cat b.txt" in summary
    assert "current request: copy a.txt into b.txt and show it" in summary
    assert a.compact() == 0  # nothing more to fold


def test_compacting_twice_keeps_the_earlier_summary_lines(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a, _ = steps_agent(tmp_path, [{"call": {"name": "shell", "arguments": {"command": "echo more"}}}], "Again done.")
    a.run("copy a.txt into b.txt and show it")
    a.compact()
    a.run("now echo more")
    a.compact()
    summary = a.msgs[1]["content"]
    assert "write b.txt" in summary and "shell echo more" in summary and "current request: now echo more" in summary
    assert summary.count("Summary of the conversation so far") == 1


def test_compact_command_reports_what_it_freed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a, _ = steps_agent(tmp_path)
    assert "nothing" in texts(sprint.command(a, "/compact"))[0]
    a.run("copy a.txt into b.txt and show it")
    said = texts(sprint.command(a, "/compact"))[0]
    assert "folded 6 messages" in said and "tokens" in said and len(a.msgs) == 3


def test_a_plain_reply_that_runs_out_of_room_is_kept_and_continued():
    chat = scripted(["The first half of the", {"cut": True}], " answer.")
    a = agent(chat)
    assert a.run("explain") == "The first half of the answer."
    assert chat.seen[1][-2] == {"role": "assistant", "content": "The first half of the"}
    assert "Continue" in chat.seen[1][-1]["content"]


def test_a_tool_call_that_runs_out_of_room_compacts_first_and_tries_again(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("alpha\n")
    seen = []

    def chat(msgs):
        seen.append([dict(m) for m in msgs])
        n = len(seen)
        if n <= 2:
            yield {"call": {"name": "read", "arguments": {"path": "a.txt"}}} if n == 1 else {"call": {"name": "shell", "arguments": {"command": "echo hi"}}}
        elif n == 3:
            raise RuntimeError(TRUNCATED)
        elif n == 4:
            yield {"call": {"name": "write", "arguments": {"path": "out.txt", "content": "ok\n"}}}
        else:
            yield "Done."

    a = agent(chat)
    assert a.run("read, echo, then write out.txt") == "Done." and (tmp_path / "out.txt").exists()
    retry = seen[3]
    assert len(retry) < len(seen[2]) and "Summary of the conversation so far" in retry[1]["content"]
    assert not any("too long" in m["content"] for m in retry)  # room was made, so it simply gets another go


def test_summary_keeps_what_you_asked_and_what_changed_files_over_what_was_only_looked_at(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("alpha\n")
    looks = [[{"call": {"name": "shell", "arguments": {"command": f"echo look {i}"}}}] for i in range(30)]
    chat = scripted([{"call": {"name": "write", "arguments": {"path": "kept.txt", "content": "x\n"}}}], *looks, "Finished.")
    a = agent(chat, ctx=2048, steps=40)
    a.run("first make kept.txt then look around a lot")
    a.compact()
    summary = a.msgs[1]["content"]
    assert "write kept.txt" in summary and "echo look 29" in summary and "echo look 3 " not in summary
    assert "first make kept.txt then look around a lot" in summary


def test_the_read_tool_can_start_from_a_line_when_called_natively(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "big.txt").write_text("".join(f"line {i}\n" for i in range(1, 301)))
    chat = scripted([{"call": {"name": "read", "arguments": {"path": "big.txt", "from_line": 200}}}], "It says line 201.")
    a = agent(chat)
    a.run("what is on line 201?")
    assert chat.seen[1][-1]["content"].startswith("line 200\nline 201\n")
    read_tool = [t["function"] for t in sprint.TOOLS if t["function"]["name"] == "read"][0]
    assert "from_line" in read_tool["parameters"]["properties"] and read_tool["parameters"]["required"] == ["path"]



# ---------- going in circles is caught sooner; the model is loaded ahead of time ----------

def test_an_exact_repeat_gets_one_warning_and_then_the_run_stops(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted(*["```shell\necho x >> log\n```"] * 10)
    out = agent(chat).run("go")
    assert out.startswith("stopped:") and "repeating" in out and len(chat.seen) == 3
    assert (tmp_path / "log").read_text() == "x\n"
    assert "repeating yourself" in chat.seen[2][-1]["content"]  # the one warning it got


def test_rewriting_the_same_file_over_and_over_is_stopped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted(*[f"```write a.txt\nversion {i}\n```" for i in range(10)])
    out = agent(chat).run("go")
    assert "repeating" in out and len(chat.seen) == 3
    assert "repeating yourself" in chat.seen[2][-1]["content"]


def test_the_same_failure_three_times_is_stopped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("alpha\n")
    chat = scripted(*[f"```edit a.txt\nnot there {i}\n====\nx\n```" for i in range(10)])
    out = agent(chat).run("go")
    assert "repeating" in out and len(chat.seen) == 3


def test_several_different_edits_to_one_file_in_a_row_are_fine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\nfour\n")
    edits = [f"```edit a.txt\n{old}\n====\n{new}\n```" for old, new in (("one", "1"), ("two", "2"), ("three", "3"), ("four", "4"))]
    assert agent(scripted(*edits, "All four changed.")).run("go") == "All four changed."
    assert (tmp_path / "a.txt").read_text() == "1\n2\n3\n4\n"


def test_the_default_step_limit_is_modest_and_says_how_to_go_on(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    chat = scripted(*[f"```shell\necho {i}\n```" for i in range(40)])
    out = agent(chat).run("go")
    assert "step limit" in out and "continue" in out and len(chat.seen) == 16


def test_ollama_is_asked_to_keep_the_model_loaded_between_messages(monkeypatch):
    sent = fake_urlopen(monkeypatch, json.dumps({"done": True}))
    list(Agent(model="tiny", url="http://localhost:11434", out=io.StringIO()).chat([]))
    assert sent["body"]["keep_alive"] == "30m"


def test_warm_loads_the_model_ahead_of_the_first_message(monkeypatch):
    sent = fake_urlopen(monkeypatch, "{}")
    sprint.warm("http://localhost:11434", "tiny:1b").join(5)
    assert sent["url"] == "http://localhost:11434/api/generate" and sent["body"] == {"model": "tiny:1b", "keep_alive": "30m"}
    assert sprint.warm("http://localhost:8080/v1", "tiny:1b") is None  # only Ollama loads models on request


# ---------- Windows: PowerShell instead of bash, and nothing that only Unix has ----------

def test_on_windows_commands_are_run_through_powershell(monkeypatch):
    monkeypatch.setattr(sprint, "WINDOWS", True)
    monkeypatch.setattr(sprint.shutil, "which", lambda name: "C:/pwsh.exe" if name == "pwsh" else None)
    argv = sprint.shell_argv("ls; python hello.py", "C:\\Temp\\cwd.txt")
    assert argv[0] == "C:/pwsh.exe" and "-NoProfile" in argv and argv[-2] == "-Command"
    script = argv[-1]
    assert "ls; python hello.py" in script and "Get-Location" in script and "C:\\Temp\\cwd.txt" in script
    assert "$LASTEXITCODE" in script and script.rstrip().endswith("exit $__rc")
    monkeypatch.setattr(sprint.shutil, "which", lambda name: None)
    assert sprint.shell_argv("ls", "t")[0] == "powershell"  # the one every Windows has


def test_elsewhere_commands_are_run_through_bash():
    argv = sprint.shell_argv("ls -la", "/tmp/cwd")
    assert argv[0].endswith(("bash", "sh")) and argv[1] == "-c" and "ls -la" in argv[2] and "pwd > '/tmp/cwd'" in argv[2]


def test_stopping_a_command_uses_taskkill_on_windows_and_the_process_group_elsewhere(monkeypatch):
    class Proc:
        pid = 4242

    ran, killed = [], []
    monkeypatch.setattr(sprint.subprocess, "run", lambda argv, **kw: ran.append(argv))
    monkeypatch.setattr(sprint.os, "killpg", lambda pid, sig: killed.append(pid), raising=False)
    monkeypatch.setattr(sprint, "WINDOWS", True)
    sprint.kill_tree(Proc())
    assert ran == [["taskkill", "/F", "/T", "/PID", "4242"]] and killed == []
    monkeypatch.setattr(sprint, "WINDOWS", False)
    sprint.kill_tree(Proc())
    assert killed == [4242]


def test_the_model_is_told_which_shell_it_has(monkeypatch):
    a = agent(scripted())
    assert "PowerShell" not in a.system()
    monkeypatch.setattr(sprint, "WINDOWS", True)
    monkeypatch.setattr(sprint.shutil, "which", lambda name: None)  # a Windows with no Git Bash
    assert "PowerShell" in a.system()
    assert "PowerShell" in sprint.tools()[0]["function"]["description"]
    monkeypatch.setattr(sprint, "WINDOWS", False)
    assert "bash" in sprint.tools()[0]["function"]["description"]


def test_settings_live_in_appdata_on_windows_and_dot_config_elsewhere():
    assert str(sprint.config_dir({"APPDATA": "C:/Users/me/AppData/Roaming"}, windows=True)).replace("\\", "/") == "C:/Users/me/AppData/Roaming/sprint"
    assert sprint.config_dir({"XDG_CONFIG_HOME": "/x"}, windows=True) == sprint.Path("/x") / "sprint"
    assert sprint.config_dir({}, windows=False) == sprint.Path.home() / ".config" / "sprint"


def test_files_are_always_read_and_written_as_utf8(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write("café.txt", "naïve ░▒▓█\n")
    assert (tmp_path / "café.txt").read_bytes() == "naïve ░▒▓█\n".encode("utf-8")
    edit("café.txt", "naïve\n====\nnaïf\n")
    assert read("café.txt") == "naïf ░▒▓█"
    sprint.save_prompt("Sois bref ░")
    assert sprint.prompt_file().read_bytes() == "Sois bref ░\n".encode("utf-8") and sprint.load_prompt() == "Sois bref ░"


def test_copying_on_windows_gives_clip_text_it_can_read(monkeypatch):
    sent = {}
    monkeypatch.setattr(sprint, "WINDOWS", True)
    monkeypatch.setattr(sprint.shutil, "which", lambda name: "clip.exe" if name == "clip" else None)
    monkeypatch.setattr(sprint.subprocess, "run", lambda argv, **kw: sent.update(argv=argv, **kw))
    assert sprint.clipboard("héllo") is True
    assert sent["argv"] == ["clip"] and sent["input"] == "héllo".encode("utf-16")



def test_on_windows_git_bash_is_used_when_it_is_installed(monkeypatch):
    monkeypatch.setattr(sprint, "WINDOWS", True)
    monkeypatch.setattr(sprint.shutil, "which", lambda name: "C:/Program Files/Git/bin/bash.exe" if name == "bash" else None)
    argv = sprint.shell_argv("ls && cat a.txt", "C:\\Temp\\cwd.txt")
    assert argv[0] == "C:/Program Files/Git/bin/bash.exe" and argv[1] == "-c"
    assert "ls && cat a.txt" in argv[2] and "pwd -W > 'C:\\Temp\\cwd.txt'" in argv[2]  # -W: a path Windows understands
    assert "bash" in sprint.tools()[0]["function"]["description"] and "Git Bash" in agent(scripted()).system()


def test_the_wsl_launcher_is_not_mistaken_for_bash_but_gits_own_bash_is_found(monkeypatch):
    monkeypatch.setattr(sprint, "WINDOWS", True)
    where = {"bash": "C:/Windows/System32/bash.exe", "git": "C:/Program Files/Git/cmd/git.exe"}
    monkeypatch.setattr(sprint.shutil, "which", lambda name: where.get(name))
    monkeypatch.setattr(sprint.os.path, "isfile", lambda p: p.replace("\\", "/").endswith("Git/bin/bash.exe"))
    assert sprint.bash_path().replace("\\", "/") == "C:/Program Files/Git/bin/bash.exe"
    del where["git"]
    assert sprint.bash_path() is None and sprint.shell_argv("ls", "t")[0] == "powershell"


def test_old_powershell_gets_its_double_ampersands_translated(monkeypatch):
    monkeypatch.setattr(sprint, "WINDOWS", True)
    monkeypatch.setattr(sprint.shutil, "which", lambda name: None)
    assert "cd app; python main.py" in sprint.shell_argv("cd app && python main.py", "t")[-1]
    monkeypatch.setattr(sprint.shutil, "which", lambda name: "pwsh.exe" if name == "pwsh" else None)
    assert "cd app && python main.py" in sprint.shell_argv("cd app && python main.py", "t")[-1]  # the new one understands &&
