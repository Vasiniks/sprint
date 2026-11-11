"""what keeps a model from hurting the folder it works in, and sprint from falling over when one does: the working
folder vanishing, commands that delete, and a question nobody answers"""
import io
import os
import shutil

import pytest

import sprint
import sprint_tui as tui
from sprint import Agent
from test_sprint import scripted

pytestmark = pytest.mark.skipif(sprint.WINDOWS, reason="Windows will not delete a folder something is working in")


@pytest.fixture
def nest(tmp_path, monkeypatch):
    """tmp/a/b, with sprint working in b"""
    inner = tmp_path / "a" / "b"
    inner.mkdir(parents=True)
    monkeypatch.chdir(inner)
    assert os.path.samefile(sprint.folder(), inner)
    return tmp_path


def test_a_deleted_working_folder_is_stepped_out_of_to_the_nearest_one_left(nest):
    shutil.rmtree(nest / "a")
    with pytest.raises(OSError):
        os.getcwd()  # what brought the whole screen down
    assert os.path.samefile(sprint.folder(), nest) and os.path.samefile(os.getcwd(), nest)


def test_the_file_listing_for_the_model_survives_it_too(nest):
    (nest / "kept.txt").write_text("x")
    shutil.rmtree(nest / "a")
    assert "kept.txt" in sprint.here()


def test_a_command_that_deletes_the_folder_it_ran_in_says_so_and_leaves_sprint_standing(nest):
    out = sprint.shell("cd .. && rm -r b && echo gone")
    assert "gone" in out and "working folder" in out and "deleted" in out
    assert os.path.samefile(os.getcwd(), nest / "a")
    assert "a" in out.split("working folder")[1]  # and the model is told where it is now


def test_the_screen_draws_on_after_the_working_folder_is_deleted(nest, monkeypatch):
    from test_popups import Scr
    monkeypatch.setattr(tui.curses, "curs_set", lambda n: None)
    monkeypatch.setattr(tui.curses, "color_pair", lambda n: n << 8)
    s, scr = tui.Screen(Agent(chat=scripted(), out=io.StringIO())), Scr(30, 120)
    s.draw(scr)
    shutil.rmtree(nest / "a")
    s.draw(scr)  # the opening screen names the folder
    s.items = [{"t": "text", "text": "hello"}]
    s.sidebar = True
    s.draw(scr)  # and so does the side panel
    assert os.path.samefile(os.getcwd(), nest) and "sprint" not in scr.text(29)[:10]  # in the conversation, not back at the start
