"""websearch: the model looks something up by itself. opencode's tool (its name, its one field, the free Exa endpoint
it asks), with the answer cut down to what an 8K context can take: five results, a title, a link and a few words each"""
import io
import json
import urllib.error

import pytest

import sprint
from sprint import Agent, Call
from test_sprint import FakeResponse, scripted

EXA = "\n\n---\n\n".join([
    "Title: Coroutines and tasks\nURL: https://docs.python.org/3/library/asyncio-task.html\nPublished: N/A\nAuthor: N/A\n"
    "Highlights:\n## Running tasks concurrently\n\nawaitable asyncio.gather(*aws)\n: Run awaitable objects   concurrently. " + "x" * 900,
    "Title: N/A\nURL: https://superfastpython.com/asyncio-gather/\nPublished: 2024-04-11T00:00:00.000Z\nText:\nHow to use gather.",
    "Title: No link here\nPublished: N/A\nHighlights:\nnothing to follow"])
DDG = """
<h2 class="result__title">
 <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.pythontutorial.net%2Fasyncio%2Dgather%2F&amp;rut=0a76">Python asyncio.gather() - Run <b>Multiple</b> Operations</a>
</h2>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">In this <b>tutorial</b>, you&#x27;ll learn how to use
   <b>asyncio.gather</b>().</a>
<h2 class="result__title">
 <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fduckduckgo.com%2Fy.js%3Fad_domain%3Dshop.example&amp;rut=1">An advertisement</a>
</h2>
<a class="result__snippet" href="//duckduckgo.com/y.js">Buy it now.</a>
<h2 class="result__title">
 <a rel="nofollow" class="result__a" href="https://realpython.com/async-io-python/">Async IO in Python</a>
</h2>
"""


def exa_reply(text, **more):
    return "event: message\ndata: " + json.dumps({"result": dict({"content": [{"type": "text", "text": text}]}, **more), "jsonrpc": "2.0", "id": 1}) + "\n\n"


def web(monkeypatch, exa=None, ddg=None):
    """the two services, played: each answers with its text, or raises what it was given -> the requests that were made"""
    asked = []

    def urlopen(req, timeout=None):
        asked.append(req)
        given = exa if "exa.ai" in req.full_url else ddg
        if given is None or isinstance(given, Exception):
            raise given or urllib.error.URLError("no network")
        return FakeResponse(given.encode())

    monkeypatch.setattr(sprint.urllib.request, "urlopen", urlopen)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    return asked


# ---------- the search itself ----------

def test_a_search_asks_exa_as_opencode_does_and_lists_what_it_found(monkeypatch):
    asked = web(monkeypatch, exa=exa_reply(EXA))
    said = sprint.websearch("python asyncio gather")
    sent = json.loads(asked[0].data)
    assert len(asked) == 1 and asked[0].full_url == "https://mcp.exa.ai/mcp" and sent["method"] == "tools/call"
    assert sent["params"] == {"name": "web_search_exa", "arguments": {"query": "python asyncio gather", "numResults": sprint.SEARCH_TOP}}
    assert "event-stream" in asked[0].get_header("Accept") and asked[0].get_header("Content-type") == "application/json"
    lines = said.splitlines()
    assert lines[0] == "1. Coroutines and tasks" and lines[1] == "   https://docs.python.org/3/library/asyncio-task.html"
    assert lines[2] == "   Running tasks concurrently awaitable asyncio.gather(*aws) : Run awaitable objects concurrently.…"  # one line, cut
    assert lines[3] == "2. superfastpython.com" and lines[4] == "   https://superfastpython.com/asyncio-gather/" and lines[5] == "   How to use gather."
    assert "No link here" not in said and "Published" not in said and "N/A" not in said


def test_what_comes_back_is_small_enough_for_a_small_context(monkeypatch):
    block = "Title: A page about it number {n}\nURL: https://example.com/a/long/path/to/page-{n}\nHighlights:\n" + "word " * 2000
    web(monkeypatch, exa=exa_reply("\n\n---\n\n".join(block.format(n=n) for n in range(8))))
    said = sprint.websearch("anything")
    assert said.count("https://example.com/") == sprint.SEARCH_TOP == 5 and len(said) < 2000
    assert all(len(line) <= 3 + sprint.SEARCH_WORDS + 1 for line in said.splitlines()) and "word…" in said  # cut, and said to be
    assert said.splitlines()[-1] == "To read one of these pages, fetch its URL."


def test_a_key_of_your_own_goes_with_the_request(monkeypatch):
    asked = web(monkeypatch, exa=exa_reply(EXA))
    monkeypatch.setenv("EXA_API_KEY", "k-123")
    sprint.websearch("x")
    assert asked[0].full_url == "https://mcp.exa.ai/mcp?exaApiKey=k-123"


