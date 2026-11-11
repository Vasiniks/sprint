"""the top of the right-hand panel: the context window and the session, laid out as labelled rows. How much it says
depends on how tall the screen is: what the agent did (and the plan) must keep their room"""
import pytest

import sprint_tui as tui
from sprint_tui import flat
from test_popups import Scr, finish, scr, screen  # noqa: F401  (scr is a fixture)


def head(s, inner=28, h=50):
    return [flat(r) for r in s.side_head(inner, h)]


def worked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = screen("```shell\necho a\n```", "```shell\nexit 3\n```", "all done")
    s.submit("go")
    finish(s)
    return s


def test_on_a_tall_screen_the_context_comes_with_a_legend_of_what_fills_it(tmp_path, monkeypatch):
    s = worked(tmp_path, monkeypatch)
    rows = head(s)
    assert rows[0].startswith("Context") and rows[0].endswith("%") and len(rows[0]) == 28
    assert set(rows[1]) <= set("█·▒") and len(rows[1]) == len(rows[2]) == 28  # the window itself, as before
    assert " of " in rows[3] and "tokens" in rows[3]
    legend = " ".join(rows[4:7])
    for what in ("system", "tools", "you", "replies", "results", "reply"):
        assert what in legend, what
    assert all(len(r) <= 28 for r in rows)
    spans = s.side_head(28, 50)[4]
    assert spans[0] == ("█ ", tui.sprint.SHADE["system prompt"])  # each in the colour it has in the window


def test_then_the_session_as_labelled_rows(tmp_path, monkeypatch):
    s = worked(tmp_path, monkeypatch)
    s.agent.rate, s.agent.took = 61.4, 5.52
    rows = head(s)
    at = rows.index(next(r for r in rows if r.startswith("Session")))
    told = {r.split()[0]: r for r in rows[at + 1:]}
    assert list(told) == ["model", "thinking", "showthink", "effort", "plan", "speed", "tokens", "tools", "time"]
    assert tui.short(s.agent.model)[:10] in told["model"]
    assert not any(tui.MODE_ICON[m] in r for r in rows for m in tui.MODES)  # the mode is by the prompt already: not here too
    assert told["thinking"].split()[1] == "off" and told["effort"].split()[1] == s.agent.effort
    assert told["showthink"].split()[1] == "off" and told["plan"].split()[1] == "off"
    s.showthink = s.plan = True
    again = {r.split()[0]: r for r in head(s)[at + 1:]}
    assert again["showthink"].split()[1] == "on" and again["plan"].split()[1] == "on"
    assert dict(s.side_head(28, 50)[at + 3])["on"] == "ok"  # on is green, off is quiet
    s.showthink = s.plan = False
    assert "61 tok/s" in told["speed"] and "5.5s" in told["speed"]
    assert "↑" in told["tokens"] and "↓" in told["tokens"]
    assert "2 calls" in told["tools"] and "1 failed" in told["tools"]
    labels = {r.index(r.split()[1]) for r in rows[at + 1:]}
    assert len(labels) == 1  # the values line up
    s.agent.think = True
    assert head(s)[at + 2].split()[1] == "on"


def test_the_fuller_the_window_the_louder_the_figure(tmp_path, monkeypatch):
    s = worked(tmp_path, monkeypatch)
    role = lambda: s.side_head(28, 50)[0][-1][1]
    monkeypatch.setattr(s.agent, "used", lambda: s.agent.ctx * 0.2)
    assert role() == "ok"
    monkeypatch.setattr(s.agent, "used", lambda: s.agent.ctx * 0.7)
    assert role() == "warn"
    monkeypatch.setattr(s.agent, "used", lambda: s.agent.ctx * 0.95)
    assert role() == "err"


def test_a_shorter_screen_says_less_so_the_activity_keeps_its_room(tmp_path, monkeypatch):
    s = worked(tmp_path, monkeypatch)
    tall, mid, short = head(s, h=50), head(s, h=36), head(s, h=30)
    assert len(tall) > len(mid) > len(short) == 6
    assert any(r.startswith("Session") for r in mid) and not any("system" in r for r in mid)  # no legend
    assert [r.split()[0] for r in mid[mid.index(next(r for r in mid if r.startswith("Session"))) + 1:]] == \
        ["model", "thinking", "showthink", "plan", "speed", "tokens", "tools"]
    assert not any(r.startswith("Session") for r in short)  # the six lines it always had
    assert tui.short(s.agent.model)[:10] in short[5] and not any(tui.MODE_ICON[m] in short[5] for m in tui.MODES)
    s.agent.think = s.plan = s.showthink = True
    assert "think" in head(s, h=30)[5] and "plan" in head(s, h=30)[5]  # what is switched on, after the model
    s.agent.think = s.plan = s.showthink = False
    assert not any("system" in r for r in head(s, inner=20, h=50))  # nor a legend where the panel is too narrow for it


def test_the_activity_and_the_plan_start_under_it_wherever_that_is(tmp_path, monkeypatch):
    s = worked(tmp_path, monkeypatch)
    s.sidebar = True
    monkeypatch.setattr(tui.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(tui.curses, "color_pair", lambda n: n << 8)
    big = Scr(50, 140)
    s.draw(big)
    w, lw, side = s.geo
    col = [big.text(y)[w - side + 1:].rstrip() for y in range(big.h)]
    rows = len(s.side_head(side - 3, 50))
    act = col.index("Activity")
    assert act == rows + 2 and col[act + 1].startswith("✓ shell") and col[act + 2].startswith("✗ shell")
    assert ("side", act + 1) in s.hits  # and a click still finds the call
    s.plan = True
    s.items.append({"t": "plan", "steps": ["one", "two"], "at": 0})
    s.plan_h = 49  # dragged as far up as it will go
    s.draw(big)
    col = [big.text(y)[w - side + 1:].rstrip() for y in range(big.h)]
    assert next(y for y, r in enumerate(col) if r.startswith("Plan")) == act + 3  # one line of activity is always left
