"""the low-level settings: how the model chooses its next word (temperature, top_p, ...). They go to the model server
with every request, a setting left alone keeps the model's own default, and the settings popup has a page for them"""
import curses
import io
import json

import sprint
from sprint import Agent
from sprint_tui import Screen, flat
from test_sprint import agent, fake_urlopen, scripted, texts
from test_tui import screen, typed

DONE = json.dumps({"message": {"content": "ok"}, "done": True})


def asked(monkeypatch, a):
    """what one request from this agent carries"""
    sent = fake_urlopen(monkeypatch, DONE)
    list(a.chat([{"role": "user", "content": "hi"}]))
    return sent["body"]


def local(**kw):
    return Agent(model="tiny", url="http://localhost:11434", out=io.StringIO(), **kw)


# ---------- the settings themselves ----------

def test_left_alone_nothing_about_sampling_is_sent_and_the_model_keeps_its_own(monkeypatch):
    assert set(asked(monkeypatch, local())["options"]) == {"num_ctx", "num_predict"}


def test_a_temperature_you_set_is_kept_and_goes_with_every_request(monkeypatch):
    a = local()
    sprint.command(a, "/config temperature 0.7")
    assert a.sampling == {"temperature": 0.7} and sprint.load_config()["temperature"] == 0.7
    assert asked(monkeypatch, a)["options"]["temperature"] == 0.7


def test_whole_numbers_stay_whole(monkeypatch):
    a = local()
    sprint.command(a, "/config top_k 40")
    sprint.command(a, "/config seed 7")
    sprint.command(a, "/config repeat_last_n -1")  # the one that may be below nought: the whole window
    sent = asked(monkeypatch, a)["options"]
    assert (sent["top_k"], sent["seed"], sent["repeat_last_n"]) == (40, 7, -1)
    assert all(type(sent[k]) is int for k in ("top_k", "seed", "repeat_last_n"))


def test_default_hands_a_setting_back_to_the_model(monkeypatch):
    a = local()
    sprint.command(a, "/config temperature 0.7")
    sprint.command(a, "/config top_p 0.9")
    sprint.command(a, "/config temperature default")
    assert a.sampling == {"top_p": 0.9} and "temperature" not in sprint.load_config()
    assert "temperature" not in asked(monkeypatch, a)["options"]


def test_a_value_out_of_range_or_not_a_number_is_refused_and_says_why():
    a = local()
    sprint.command(a, "/config temperature 0.7")
    for wrong in ("9", "-1", "hot", "nan", "inf"):
        said = texts(sprint.command(a, f"/config temperature {wrong}"))[0]
        assert ("cannot" in said or "needs" in said) and "temperature" in said, wrong
    assert "0 to 2" in texts(sprint.command(a, "/config temperature 9"))[0]  # and what would do
    assert "needs" in texts(sprint.command(a, "/config top_k 1.5"))[0]  # a count is a whole number
    assert a.sampling == {"temperature": 0.7} and sprint.load_config()["temperature"] == 0.7


def test_the_settings_come_back_at_the_next_start_and_a_spoiled_one_is_left_out():
    sprint.save_config(temperature=0.2, top_k=20, top_p="lots", min_p=7, seed=True)
    a = sprint.build_agent(sprint.arguments([]))
    assert a.sampling == {"temperature": 0.2, "top_k": 20}


def test_one_agents_settings_are_not_anothers():
    a, b = local(), local()
    sprint.command(a, "/config temperature 0.7")
    assert b.sampling == {} and Agent.sampling == {}


def test_config_lists_them_and_says_which_are_the_models_own():
    a = local()
    sprint.command(a, "/config top_p 0.9")
    rows = texts(sprint.command(a, "/config"))
    assert any("temperature" in r and "default" in r for r in rows) and any("top_p" in r and "0.9" in r for r in rows)
    assert all(any(k in r for r in rows) for k in sprint.SAMPLING)


