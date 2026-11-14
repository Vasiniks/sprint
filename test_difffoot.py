"""the bottom edge of a folded diff says what is not shown as a diff would: lines put in, in green, and lines taken
out, in red. Anything else that is folded (a command's output, a file read) keeps its plain count"""
import io

from sprint import Agent, Call
from sprint_tui import Screen, box_foot, flat
from test_sprint import scripted


def screen():
    return Screen(Agent(chat=scripted(), out=io.StringIO(), mode="auto"))


def edit(old, new):
    return Call("edit", "a.py", "\n".join(f"old {n}" for n in range(old)) + "\n====\n" + "\n".join(f"new {n}" for n in range(new)), 0, 0)


def foot(s, w=70):
    return s.window(s.items[-1], w)[-1][1]


def test_an_edit_folded_counts_the_lines_put_in_and_the_lines_taken_out_apart():
    s = screen()
    s.show_call(edit(10, 12))  # six lines are shown: all of them taken out
    spans = foot(s)
    assert flat(spans).startswith("╰─ … + 12 lines | - 4 lines · ctrl+o ─") and len(flat(spans)) == 70
    assert spans[:6] == [("╰─", "frame"), (" … ", "dim"), ("+ 12 lines", "add"), (" | ", "dim"), ("- 4 lines", "del"), (" · ctrl+o ", "dim")]


def test_a_side_that_is_all_on_show_is_left_out():
    s = screen()
    s.show_call(edit(2, 9))  # both lines taken out are shown, and four of those put in
    assert flat(foot(s)).startswith("╰─ … + 5 lines · ctrl+o ─") and ("+ 5 lines", "add") in foot(s) and "del" not in [r for _, r in foot(s)]


def test_one_line_is_a_line():
    s = screen()
    s.show_call(edit(7, 1))
    assert flat(foot(s)).startswith("╰─ … + 1 line | - 1 line · ctrl+o ─")


def test_a_new_file_folded_counts_its_lines_in_green():
    s = screen()
    s.show_call(Call("write", "a.py", "\n".join(f"line {n}" for n in range(20)), 0, 0))
    assert flat(foot(s)).startswith("╰─ … + 14 lines · ctrl+o ─") and ("+ 14 lines", "add") in foot(s)


def test_what_the_tool_said_afterwards_is_not_counted_as_a_line_of_the_diff():
    s = screen()
    s.show_call(edit(10, 12))
    s.show_result("edited a.py")
    assert flat(foot(s)).startswith("╰─ … + 12 lines | - 4 lines · ctrl+o ─")
    s.items.clear()
    s.show_call(edit(3, 3))  # the whole diff is on show: only the tool's own line is folded away
    s.show_result("edited a.py")
    assert flat(foot(s)).startswith("╰─ … +1 lines · ctrl+o ─")


def test_a_commands_output_keeps_its_plain_count():
    s = screen()
    s.show_call(Call("shell", "", "seq 1 9", 0, 0))
    s.show_result("\n".join(str(i) for i in range(1, 10)))
    assert flat(foot(s, 44)) == "╰─ … +4 lines · ctrl+o ────────────────────╯" and foot(s, 44)[1] == (" … +4 lines · ctrl+o ", "dim")


class Terminal(io.StringIO):
    def isatty(self):
        return True


def test_the_plain_prompt_counts_a_folded_diff_the_same_way():
    a = Agent(chat=scripted(), out=Terminal(), mode="auto")
    a.show_call(edit(14, 12))  # ten lines are shown there: all of them taken out
    said = a.out.getvalue()
    assert "\033[2m  │ … \033[0m\033[32m+ 12 lines\033[0m\033[2m | \033[0m\033[31m- 4 lines\033[0m\n" in said
    a.out = Terminal()
    a.show_call(Call("write", "a.py", "\n".join(f"line {n}" for n in range(11)), 0, 0))
    assert a.out.getvalue().endswith("\033[2m  │ … \033[0m\033[32m+ 1 line\033[0m\n")
    a = Agent(chat=scripted(), out=io.StringIO(), mode="auto")  # piped: the same words, without the colours
    a.show_call(edit(14, 12))
    assert a.out.getvalue().endswith("  │ … + 12 lines | - 4 lines\n")
    a.show_call(Call("shell", "", "\n".join(f"echo {n}" for n in range(13)), 0, 0))
    assert a.out.getvalue().endswith("  │ … +3 lines\n")  # a long command is no diff


def test_the_edge_is_never_wider_than_its_window():
    s = screen()
    s.show_call(edit(200, 300))
    for w in (24, 30, 38, 44, 70):
        assert len(flat(foot(s, w))) == w and flat(foot(s, w)).endswith("╯")
    assert flat(box_foot(" esc close ", 20)) == "╰─ esc close ──────╯"  # a hint that is plain words, as before
