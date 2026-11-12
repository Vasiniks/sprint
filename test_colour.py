"""what the model's words look like: each kind of emphasis and structure in a colour of its own, and its reasoning in a
box that no tool call has"""
import curses
import io

import pytest

import sprint
import sprint_tui as tui
from sprint import Agent, Call
from sprint_tui import ROLE, THEMES, Screen, code, flat, prose, wrap
from test_sprint import scripted

ITALIC = getattr(curses, "A_ITALIC", 0)


def roles(spans):
    return {t.strip(): r for t, r in spans if t.strip()}


def one(line):
    return prose(line)[0]


def hue(role):
    return ROLE[role][0]


# ---------- emphasis ----------

def test_bold_is_bold_and_has_a_colour_of_its_own_in_both_spellings():
    r = roles(one("This **really** matters, and __so does this__."))
    assert r["**really**"] == "strong" and r["__so does this__"] == "strong" and r["This"] == ""
    assert hue("strong") in range(1, 7) and ROLE["strong"][1] & curses.A_BOLD
    assert ROLE["bold"] == (0, curses.A_BOLD)  # the plain bold the rest of the screen uses is left alone


def test_italic_has_another_colour_and_is_italic_where_curses_can():
    r = roles(one("It is *quite* slow, _very slow_ even (*yes*)."))
    assert r["*quite*"] == "em" and r["_very slow_"] == "em" and r["*yes*"] == "em"
    assert hue("em") in range(1, 7) and ROLE["em"][1] == ITALIC
    assert len({hue(r) for r in ("em", "strong", "code", "path", "url")}) == 5  # no two alike, with or without italics


@pytest.mark.parametrize("line", [
    "rename snake_case_names and my_var_ here", "so 2 * 3 * 4 is 24", "and a*b*c too", "call f(*args, **kwargs) now",
    "the *.py and *.md files", "a lone * star", "an _ alone _ here", "5 * x and y * 6"])
def test_stars_and_underscores_inside_ordinary_text_are_not_emphasis(line):
    assert {r for _, r in one(line)} <= {"", "path"} and flat(one(line)) == line


def test_names_with_underscores_stay_whole_paths_or_plain():
    assert roles(one("open file_name.py and __init__.py"))["file_name.py"] == "path"
    assert roles(one("open file_name.py and __init__.py"))["__init__.py"] == "path"
    assert "em" not in {r for _, r in one("set max_tokens_ and _private_name then")}
    assert {r for _, r in one("it reads self.__dict__ and a.__class__ there")} == {""}


def test_bold_wins_over_italic_and_three_stars_are_one_piece():
    assert one("**a** *b* ***c***") == [("**a**", "strong"), (" ", ""), ("*b*", "em"), (" ", ""), ("***c***", "strong")]


def test_struck_text_is_dim():
    assert roles(one("use ~~the old way~~ the new way"))["~~the old way~~"] == "struck"
    assert ROLE["struck"] == (0, curses.A_DIM) and {r for _, r in one("about ~5 or ~~ nothing")} == {""}


# ---------- structure ----------

def test_each_level_of_heading_has_its_own_bold_colour_and_dim_marks():
    lines = prose("# Plan\n## Steps\n### Detail\n##### Deeper")
    assert lines[0] == [("# ", "dim"), ("Plan", "h1")] and lines[1] == [("## ", "dim"), ("Steps", "h2")]
    assert lines[2] == [("### ", "dim"), ("Detail", "h3")] and lines[3] == [("##### ", "dim"), ("Deeper", "h3")]
    assert len({hue("h1"), hue("h2"), hue("h3")}) == 3 and all(ROLE[h][1] & curses.A_BOLD and hue(h) for h in ("h1", "h2", "h3"))


def test_a_short_line_ending_in_a_colon_is_still_a_title():
    assert prose("Steps:") == [[("Steps:", "title")]]


def test_bullets_and_numbers_are_coloured_marks_each_their_own():
    for mark in ("- ", "* ", "+ ", "  - "):
        assert one(mark + "first do a thing") == [(mark, "bullet"), ("first do a thing", "")]
    for mark in ("1. ", "2) ", "  10. "):
        assert one(mark + "then another") == [(mark, "ordinal"), ("then another", "")]
    assert hue("ordinal") in range(1, 7) and hue("ordinal") != hue("bullet")
    assert one("+1 from me")[0][1] == "" and one("3.5 seconds")[0][1] == ""