def test_a_server_that_speaks_the_openai_way_gets_the_ones_it_knows(monkeypatch):
    a = Agent(model="tiny", url="http://localhost:8080/v1", out=io.StringIO())
    for k, v in (("temperature", "0.5"), ("top_p", "0.8"), ("seed", "3"), ("top_k", "40"), ("repeat_penalty", "1.1")):
        sprint.command(a, f"/config {k} {v}")
    sent = fake_urlopen(monkeypatch, 'data: {"choices": [{"delta": {"content": "ok"}}]}\n\ndata: [DONE]\n')
    list(a.chat([{"role": "user", "content": "hi"}]))
    body = sent["body"]
    assert (body["temperature"], body["top_p"], body["seed"]) == (0.5, 0.8, 3)
    assert "top_k" not in body and "repeat_penalty" not in body and "options" not in body  # those are Ollama's own


# ---------- their page in the settings popup ----------

def pages(s):
    return [name for name, _ in s.popup["tabs"]]


def keys(s):
    return [r["v"] for r in s.shown()[1]]


def line_of(s, key):
    return next(flat(r["s"]) for r in s.shown()[0] if r["v"] == key)


def advanced():
    s = screen()
    s.submit("/config")
    s.key(curses.KEY_RIGHT)
    return s


def test_settings_has_a_second_page_and_the_arrows_turn_to_it():
    s = screen()
    s.submit("/config")
    assert pages(s) == ["General", "Advanced"] and s.popup["tab"] == 0 and "model" in keys(s) and "temperature" not in keys(s)
    s.key(curses.KEY_RIGHT)
    assert s.popup["tab"] == 1 and keys(s) == list(sprint.SAMPLING) and s.popup["title"] == "Settings"
    s.key(curses.KEY_LEFT)
    assert s.popup["tab"] == 0 and "model" in keys(s)
    assert "pages" in flat(s.popup_lines(80, 30)[0][-1])  # the hint says how


def test_each_advanced_setting_says_what_it_is_and_that_it_is_the_models_own_until_set():
    s = advanced()
    line = line_of(s, "temperature")
    assert "temperature" in line and "default" in line and "random" in line
    assert all(len(flat(l)) == 80 for l in s.popup_lines(80, 30)[0])  # and every row fits the box


def test_enter_on_one_takes_a_number_and_comes_back_to_the_same_row_of_the_same_page():
    s = advanced()
    s.key(curses.KEY_DOWN)  # temperature -> top_p
    s.key("\n")
    assert s.popup["field"] is not None and s.popup["field"]["text"] == ""  # nothing set: nothing to edit
    assert any("0 to 1" in flat(r["s"]) for r in s.popup["rows"])  # what it may be
    typed(s, "0.9")
    s.key("\n")
    assert s.agent.sampling == {"top_p": 0.9} and sprint.load_config()["top_p"] == 0.9
    assert s.popup["title"] == "Settings" and s.popup["tab"] == 1 and s.popup["sel"] == 1
    assert "0.9" in line_of(s, "top_p") and "default" not in line_of(s, "top_p")


def test_the_number_already_set_is_there_to_edit_and_default_gives_it_back():
    s = advanced()
    s.key("\n")
    typed(s, "0.7")
    s.key("\n")
    s.key("\n")  # temperature again
    assert s.popup["field"]["text"] == "0.7"
    for _ in range(3):
        s.key(curses.KEY_BACKSPACE)
    typed(s, "default")
    s.key("\n")
    assert s.agent.sampling == {} and "default" in line_of(s, "temperature") and s.popup["tab"] == 1


def test_a_number_that_will_not_do_is_said_and_nothing_changes():
    s = advanced()
    s.key("\n")
    typed(s, "5")
    s.key("\n")
    assert s.agent.sampling == {} and "0 to 2" in s.note_text() and s.popup["tab"] == 1


def test_escape_from_the_number_goes_back_to_the_advanced_page():
    s = advanced()
    s.key(curses.KEY_DOWN)
    s.key("\n")
    s.key("\x1b")
    assert s.popup["title"] == "Settings" and s.popup["tab"] == 1 and s.popup["sel"] == 1


def test_a_switch_on_the_first_page_still_flips_and_stays_on_that_page():
    s = screen()
    s.submit("/config")
    for _ in range(5):
        s.key(curses.KEY_DOWN)  # model, url, ctx, steps, mode -> think
    s.key("\n")
    assert s.agent.think is True and s.popup["tab"] == 0 and s.popup["sel"] == 5