@pytest.mark.parametrize("exa", [urllib.error.HTTPError("https://mcp.exa.ai/mcp", 429, "Too Many Requests", {}, None),
                                 urllib.error.URLError("no route"), exa_reply("rate limited", isError=True), exa_reply(""),
                                 "not json at all", json.dumps({"error": {"code": -32000, "message": "busy"}})],
                         ids=["too many requests", "no route", "an error of its own", "nothing", "not json", "a refusal"])  # (named: Python 3.9 cannot name an HTTPError)
def test_when_exa_does_not_answer_duckduckgo_is_asked_instead(monkeypatch, exa):
    asked = web(monkeypatch, exa=exa, ddg=DDG)
    said = sprint.websearch("python asyncio gather & more")
    assert [r.full_url for r in asked] == ["https://mcp.exa.ai/mcp", "https://html.duckduckgo.com/html/?q=python+asyncio+gather+%26+more"]
    lines = said.splitlines()
    assert lines[:3] == ["1. Python asyncio.gather() - Run Multiple Operations", "   https://www.pythontutorial.net/asyncio-gather/",
                         "   In this tutorial, you'll learn how to use asyncio.gather()."]
    assert lines[3:5] == ["2. Async IO in Python", "   https://realpython.com/async-io-python/"] and "advertisement" not in said


def test_nothing_found_says_so_in_opencodes_words(monkeypatch):
    web(monkeypatch, exa=exa_reply(""), ddg="<html>no results</html>")
    assert sprint.websearch("zxqv") == "No search results found. Please try a different query."


def test_duckduckgos_challenge_for_people_is_a_failure_and_not_an_empty_answer(monkeypatch):
    """asked a few times in a row it sends a puzzle instead of results; that is not "nothing found" """
    puzzle = '<div class="anomaly-modal__box">Select all squares containing a duck</div>'
    web(monkeypatch, exa=urllib.error.HTTPError("https://mcp.exa.ai/mcp", 429, "Too Many Requests", {}, None), ddg=puzzle)
    with pytest.raises(OSError, match="429"):  # what went wrong first is what is said
        sprint.websearch("x")
    web(monkeypatch, exa=exa_reply(""), ddg=puzzle)  # Exa did answer, with nothing
    assert sprint.websearch("x") == sprint.NO_RESULTS


def test_with_neither_service_reachable_the_search_fails_with_the_reason(monkeypatch):
    web(monkeypatch)
    with pytest.raises(OSError, match="no network"):
        sprint.websearch("x")
    with pytest.raises(ValueError, match="nothing to search for"):
        sprint.websearch("   ")


# ---------- the tool: how a model calls it ----------

def test_it_is_a_tool_of_its_own_called_websearch_with_one_field_and_the_year():
    tool = next(t["function"] for t in sprint.tools() if t["function"]["name"] == "websearch")
    assert list(tool["parameters"]["properties"]) == ["query"] and tool["parameters"]["required"] == ["query"]
    assert tool["description"].startswith("Search the web") and str(sprint.time.localtime().tm_year) in tool["description"]
    assert "websearch" not in [t["function"]["name"] for t in sprint.tools(search=False)]


@pytest.mark.parametrize("name", ["websearch", "web_search", "WebSearch", "search_web"])
def test_a_native_call_by_any_of_its_names_is_the_search(name):
    c = sprint.from_args(name, {"query": "newest python version"}, 0, 0)
    assert (c.tool, c.arg, c.body) == ("websearch", "newest python version", "")


def test_a_block_typed_out_is_the_search_and_a_bare_search_block_is_not():
    c = sprint.parse("```websearch newest python version\n```")
    assert (c.tool, c.arg) == ("websearch", "newest python version")
    c = sprint.parse("```websearch\nnewest python version\nand more\n```")
    assert (c.tool, c.arg) == ("websearch", "newest python version")
    assert sprint.parse("```search TODO\n```") is None  # that may as well mean the files: it is not sent to the web


def test_a_model_without_tool_calls_is_shown_the_block_unless_search_is_off():
    a = Agent(chat=scripted(), out=io.StringIO())
    a.tools = False
    assert "```websearch what to look up\n```" in a.system()
    a.websearch = False
    assert "```websearch" not in a.system() and "```fetch https://example.com\n```" in a.system()


def test_switched_off_it_is_not_offered_and_a_call_is_turned_down(monkeypatch):
    asked = web(monkeypatch, exa=exa_reply(EXA))
    a = Agent(chat=scripted(), out=io.StringIO(), mode="auto")
    assert a.websearch and "websearch" in json.dumps(sprint.tools(a.questions, a.lists, a.websearch))
    a.websearch = False
    assert a.extra() == len(json.dumps(sprint.tools(a.questions, a.lists, False)))
    assert a.do(Call("websearch", "x", "", 0, 0)).startswith("error: web search is switched off") and not asked