def test_task_boxes_show_what_is_done_and_what_is_not():
    assert one("- [ ] write the test")[:2] == [("- ", "bullet"), ("[ ]", "warn")]
    assert one("- [x] read the file")[:2] == [("- ", "bullet"), ("[x]", "ok")] and one("1. [X] done")[1] == ("[X]", "ok")
    assert "ok" not in {r for _, r in one("- [x]no space") + one("see [x] here")}


def test_a_block_quote_gets_a_coloured_bar_and_dim_text_that_is_still_marked():
    spans = one("> keep `this` in mind")
    assert spans[0] == ("▌", "bar") and hue("bar") in range(1, 7)
    assert spans[1:] == [(" keep ", "dim"), ("`this`", "code"), (" in mind", "dim")]
    assert len(flat(spans)) == len("> keep `this` in mind")  # the bar takes the place of the mark
    assert one(">> twice")[0] == ("▌▌", "bar") and one("a > b")[0][1] == ""
    assert one("> Careful:") == [("▌", "bar"), (" Careful:", "dim")]  # not a title


@pytest.mark.parametrize("line", ["---", "***", "___", "- - -", "  -----  "])
def test_a_rule_is_a_dim_line(line):
    (text, role), = one(line)
    assert role == "dim" and set(text) == {"─"} and len(text) >= 3


@pytest.mark.parametrize("line", ["--", "-- two", "--- three", "a --- b", "**"])
def test_what_only_looks_like_a_rule_is_not_one(line):
    assert "─" not in flat(one(line))


def test_labels_that_open_a_line_take_the_colour_of_what_they_say():
    for word, role in (("Note", "note"), ("Tip", "note"), ("Warning", "warn"), ("Caution", "warn"), ("Error", "err"),
                       ("Failed", "err"), ("Done", "ok"), ("Fixed", "ok"), ("error", "err"), ("NOTE", "note"), ("TODO", "warn")):
        assert one(word + ": the cache is stale now, so run it again") == [(word + ":", role), (" the cache is stale now, so run it again", "")]
    assert hue("note") in range(1, 7)
    assert one("- Warning: this deletes the folder and all of it")[:2] == [("- ", "bullet"), ("Warning:", "warn")]
    assert one("> Note: quoted")[:2] == [("▌", "bar"), (" Note:", "note")]


@pytest.mark.parametrize("line", [
    "I should note: this is the long way round, not the short one", "Done with it and nothing else is left to do now",
    "Notes: there are several of them and none of them matter", "Error:x is not a label without the space after it",
    "Model: qwen3 is a key and a value but outside a list it stays plain", "Then run: pytest and see what it says about it"])
def test_a_colon_in_a_sentence_is_not_a_label(line):
    assert {r for _, r in one(line)} == {""}


def test_a_list_item_that_opens_with_a_key_colours_the_key():
    assert one("- Model: qwen3") == [("- ", "bullet"), ("Model:", "label"), (" qwen3", "")]
    assert one("2. Free room: 4,096 tokens")[:2] == [("2. ", "ordinal"), ("Free room:", "label")]
    assert hue("label") in range(1, 7)
    for line in ("- see https://example.com for more", "- at 12:30 we stop", "- one two three four: too long to be a key",
                 "- std::move is fine", "- C:\\temp is the folder", "- sprint.py: the agent"):
        assert "label" not in {r for _, r in one(line)}, line


def test_ordinary_sentences_stay_plain():
    text = ("I read the file and found the problem. The loop ran one time too many, so the last item was counted twice.\n"
            "It's fixed now: the range stops one earlier, and all 366 tests pass (it took 1.2s, about 5% longer).\n"
            "Nothing else changed - the \"old\" behaviour is the same, and you don't need to do anything.")
    assert {r for line in prose(text) for _, r in line} == {""}


# ---------- code ----------

