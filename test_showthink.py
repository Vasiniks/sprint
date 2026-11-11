"""what the tool windows say: how long each call took, and how much of the model's reasoning is shown"""
import io

import sprint
import sprint_tui as tui
from sprint import Agent, Call
from sprint_tui import Screen, flat
from test_sprint import scripted


def screen(*replies, **kw):
    kw.setdefault("mode", "auto")
    return Screen(Agent(chat=scripted(*replies), out=io.StringIO(), **kw))


def clock(monkeypatch, at):
    now = [at]
    monkeypatch.setattr(tui.time, "time", lambda: now[0])
    return now


def top(s, w=70):
    return flat(s.window(s.items[-1], w)[0][1])


def test_a_finished_call_shows_how_long_it_took_beside_its_status(monkeypatch):
    now, s = clock(monkeypatch, 100.0), screen()
    s.show_call(Call("shell", "", "make test", 0, 0))
    now[0] = 101.24
    s.show_result("ok")
    assert "done 1.2s ─╮" in top(s) and len(top(s)) == 70


def test_a_running_call_counts_up_and_long_ones_drop_the_decimal(monkeypatch):
    now, s = clock(monkeypatch, 100.0), screen()
    s.show_call(Call("shell", "", "sleep 30", 0, 0))
    now[0] = 103.6
    assert "running 3s" in top(s)
    now[0] = 127.0
    s.show_result("[timed out after 27s]")
    assert "failed 27s" in top(s)


def test_time_spent_waiting_for_approval_is_not_counted(monkeypatch):
    now, s = clock(monkeypatch, 100.0), screen()
    s.show_call(Call("shell", "", "ls", 0, 0))
    s.gate.set = lambda: None
    s.gate.wait = lambda: now.__setitem__(0, 160.0)  # a minute goes by before the answer
    s.answer = True
    s.ask(s.items[-1]["call"])
    now[0] = 160.5
    s.show_result("a.txt")
    assert "done 0.5s" in top(s)


def test_the_redraw_cache_notices_a_running_call_getting_older(monkeypatch):
    now, s = clock(monkeypatch, 100.0), screen()
    s.show_call(Call("shell", "", "sleep 9", 0, 0))
    first = "\n".join(flat(spans) for _, spans, _ in s.lines(70))
    now[0] = 104.0
    assert "running 4s" in "\n".join(flat(spans) for _, spans, _ in s.lines(70)) and "running 4s" not in first


def test_reasoning_is_one_line_until_showthink_is_on():
    s = screen()
    s.show_thought("First I should read the file.\nThen change the word.")
    rows = s.window(s.items[-1], 70)
    assert len(rows) == 1 and "thought" in flat(rows[0][1]) and "10 words" in flat(rows[0][1]) and "First I" not in flat(rows[0][1])
    s.submit("/showthink")
    text = "\n".join(flat(spans) for _, spans, _ in s.window(s.items[-1], 70))
    assert s.showthink and "First I should read the file." in text and "Then change the word." in text
    assert sprint.load_config()["showthink"] is True and Screen(s.agent).showthink
    s.submit("/showthink")
    assert not s.showthink and len(s.window(s.items[-1], 70)) == 1


def test_one_thought_can_be_opened_by_itself_and_showthink_is_a_listed_command():
    s = screen()
    s.show_thought("Check the tests first.")
    s.items[-1]["open"] = True
    assert "Check the tests first." in "\n".join(flat(spans) for _, spans, _ in s.window(s.items[-1], 70))
    assert "/showthink" in dict(tui.COMMANDS) and "/showthink" in tui.HELP


# ---------- Windows details of pasting, found by reading the code against PDCurses ----------

def test_a_temp_folder_with_an_apostrophe_does_not_break_the_windows_clipboard_command(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(sprint, "WINDOWS", True)
    monkeypatch.setattr(sprint.tempfile, "gettempdir", lambda: str(tmp_path / "O'Brien"))
    monkeypatch.setattr(sprint.shutil, "which", lambda name: name)
    monkeypatch.setattr(sprint.subprocess, "run", lambda argv, **kw: seen.append(argv) or sprint.subprocess.CompletedProcess(argv, 1, b"", b""))
    assert sprint.clipboard_image() == ""
    assert "O''Brien" in seen[0][-1] and "O'Brien" not in seen[0][-1].replace("O''Brien", "")


def test_a_held_key_is_not_gathered_into_a_paste():
    """key repeat comes about every 30 ms; waiting less than that for the rest of a paste keeps the two apart"""
    class Keys:
        def __init__(self):
            self.keys, self.waits = ["a", "a"], []

        def timeout(self, ms):
            self.waits.append(ms)

        def get_wch(self):
            if not self.keys:
                raise tui.curses.error("no input")
            return self.keys.pop(0)

    keys = Keys()
    screen().read_keys(keys)
    assert keys.waits[0] == 0 and 0 < max(keys.waits) <= 12


def test_alt_v_as_windows_reports_it_attaches_the_clipboard_picture(tmp_path, monkeypatch):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG....")
    monkeypatch.setattr(sprint, "clipboard_image", lambda: str(shot))
    monkeypatch.setattr(tui, "ALT_V", 0x1b6)
    s = screen()
    s.key(0x1b6)
    assert s.buf == "[Image #1] "