def test_the_setting_switches_it_and_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(sprint, "CONFIG", tmp_path / "config.json")
    a = Agent(chat=scripted(), out=io.StringIO())
    assert sprint.set_config(a, "websearch", "off") == "" and a.websearch is False and sprint.load_config()["websearch"] is False
    assert sprint.set_config(a, "websearch", "on") == "" and a.websearch is True
    assert any(flat == ["  websearch        ", "true"] for flat in ([t for t, _ in row] for row in sprint.cmd_config(a, "")))


# ---------- the turn: asked first unless the mode is auto ----------

def test_in_auto_mode_a_turn_searches_and_answers_from_what_it_found(monkeypatch):
    web(monkeypatch, exa=exa_reply(EXA))
    chat = scripted("```websearch python asyncio gather\n```", "It runs awaitables concurrently.")
    a = Agent(chat=chat, out=io.StringIO(), mode="auto")
    assert a.run("look up asyncio gather") == "It runs awaitables concurrently."
    assert "1. Coroutines and tasks" in chat.seen[1][-1]["content"] and "docs.python.org" in chat.seen[1][-1]["content"]


def test_in_ask_mode_it_is_asked_about_first_and_always_frees_every_search(tmp_path, monkeypatch):
    monkeypatch.setattr(sprint, "CONFIG", tmp_path / "config.json")
    asked, wanted = web(monkeypatch, exa=exa_reply(EXA)), []
    a = Agent(chat=scripted(), out=io.StringIO(), mode="ask")
    a.ask = lambda c: wanted.append(c.arg) or "always"
    assert a.do(Call("websearch", "first thing", "", 0, 0)).startswith("1. Coroutines")
    assert wanted == ["first thing"] and a.allow == ["websearch *"] and a.target(Call("websearch", "first thing", "", 0, 0)) == "first thing"
    assert a.do(Call("websearch", "another thing", "", 0, 0)).startswith("1. Coroutines") and wanted == ["first thing"] and len(asked) == 2
    a.allow, a.ask = [], lambda c: False
    monkeypatch.setattr(sprint.sys.stdin, "isatty", lambda: True)
    assert a.do(Call("websearch", "third", "", 0, 0)) == "denied by the user" and len(asked) == 2


def test_a_search_can_be_freed_by_hand_and_is_listed_with_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(sprint, "CONFIG", tmp_path / "config.json")
    a = Agent(chat=scripted(), out=io.StringIO(), mode="ask")
    sprint.change_allowed(a, "allow websearch *")
    assert a.allow == ["websearch *"]
    rows = ["".join(t for t, _ in row) for row in sprint.cmd_permissions(a, "")]
    assert any(r.startswith("  websearch") and "asks first" in r for r in rows)


# ---------- the screen ----------

def screen(*replies, **kw):
    import sprint_tui as tui
    kw.setdefault("mode", "auto")
    return tui.Screen(Agent(chat=scripted(*replies), out=io.StringIO(), **kw))


def flat(spans):
    return "".join(t for t, _ in spans)


def test_a_search_is_a_window_headed_by_what_was_looked_up_with_the_results_in_it():
    s = screen()
    s.show_call(Call("websearch", "newest python version", "", 0, 0))
    top = s.window(s.items[-1], 70)[0][1]
    assert "websearch" in flat(top) and ("  newest python version", "") in top  # the question is words: no path, no link
    assert s.verb() == "searching"
    s.show_result("1. Python 3.14\n   https://www.python.org/downloads/\n   The newest release.\nTo read one of these pages, fetch its URL.")
    text = [flat(spans) for _, spans, _ in s.window(s.items[-1], 70)]
    assert " done " in text[0] and text[1].startswith("│ 1. Python 3.14") and "https://www.python.org/downloads/" in text[2]
    assert [flat(spans) for _, spans in s.activity()] == ["✓ websearch newest python version"]
    assert s.activity()[0][1][2] == ("newest python version", "")


def test_the_mode_window_says_whether_a_search_is_asked_about():
    s = screen(mode="ask")
    s.submit("/mode")
    rows = [flat(spans) for spans in s.popup_lines(70, 30)[0]]
    assert any("websearch" in r and "asks first" in r for r in rows) and any("fetch " in r and "asks first" in r for r in rows)
    assert len({r.index("asks first") for r in rows if "asks first" in r}) == 1  # the longer name keeps the column straight


def test_the_settings_window_has_the_switch_and_enter_flips_it(tmp_path, monkeypatch):
    monkeypatch.setattr(sprint, "CONFIG", tmp_path / "config.json")
    s = screen()
    s.submit("/config")
    keys = [r["v"] for r in s.popup["tabs"][0][1] if r["v"]]
    assert "websearch" in keys
    s.config_pick("websearch", True, keys.index("websearch"))
    assert s.agent.websearch is False and sprint.load_config()["websearch"] is False
    assert any("websearch" in flat(spans) and " off" in flat(spans) for spans in s.popup_lines(70, 40)[0])