def test_code_also_colours_what_is_called_decorators_and_constants():
    r = roles(code("@cache.memo"))
    assert r["@cache.memo"] == "deco"
    r = roles(code("    total = fetch_all(MAX_ROWS, x) if ok else None  # why"))
    assert r["fetch_all"] == "func" and r["MAX_ROWS"] == "const" and r["if"] == "kw" and r["None"] == "kw" and r["# why"] == "comment"
    assert "total = " in [t.lstrip() for t, r in code("    total = fetch_all(MAX_ROWS, x)") if r == ""]
    assert "func" not in roles(code("if (x) return y")).values() and roles(code("if (x) return y"))["if"] == "kw"
    assert "deco" not in roles(code("mail me@example.com")).values() and "const" not in roles(code("a HTTPServer and I")).values()
    assert len({hue("func"), hue("deco"), hue("const")}) == 3 and all(hue(x) in range(1, 7) for x in ("func", "deco", "const"))


def test_fences_still_go_through_code_and_prose_marks_stay_out_of_them():
    lines = prose("Here:\n```python\nx = a * b * c  # *not* italic\n```\n*italic* again")
    assert "em" not in {r for _, r in lines[2]} and roles(lines[2])["# *not* italic"] == "comment"
    assert lines[4][0] == ("*italic*", "em") and lines[1] == [("```python", "dim")]


# ---------- the contract with the rest of the screen ----------

SAMPLE = ("# Title\n## Sub\n### Deep\nSteps:\n- a **bold** and *slanted* and ~~gone~~ item with `code` in src/app.py\n1. [x] Key: value\n"
          "> quoted *words* here\n---\nNote: see https://example.com\n```\n@wraps\ndef f(X_MAX): return g(1)\n```\nplain")


def test_every_role_that_prose_uses_is_known_and_themes_can_recolour_it():
    used = {r for line in prose(SAMPLE) for _, r in line}
    assert {"h1", "h2", "h3", "title", "bullet", "ordinal", "ok", "label", "strong", "em", "struck", "code", "path", "bar", "note", "url",
            "deco", "kw", "func", "const", "number"} <= used
    assert used <= set(ROLE)
    assert all(ROLE[r][0] in (0, 1, 2, 3, 4, 5, 6, 9) for r in used)  # only the colours a theme remaps
    assert all(set(range(1, 7)) <= set(t) for name, t in THEMES.items() if name != "terminal")


def test_the_marks_stay_in_the_text_and_nothing_is_lost():
    for src, line in zip(SAMPLE.split("\n"), prose(SAMPLE)):
        if src != "---":
            assert flat(line).replace("▌", ">") == src
        assert all(t for t, _ in line) or not src  # no empty spans


def test_coloured_lines_still_wrap_to_the_width_with_their_roles():
    line = one("- Key: a **bold thing** and an *italic thing* and ~~an old thing~~ and `some code` to wrap around")
    rows = wrap(line, 20)
    assert len(rows) > 3 and all(len(flat(r)) <= 20 for r in rows)
    assert " ".join(flat(r).strip() for r in rows) == flat(line).strip()
    seen = [r for row in rows for _, r in row]
    assert {"bullet", "label", "strong", "em", "struck", "code"} <= set(seen)


# ---------- the reasoning box ----------

def screen():
    return Screen(Agent(chat=scripted(), out=io.StringIO(), mode="auto"))


def thought(text, w=60, **item):
    s = screen()
    s.show_thought(text)
    s.items[-1].update(item)
    return s.window(s.items[-1], w)


def test_an_open_thought_sits_in_a_dashed_square_frame_of_its_own():
    rows = thought("First I should read the file.\nThen change the word.", open=True, secs=1.24)
    text = [flat(spans) for _, spans, _ in rows]
    assert all(len(t) == 60 for t in text) and len(text) == 4
    assert text[0] == "┌┄ thought  14 tokens · 10 words " + "┄" * 26 + "┐" and text[-1] == "└" + "┄" * 58 + "┘"  # its size both ways
    assert text[1] == "┆ First I should read the file." + " " * 28 + "┆" and text[2].startswith("┆ Then change the word. ")
    assert not set("╭╮╰╯│─▸▾") & set("".join(text))  # nothing of a tool window's frame
    assert "done" not in text[0] and "1.2s" not in text[0] and "think " not in text[0]  # reasoning is not an action: no status, no time


def test_a_thought_is_drawn_quietly_and_its_frame_like_any_frame():
    rows = thought("Check the tests first.", open=True)
    assert rows[0][1][:3] == [("┌┄ ", "frame"), ("thought", "dim"), ("  6 tokens · 4 words ", "dim")]
    assert rows[1][1][0] == ("┆ ", "frame") and rows[1][1][1] == ("Check the tests first.", "thought") and rows[1][1][-1] == (" ┆", "frame")
    assert {r for _, r in rows[2][1]} == {"frame"} and all(item is rows[0][2] for _, _, item in rows)
    assert ROLE["thought"] == (0, curses.A_DIM | ITALIC)


def test_a_long_thought_is_wrapped_never_cut():
    said = "weigh the first way against the second and then the third " * 4 + "\n\n\tand one more line"
    text = [flat(spans) for _, spans, _ in thought(said, w=40, open=True)]
    assert all(len(t) == 40 and t[:2] == "┆ " and t[-2:] == " ┆" for t in text[1:-1]) and len(text[0]) == len(text[-1]) == 40
    assert "…" not in "".join(text) and " ".join(" ".join(t[2:-2] for t in text[1:-1]).split()) == " ".join(said.split())
    assert "┆" + " " * 38 + "┆" in text and "┆     and one more line" in text[-2]  # an empty line stays, a tab is four spaces
    assert "48 words" in text[0]


def test_a_folded_thought_is_one_dashed_line_that_matches_the_box():
    (kind, spans, item), = thought("First I should read the file.\nThen change the word.")
    assert flat(spans).rstrip() == "  ┄ thought  14 tokens · 10 words" and len(flat(spans)) == 60 and spans[0] == ("  ┄ ", "frame")
    assert "▸" not in flat(spans) and "First" not in flat(spans)


def test_showthink_opens_every_thought_into_the_dashed_box():
    s = screen()
    s.show_thought("one two three")
    s.showthink = True
    text = [flat(spans) for _, spans, _ in s.window(s.items[-1], 50)]
    assert text[0].startswith("┌┄ thought  3 tokens · 3 words ┄") and text[1].startswith("┆ one two three") and text[2].startswith("└┄")


def test_tool_windows_are_exactly_as_they_were(monkeypatch):
    now, s = [100.0], screen()
    monkeypatch.setattr(tui.time, "time", lambda: now[0])
    s.show_call(Call("shell", "", "seq 1 9", 0, 0))
    now[0] = 101.24
    s.show_result("\n".join(str(i) for i in range(1, 10)))
    rows = s.window(s.items[-1], 44)
    assert [flat(spans) for _, spans, _ in rows] == [
        "╭─ ▸ shell  seq 1 9 ─────────── done 1.2s ─╮", "│ $ seq 1 9                                │"] + [
        f"│ {i}                                        │" for i in range(1, 6)] + ["╰─ … +4 lines · ctrl+o ────────────────────╯"]
    assert rows[0][1] == [("╭─ ▸ ", "frame"), ("shell", "bold"), ("  seq 1 9", ""), ("", "dim"), (" ───────────", "frame"), (" done ", "ok"),
                          ("1.2s ", "dim"), ("─╮", "frame")]
    s.items[-1]["open"] = True
    text = [flat(spans) for _, spans, _ in s.window(s.items[-1], 44)]
    assert text[0] == "╭─ ▾ shell  seq 1 9 ─────────── done 1.2s ─╮" and text[-1] == "╰─ ctrl+o to collapse ─────────────────────╯" and len(text) == 12


def test_copying_out_of_a_thought_leaves_its_frame_behind(monkeypatch):
    held = []
    monkeypatch.setattr(sprint, "clipboard", lambda text: held.append(text) or True)
    s = screen()
    s.screen = {y: (0, flat(spans)) for y, (_, spans, _) in enumerate(thought("read the file\n  then change it", w=40, open=True))}
    s.copy_range((1, 0), (2, 39))
    assert held == ["read the file\n  then change it"]
