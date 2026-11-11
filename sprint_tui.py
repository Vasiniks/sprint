"""sprint_tui - the full-screen terminal for sprint. stdlib curses."""
import curses
import json
import locale
import os
import re
import shlex
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import sprint
from sprint import ABOUT, MODES, split_edit

SKIP = {"node_modules", "__pycache__", "venv", "dist", "build", "target"}  # folders @ completion does not look in
PEEK = 6   # body lines a collapsed tool window shows
ROOM = 44  # columns the conversation always keeps; a panel that would squeeze it further is hidden
SIDE = 32  # the sidebar's width until you drag it
COMMANDS = [("/clear", "clear the conversation and start fresh"), ("/new", "the same as /clear"),
            ("/model", "switch model"), ("/mode", "ask · edits · auto"),
            ("/think", "let the model reason first, on or off"), ("/effort", "low · medium · high"),
            ("/context", "what is filling the context window"), ("/permissions", "what runs without asking"),
            ("/config", "see or change saved settings"), ("/systemprompt", "edit what the model is told at the start"),
            ("/theme", "colours"), ("/plan", "split each message into steps first, on or off"),
            ("/race", "one message, two models, side by side"),
            ("/directory", "change the folder sprint works in"),
            ("/copy", "copy the last answer; /copy code, /copy all"), ("/export", "save the conversation as markdown"),
            ("/log", "what happened recently, and where the full log is"),
            ("/compact", "fold the older conversation into a short summary to free room"), ("/sidebar", "show or hide the sidebar, or set its width"),
            ("/history", "show or hide the message list, or set its width"),
            ("/tools", "expand or collapse all tool windows"), ("/mouse", "mouse on or off, for selecting text"),
            ("/help", "keys and commands"), ("/exit", "quit")]
HELP = """In a message
  @path attaches that file (tab completes) · a line starting with ! runs the rest as a command yourself
  paste or drag a file in and it becomes a chip like [Image #1] or [PDF #1] that travels with the message
  a large paste becomes a chip too, like [Pasted #1 +42 lines]; the model gets the full text
Keys
  enter send · tab switch mode (ask → edits → auto) · esc interrupt · ctrl+l clear · ctrl+c quit
  ctrl+o expand or collapse all tool windows · click a window to toggle just that one
  pgup / pgdn or the mouse wheel scroll · up / down recall earlier messages
Commands
  /clear  /model [name]  /mode [ask|edits|auto]  /permissions  /think  /effort [low|medium|high]
  /context  /config [key value]  /systemprompt  /theme  /sidebar [width]  /history [width]  /tools  /mouse
  /plan  /race [model message]  /directory [path]  /copy [code|all]  /export [file]  /log  /compact  /help  /exit
Copy and paste
  drag over text in the conversation to copy it · ctrl+y or /copy copies the last answer
  paste as usual (files become chips) · /mouse hands the mouse back to the terminal
Mouse
  drag the line beside either panel to resize it · click a message or an activity line to jump to it"""

# the wordmark: "sprint" in a 5-row pixel font, shaded from light to solid, one colour per shade, leaning forward
WORD = "sprint"
PIX = {"s": ["XXXX", "X   ", "XXXX", "   X", "XXXX"], "p": ["XXXX", "X  X", "XXXX", "X   ", "X   "],
       "r": ["XXXX", "X  X", "XXX ", "X X ", "X  X"], "i": ["X", "X", "X", "X", "X"],
       "n": ["X  X", "XX X", "X XX", "X  X", "X  X"], "t": ["XXXXX", "  X  ", "  X  ", "  X  ", "  X  "],
       "e": ["XXXX", "X   ", "XXX ", "X   ", "XXXX"]}
STILL = (1, 1, 2, 3, 3, 4)  # shade of each letter when nothing moves: ░ ░ ▒ ▓ ▓ █
SHADE = {"░": "yellow", "▒": "cyan", "▓": "magenta", "█": "bold"}  # each shade in its own colour, like the four bars of the logo
LOGO_W = 60        # columns: two per pixel, two between letters, four of lean
WAVE_SECONDS = 11  # how long the crest takes to cross the word and come round again


def wave(x, t):
    """shade (1-4) of column x at time t: a solid crest with a long fading tail, one soft step at a time"""
    d = (t * LOGO_W / WAVE_SECONDS - x) % LOGO_W
    return 4 if d < 6 else 3 if d < 14 else 2 if d < 25 else 1 if d < LOGO_W - 6 else 2 if d < LOGO_W - 3 else 3


def logo_rows(t=None):
    """the wordmark as 5 rows of spans; t in seconds runs the wave through it, None holds it still"""
    rows = []
    for r in range(5):
        text, x = "", 0
        for i, ch in enumerate(WORD):
            for px in PIX[ch][r] + " ":
                for _ in range(2):
                    text += " ░▒▓█"[0 if px != "X" else STILL[i] if t is None else wave(x, t)]
                    x += 1
        text = (" " * (4 - r) + text).ljust(LOGO_W)
        rows.append([(m.group(), SHADE.get(m.group()[0], "")) for m in re.finditer(r"(.)\1*", text)])
    return rows


# what each kind of text looks like: (colour 0-7, attribute). 0 = terminal default
ROLE = {"": (0, 0), "dim": (0, curses.A_DIM), "bold": (0, curses.A_BOLD), "frame": (0, curses.A_DIM),
        "title": (3, curses.A_BOLD), "code": (6, 0), "path": (5, 0), "url": (4, curses.A_UNDERLINE),
        "bullet": (6, 0), "kw": (5, 0), "string": (2, 0), "comment": (0, curses.A_DIM), "number": (3, 0),
        "add": (2, 0), "del": (1, 0), "err": (1, 0), "ok": (2, 0), "warn": (3, 0),
        "ask": (6, curses.A_BOLD), "edits": (3, curses.A_BOLD), "auto": (5, curses.A_BOLD),
        "red": (1, 0), "green": (2, 0), "yellow": (3, 0), "blue": (4, 0), "magenta": (5, 0), "cyan": (6, 0)}
# a theme only says which terminal colour each of the six hues becomes; the rest of the screen follows
THEMES = {"default": {}, "mono": {i: -1 for i in range(1, 8)},
          "amber": {1: 203, 2: 178, 3: 214, 4: 172, 5: 208, 6: 220}, "forest": {1: 167, 2: 114, 3: 185, 4: 109, 5: 150, 6: 79},
          "ocean": {1: 204, 2: 49, 3: 80, 4: 33, 5: 105, 6: 39}, "sunset": {1: 203, 2: 150, 3: 221, 4: 141, 5: 205, 6: 215}}
THEME_ABOUT = {"default": "your terminal's own colours", "mono": "no colour, only bold and dim", "amber": "warm ambers, like an old monitor",
               "forest": "soft greens", "ocean": "blues and teals", "sunset": "orange, pink and violet"}
WHEEL_DOWN = getattr(curses, "BUTTON5_PRESSED", 0x200000)
MOUSE = (curses.BUTTON1_PRESSED | curses.BUTTON1_RELEASED | curses.BUTTON4_PRESSED | WHEEL_DOWN
         | getattr(curses, "REPORT_MOUSE_POSITION", 0))
VT = not sprint.WINDOWS  # terminal escape codes for drag reporting and marked pastes; the Windows curses does without

CODE = re.compile(
    r"(?P<comment>#.*|//.*)|(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')|(?P<number>\b\d+(?:\.\d+)?\b)"
    r"|(?P<kw>\b(?:def|class|return|if|elif|else|for|while|import|from|as|try|except|finally|with|lambda|yield|async"
    r"|await|function|const|let|var|new|public|private|static|void|fn|struct|impl|package|func|type|interface|export"
    r"|default|switch|case|break|continue|in|not|and|or|is|None|True|False|null|true|false|this|self)\b)")
PROSE = re.compile(
    r"(?P<code>`[^`\n]+`)|(?P<bold>\*\*[^*\n]+\*\*)|(?P<url>https?://[^\s)>\]]+)"
    r"|(?P<path>(?<![\w/])(?:~/|\.{0,2}/)?(?:[\w.-]+/)+[\w.-]*\w"
    r"|(?<![\w/.-])[\w-]+\.(?:py|js|ts|tsx|jsx|json|md|txt|toml|ya?ml|sh|html|css|go|rs|c|h|cpp|java|rb|cfg|ini)\b)")


# ---------- text: highlighting and wrapping. A line is a list of (text, role) spans ----------

def marks(line, pattern, base=""):
    out, i = [], 0
    for m in pattern.finditer(line):
        if m.start() > i:
            out.append((line[i:m.start()], base))
        out.append((m.group(), m.lastgroup))
        i = m.end()
    return out + ([(line[i:], base)] if i < len(line) else [])


def code(line):
    return marks(line, CODE)


def prose(text):
    """assistant text -> highlighted lines: titles, bullets, `code`, **bold**, paths, URLs, fenced code"""
    out, fenced = [], False
    for line in text.split("\n"):
        bullet = re.match(r"\s*(?:[-*•]|\d+[.)])\s+", line)
        if line.strip().startswith("```"):
            fenced = not fenced
            out.append([(line, "dim")])
        elif fenced:
            out.append(code(line))
        elif line.lstrip().startswith("#") or (0 < len(line) <= 40 and line.rstrip().endswith(":") and not bullet):
            out.append([(line, "title")])
        elif bullet:
            out.append([(bullet.group(), "bullet")] + marks(line[bullet.end():], PROSE))
        else:
            out.append(marks(line, PROSE))
    return out


def flat(spans):
    return "".join(t for t, _ in spans)


def wrap(spans, w):
    """break a line of spans into lines at most w wide, at spaces where it can"""
    text, role = flat(spans), [r for t, r in spans for _ in t]
    cuts, i = [], 0
    while len(text) - i > w:
        sp = text.rfind(" ", i, i + w + 1)
        cuts.append((i, sp if sp > i else i + w))
        i = sp + 1 if sp > i else i + w
    out = []
    for a, b in cuts + [(i, len(text))]:
        row = []
        while a < b:
            e = a
            while e < b and role[e] == role[a]:
                e += 1
            row.append((text[a:e], role[a]))
            a = e
        out.append(row)
    return out


def clip(spans, w):
    """exactly w columns: cut with an ellipsis, or pad with spaces"""
    if len(flat(spans)) <= w:
        return spans + [(" " * (w - len(flat(spans))), "")]
    out, room = [], w - 1
    for t, r in spans:
        if room > 0:
            out.append((t[:room], r))
            room -= len(t[:room])
    return out + [("…", "dim")]


def short(model):
    return re.sub(r"^hf\.co/[^/]+/|-GGUF", "", model)


def size(n):
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def row(spans, value=None, keep=False):
    """one line of a popup. value None: only shown, cannot be chosen. keep: stays when the list is filtered"""
    return {"s": spans if isinstance(spans, list) else [(spans, "")], "v": value, "keep": keep}


def kilo(n):
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


# ---------- the screen: conversation model, keys, drawing ----------

class Screen:
    def __init__(self, agent):
        self.agent = agent
        self.items, self.note, self.cache = [], "", {}  # conversation, transient status, wrapped-line cache
        self.buf, self.cur, self.past, self.at, self.pick = "", 0, [], 0, 0  # input line, cursor, history, popup row
        self.scroll, self.page, self.hits = 0, 10, {}
        self.pending, self.answer, self.gate = None, None, threading.Event()  # a tool call waiting for approval
        self.worker, self.models, self.quit, self.colors = None, None, False, False
        self.mouse_on, self.drag, self.jump, self.geo = True, None, None, (0, 0, 0)  # geo: screen, list and sidebar widths
        self.popup, self.job, self.toast, self.details = None, None, ("", 0.0), {}  # open popup, its background work, brief note
        self.chips, self.esc, self.pasting = {}, None, None  # pasted files by chip label; a held escape; a paste in progress
        self.pastes = {}  # large pasted texts by their chip label
        self.select, self.screen, self.rich = None, {}, False  # a drag over text; what each screen row shows; 256 colours?
        self.tree = (0.0, "", [])  # when the folder was last listed for @ completion, where, and what was there
        self.load_ui()
        agent.emit, agent.status, agent.ask = self.emit, self.status, self.ask
        agent.show_call, agent.show_result, agent.show_thought = self.show_call, self.show_result, self.show_thought
        agent.show_plan = self.show_plan

    def load_ui(self):
        kept = sprint.load_config()
        self.sidebar, self.history = kept.get("sidebar", True), kept.get("history", True)
        self.side_w, self.left_w = kept.get("sidebar_w", SIDE), kept.get("history_w", 26)
        self.motion, self.plan = kept.get("motion", True), bool(kept.get("plan", False))
        self.theme = kept.get("theme") if kept.get("theme") in THEMES else "default"
        self.peek = kept["peek"] if isinstance(kept.get("peek"), int) and kept["peek"] > 0 else PEEK
        self.paint()

    def paint(self):
        """(re)define the colour pairs for the current theme"""
        try:
            if not curses.has_colors():
                return
            curses.use_default_colors()
            rich = self.rich = curses.COLORS >= 256
            for i in range(8):
                colour = THEMES[self.theme].get(i, i) if rich or self.theme == "mono" else i
                if i:
                    curses.init_pair(i, colour, -1)
                curses.init_pair(10 + i, colour if i else -1, 236 if rich else -1)
        except curses.error:
            pass  # no screen yet

    # --- what the agent calls (from its worker thread)

    def emit(self, s, style=""):
        if self.items and self.items[-1]["t"] == "text":
            self.items[-1]["text"] += s
        elif s.strip():
            self.items.append({"t": "text", "text": s})

    def status(self, s):
        self.note = s

    def say(self, text, role="dim"):
        self.items.append({"t": "note", "text": text, "role": role})

    def show_call(self, c):
        self.items.append({"t": "tool", "call": c, "result": None, "state": "running", "open": False,
                           "old": c.tool == "write" and os.path.isfile(os.path.expanduser(c.arg))})

    def show_thought(self, text):
        self.items.append({"t": "tool", "call": sprint.Call("think", "", "", 0, 0), "result": text, "state": "done",
                           "open": False, "old": False})

    def show_result(self, r):
        it = self.items[-1]
        bad = r.startswith("error") or re.search(r"\[(exit -?\d+|timed out[^\]]*)\]$", r)
        it["result"], it["state"] = r, "denied" if r.startswith("denied") else "failed" if bad else "done"

    def ask(self, call):
        self.items[-1]["state"] = "waiting"
        self.gate.clear()
        self.pending = call
        self.gate.wait()
        self.pending = None
        self.items[-1]["state"] = "running"
        return self.answer

    def show_plan(self, steps, at):
        last = next((i for i in reversed(self.items) if i["t"] in ("plan", "user")), None)
        if last and last["t"] == "plan":
            last["at"] = at
        else:
            self.items.append({"t": "plan", "steps": list(steps), "at": at})

    def speed_text(self):
        a = self.agent
        return f"{a.rate:.0f} tok/s · {a.took:.1f}s last reply" if a.rate else ""

    def work(self, task, chips=None):
        try:
            stop = (self.agent.run_plan if self.plan else self.agent.run)(task, chips)
            if stop.startswith("stopped:"):
                self.say(stop, "warn")
        except KeyboardInterrupt:
            self.say("interrupted", "warn")
        except Exception as e:  # shown to the user rather than killing the screen
            import traceback
            sprint.log("crash", error=traceback.format_exc()[-1500:])
            self.say(str(e), "err")
        self.note = ""

    def active(self):
        """is anything on the screen changing by itself? When nothing is, the loop sleeps instead of redrawing."""
        wave = not self.items and self.motion
        return bool(self.busy() or self.pending or self.drag or self.select or self.note_text() or wave
                    or (self.job and self.job.is_alive()) or self.esc is not None or self.pasting is not None)

    def busy(self):
        return bool(self.worker and self.worker.is_alive())

    def interrupt(self):
        self.agent.stop()
        if self.pending:
            self.answer = False
            self.gate.set()

    # --- input

    def submit(self, line):
        cmd, _, arg = line.partition(" ")
        a, arg = self.agent, arg.strip()
        if cmd in ("/exit", "/quit", "/bye"):
            self.quit = True
        elif cmd in ("/new", "/clear"):
            if not self.busy():
                a.reset()
                self.items, self.cache, self.scroll = [], {}, 0
        elif cmd in ("/sidebar", "/history"):
            right = cmd == "/sidebar"
            if arg.isdigit():  # a width: show the panel at that size
                size = max(20 if right else 16, min(80, int(arg)))
                self.sidebar, self.side_w, self.history, self.left_w = (True, size, self.history, self.left_w) if right else \
                    (self.sidebar, self.side_w, True, size)
            elif right:
                self.sidebar = not self.sidebar
            else:
                self.history = not self.history
            sprint.save_config(sidebar=self.sidebar, sidebar_w=self.side_w, history=self.history, history_w=self.left_w)
        elif cmd == "/tools":
            self.toggle_all()
        elif cmd in ("/directory", "/dir", "/cd") and self.busy():
            self.tell("wait for the current turn to finish before changing folder")
        elif cmd == "/compact" and self.busy():
            self.tell("wait for the current turn to finish; sprint compacts by itself when room runs out")
        elif cmd == "/race" and arg:
            rival, _, task = arg.partition(" ")
            self.race(rival, task.strip()) if task.strip() else self.race_ask(rival)
        elif cmd == "/mouse":
            self.mouse_on = not self.mouse_on
            self.set_mouse()
            self.tell("mouse on: click a window to toggle it, wheel to scroll" if self.mouse_on else
                      "mouse off: select text as usual; ctrl+o and pgup/pgdn still work")
        elif cmd in self.popups() and not arg:
            self.popups()[cmd]()
        elif (rows := sprint.command(a, line)) is not None:  # a command given its value: apply it and say so
            self.models = None if cmd == "/model" else self.models
            self.load_ui()
            self.tell(flat(rows[0]) if rows else "")
        elif cmd.startswith("/"):
            self.help_popup()
        elif not self.busy():
            mine = line[1:].strip() if line.startswith("!") else ""  # !command: run it yourself, no model involved
            if mine or not line.startswith("!"):
                line = full = line if mine else self.chipify(line)
                chips, self.chips = {label: path for label, path in self.chips.items() if label in line}, {}
                for label, text in self.pastes.items():  # the screen keeps the chip; the model gets what was pasted
                    full = full.replace(label, sprint.clip(text, self.agent.cap * 2))
                self.pastes = {}
                self.items.append({"t": "user", "text": line})
                self.worker = threading.Thread(target=self.bang if mine else self.work, args=(mine,) if mine else (full, chips), daemon=True)
                self.worker.start()

    def bang(self, cmd):
        try:
            self.agent.bang(cmd)
        except Exception as e:  # shown to the user rather than killing the screen
            self.say(str(e), "err")

    def files(self, part):
        """files under this folder whose path contains part, best matches first (for @ completion)"""
        if time.time() - self.tree[0] > 3 or self.tree[1] != os.getcwd():
            found = []
            for root, dirs, names in os.walk("."):
                dirs[:] = [] if root.count(os.sep) >= 3 else sorted(d for d in dirs if not d.startswith(".") and d not in SKIP)
                found += [os.path.join(root, n)[2:] for n in sorted(names) if not n.startswith(".")]
                if len(found) > 2000:
                    break
            self.tree = (time.time(), os.getcwd(), found)
        low = part.lower()
        hits = [p for p in self.tree[2] if low in p.lower() and p != part]
        return sorted(hits, key=lambda p: (not os.path.basename(p).lower().startswith(low), p))[:20]

    def mention(self):
        """the @word being typed at the cursor, without the @, or None"""
        m = re.search(r"(?:^|\s)@(\S*)$", self.buf[:self.cur])
        return m.group(1) if m else None

    def installed(self):
        if self.models is None:
            try:
                with urllib.request.urlopen(self.agent.url.rstrip("/") + "/api/tags", timeout=2) as r:
                    found = json.load(r)["models"]
                self.models = [m["name"] for m in found]
                self.details = {m["name"]: ((m.get("details") or {}).get("parameter_size", ""),
                                            (m.get("details") or {}).get("quantization_level", ""), m.get("size", 0)) for m in found}
            except (OSError, ValueError, KeyError):
                self.models = []
        return self.models

    def options(self):
        """completions for what is typed: commands after "/", then their arguments"""
        if self.mention() is not None:
            return [("@" + p, "") for p in self.files(self.mention())]
        cmd, space, arg = self.buf.partition(" ")
        if not cmd.startswith("/"):
            return []
        if not space:
            return [(c, d) for c, d in COMMANDS if c.startswith(cmd) and c != cmd]
        pool = {"/mode": [(m, ABOUT[m]) for m in MODES], "/permissions": [(m, ABOUT[m]) for m in MODES],
                "/effort": [(e, f"replies up to window/{n} tokens, {k} steps") for e, (n, k) in sprint.EFFORT.items()],
                "/config": [(k, "") for k in sprint.SETTINGS]}.get(cmd)
        pool = [(m, "") for m in self.installed()] if cmd == "/model" else pool or []
        return [(o, d) for o, d in pool if o.startswith(arg) and o != arg]

    def complete(self, opts):
        choice = opts[min(self.pick, len(opts) - 1)][0]
        if self.mention() is not None:  # swap the @word at the cursor for the chosen file
            start = self.cur - len(self.mention()) - 1
            self.buf, self.cur, self.pick = self.buf[:start] + choice + " " + self.buf[self.cur:], start + len(choice) + 1, 0
            return
        cmd, space, _ = self.buf.partition(" ")
        self.buf = f"{cmd} {choice}" if space else choice + (" " if choice in ("/model", "/mode") else "")
        self.buf += " " if cmd == "/config" and space else ""
        self.cur, self.pick = len(self.buf), 0

    def key(self, k):
        if self.pending:  # only the approval keys count while a call waits
            if k in ("y", "Y", "\n", "\r", "a", "A"):
                self.answer = "always" if k in ("a", "A") else True
                self.gate.set()
            elif k in ("n", "N", "\x1b", "\x03"):
                self.interrupt() if k in ("\x1b", "\x03") else (setattr(self, "answer", False), self.gate.set())
            return
        if self.popup:
            return self.popup_key(k)
        opts, n = self.options(), len(self.buf)
        if k in ("\n", "\r", curses.KEY_ENTER):
            if opts:
                self.complete(opts)
            line = self.buf.strip()  # a completion that wants more typing (it ends in a space) is not sent yet
            if line and not (opts and self.buf.endswith(" ")) and (line.startswith("/") or not self.busy()):
                self.past.append(line)
                self.buf, self.cur, self.at, self.scroll = "", 0, len(self.past), 0
                self.submit(line)
        elif k == "\t":
            self.complete(opts) if opts else self.agent.cycle()
        elif k == curses.KEY_BTAB:
            self.agent.cycle()
        elif k in (curses.KEY_UP, curses.KEY_DOWN):
            step = -1 if k == curses.KEY_UP else 1
            if opts:
                self.pick = (min(self.pick, len(opts) - 1) + step) % len(opts)
            elif self.past:
                self.at = max(0, min(len(self.past), self.at + step))
                self.buf = self.past[self.at] if self.at < len(self.past) else ""
                self.cur = len(self.buf)
        elif k in (curses.KEY_BACKSPACE, "\x7f", "\b") and re.search(r"\[(?:(?:Image|PDF|File) #\d+|Pasted #\d+ [^\]]+)\]$", self.buf[:self.cur]):
            chip = re.search(r"\[(?:(?:Image|PDF|File) #\d+|Pasted #\d+ [^\]]+)\]$", self.buf[:self.cur])
            self.buf, self.cur = self.buf[:chip.start()] + self.buf[self.cur:], chip.start()
        elif k in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            if self.cur:
                self.buf, self.cur = self.buf[:self.cur - 1] + self.buf[self.cur:], self.cur - 1
        elif k == curses.KEY_DC:
            self.buf = self.buf[:self.cur] + self.buf[self.cur + 1:]
        elif k in (curses.KEY_LEFT, curses.KEY_RIGHT):
            self.cur = max(0, min(n, self.cur + (1 if k == curses.KEY_RIGHT else -1)))
        elif k in (curses.KEY_HOME, "\x01"):
            self.cur = 0
        elif k in (curses.KEY_END, "\x05"):
            self.cur = n
        elif k == "\x15":  # ctrl+u
            self.buf, self.cur = "", 0
        elif k == "\x17":  # ctrl+w
            cut = self.buf[:self.cur].rstrip().rfind(" ") + 1
            self.buf, self.cur = self.buf[:cut] + self.buf[self.cur:], cut
        elif k == "\x0f":  # ctrl+o
            self.toggle_all()
        elif k == "\x0c":  # ctrl+l
            self.submit("/clear")
        elif k == "\x19":  # ctrl+y: copy the last answer
            self.tell(flat(sprint.command(self.agent, "/copy")[0]))
        elif k in (curses.KEY_PPAGE, curses.KEY_NPAGE):
            self.scroll = max(0, self.scroll + (self.page if k == curses.KEY_PPAGE else -self.page))
        elif k == curses.KEY_MOUSE:
            self.mouse()
        elif k == "\x1b":
            self.interrupt() if self.busy() else None
        elif k == "\x03":  # ctrl+c: clear the line, else stop the turn, else leave
            if self.buf:
                self.buf, self.cur = "", 0
            elif self.busy():
                self.interrupt()
            else:
                self.quit = True
        elif k == "\x04" and not self.buf:
            self.quit = True
        elif isinstance(k, str) and k.isprintable():
            self.buf, self.cur, self.pick = self.buf[:self.cur] + k + self.buf[self.cur:], self.cur + 1, 0

    def insert(self, text):
        self.buf, self.cur, self.pick = self.buf[:self.cur] + text + self.buf[self.cur:], self.cur + len(text), 0

    def dropped(self, text):
        """the files a paste names (a dragged file arrives as its path: quoted, backslash-escaped, or file://), else []"""
        try:
            parts = shlex.split(text.strip())
        except ValueError:
            return []
        parts = [urllib.parse.unquote(p[7:]) if p.startswith("file://") else os.path.expanduser(p) for p in parts]
        return parts if parts and all(os.path.isfile(p) for p in parts) else []

    def chip(self, path):
        kind = sprint.kind_of(path)
        label = f"[{kind} #{1 + sum(1 for known in self.chips if known.startswith('[' + kind + ' #'))}]"
        self.chips[label] = path
        return label

    def chipify(self, line):
        """a picture's or PDF's full path in the message becomes a chip as it is sent: what a dropped file leaves
        behind on terminals that cannot mark a paste (Windows), and handy everywhere else"""
        def swap(m):
            path = os.path.expanduser(m.group(1) or m.group(2) or m.group(3))
            real = os.path.isabs(path) and os.path.isfile(path) and sprint.kind_of(path) != "File"
            return self.chip(path) if real else m.group()
        return re.sub(r'"([^"]+)"|\'([^\']+)\'|(\S+)', swap, line)

    def paste(self, text):
        """pasted text: files become chips like [Image #1]; anything else goes in as typed, on one line"""
        if self.popup:
            for ch in text.replace("\n", " "):
                self.popup_key(ch)
            return
        files = self.dropped(text)
        if files:
            self.insert("".join(self.chip(p) + " " for p in files))
        elif text.count("\n") >= 3 or len(text) > 600:  # too much to show on the prompt line
            lines, n = text.count("\n") + 1, len(self.pastes) + 1
            label = f"[Pasted #{n} +{lines} lines]" if lines > 1 else f"[Pasted #{n} {len(text):,} chars]"
            self.pastes[label] = text
            self.insert(label + " ")
        else:
            self.insert(" ".join(text.split("\n")))

    def take(self, k):
        """every key comes through here: a terminal paste is collected whole, and a lone escape is held one beat
        until it is clear it does not start one"""
        if self.pasting is not None:
            if isinstance(k, str):
                self.pasting += k
                if self.pasting.endswith("\x1b[201~"):
                    text, self.pasting = self.pasting[:-6], None
                    self.paste(text)
            return
        if self.esc is not None:
            if isinstance(k, str) and "[200~".startswith(self.esc + k):
                self.esc += k
                if self.esc == "[200~":
                    self.esc, self.pasting = None, ""
                return
            self.idle()
        if k == "\x1b":
            self.esc = ""
        else:
            self.key(k)

    def idle(self):
        """no more keys for now: a held escape was just escape"""
        if self.esc is not None:
            held, self.esc = self.esc, None
            self.key("\x1b")
            for ch in held:
                self.key(ch)

    def toggle_all(self):
        tools = [i for i in self.items if i["t"] == "tool"]
        want = not all(i["open"] for i in tools)
        for i in tools:
            i["open"] = want

    def set_mouse(self):
        try:
            curses.mouseinterval(0)
            curses.mousemask(MOUSE if self.mouse_on else 0)
            if VT:
                sys.stdout.write("\033[?1002h" if self.mouse_on else "\033[?1002l")  # report drags, not just clicks
                sys.stdout.flush()
        except curses.error:
            pass  # no screen yet, or a terminal without a mouse

    def mouse(self):
        try:
            _, x, y, _, state = curses.getmouse()
        except curses.error:
            return
        self.pointer(x, y, state)

    def pointer(self, x, y, state):
        """a mouse event: drag a divider, drag over text to copy it, scroll, or click"""
        w, lw, side = self.geo
        if self.drag:
            if self.drag == "right":
                self.side_w = max(20, min(w - lw - ROOM, w - 1 - x))
            else:
                self.left_w = max(16, min(w - side - ROOM, x))
            if state & (curses.BUTTON1_RELEASED | curses.BUTTON1_CLICKED):
                self.drag = None
                sprint.save_config(sidebar_w=self.side_w, history_w=self.left_w)
        elif self.select is not None:
            self.select["b"] = (y, x)
            if state & (curses.BUTTON1_RELEASED | curses.BUTTON1_CLICKED):
                picked, self.select = self.select, None
                self.click(x, y) if picked["a"] == picked["b"] else self.copy_range(picked["a"], picked["b"])
        elif state & curses.BUTTON4_PRESSED:
            self.scroll += 3
        elif state & WHEEL_DOWN:
            self.scroll = max(0, self.scroll - 3)
        elif state & curses.BUTTON1_PRESSED:
            if side and abs(x - (w - side - 1)) <= 1:
                self.drag = "right"
            elif lw and abs(x - lw) <= 1:
                self.drag = "left"
            else:
                self.select = {"a": (y, x), "b": (y, x)}

    def click(self, x, y):
        w, lw, side = self.geo
        where = "left" if lw and x < lw else "side" if side and x > w - side - 1 else "main"
        item = self.hits.get((where, y))
        if item and where == "main" and item["t"] == "tool":
            item["open"] = not item["open"]
        elif item and where != "main":
            self.jump = item

    def copy_range(self, a, b):
        """copy the conversation text between two screen positions, without the tool windows' side borders"""
        (ya, xa), (yb, xb) = sorted([a, b])
        lines = []
        for y in range(ya, yb + 1):
            x0, text = self.screen.get(y, (0, ""))
            piece = text[max(0, xa - x0) if y == ya else 0:(xb - x0 + 1) if y == yb else None]
            lines.append(re.sub(r" *│\s*$", "", re.sub(r"^\s*│ ?", "", piece)).rstrip() if y in self.screen else "")
        indent = min((len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()), default=0)  # keep code's own indentation
        text = "\n".join(ln[indent:] for ln in lines).strip("\n")
        if text.strip():
            self.tell(f"copied {len(text):,} characters" if sprint.clipboard(text) else "cannot copy: no clipboard tool found")

    # --- the two side panels

    def outline(self):
        """your messages, numbered, with what each one took: [(item, title, meta), ...]"""
        out = []
        for i, it in enumerate(self.items):
            if it["t"] != "user":
                continue
            steps = []
            for later in self.items[i + 1:]:
                if later["t"] == "user":
                    break
                if later["t"] == "tool" and later["call"].tool != "think":
                    steps.append(later)
            files = {t["call"].arg for t in steps if t["call"].tool in ("write", "edit") and t["state"] == "done"}
            meta = f"{len(steps)} step{'s' * (len(steps) != 1)}" if steps else "no tools"
            meta += f" · {len(files)} file{'s' * (len(files) != 1)}" if files else ""
            if self.busy() and not any(x["t"] == "user" for x in self.items[i + 1:]):
                meta = "working…"
            out.append((it, [(f"{len(out) + 1} ", "dim"), (it["text"].split("\n")[0], "")], [("  " + meta, "dim")]))
        return out

    def activity(self):
        """what the agent did, one line per tool call: [(item, spans), ...]"""
        out = []
        for it in self.items:
            if it["t"] != "tool" or it["call"].tool == "think":
                continue
            c, extra = it["call"], ""
            mark = {"done": ("✓", "green"), "failed": ("✗", "red"), "denied": ("✗", "red"),
                    "waiting": ("?", self.agent.mode)}.get(it["state"], ("…", "warn"))
            if c.tool == "edit":
                old, new = split_edit(c.body) or ("", c.body)
                extra = f" +{len(new.splitlines())} -{len(old.splitlines())}"
            elif c.tool == "write":
                extra = f" +{len(c.body.splitlines())}"
            what = c.body.split("\n")[0] if c.tool == "shell" else c.arg
            out.append((it, [(mark[0] + " ", mark[1]), (c.tool + " ", "bold"), (what, "" if c.tool == "shell" else "path"), (extra, "dim")]))
        return out

    # --- popups: one window for everything a command brings up

    def note_text(self):
        return self.toast[0] if time.time() < self.toast[1] else ""

    def tell(self, text):
        self.toast = (text, time.time() + 4)

    def pop(self, title, rows, pick=None, field=None, back=None, sel=0, move=None, edit=None):
        """open the popup. pick(value) runs on enter and move(value) as the choice changes; field is a line of typed
        text that filters the rows or is submitted; edit turns the popup into a small text editor"""
        self.popup = {"title": title, "rows": rows, "pick": pick, "field": field, "back": back, "sel": sel, "move": move,
                      "edit": edit}
        return self.popup

    def shown(self):
        """the popup's rows after filtering, and those of them that can be chosen"""
        p = self.popup
        q = p["field"]["text"].lower() if p["field"] and p["field"].get("filter") else ""
        rows = [r for r in p["rows"] if not q or r["keep"] or (r["v"] is not None and q in flat(r["s"]).lower())]
        return rows, [r for r in rows if r["v"] is not None]

    def popup_lines(self, w, tall=20):
        """the popup as a window exactly w wide -> (lines, index of the chosen line, index of the typing line)"""
        p = self.popup
        rows, can = self.shown()
        p["sel"] = max(0, min(p["sel"], len(can) - 1))
        chosen = can[p["sel"]] if can else None
        top = [("╭─ ", "frame"), (p["title"], "bold"), (" ", "")]
        lines, field_at, sel_at = [top + [("─" * (w - len(flat(top)) - 1) + "╮", "frame")]], None, None
        if p["edit"]:  # a text editor: show the lines around the cursor
            e, room = p["edit"], max(3, tall - 2)
            e["top"] = max(e["row"] - room + 1, min(e["top"], e["row"]))
            shift = max(0, e["col"] - (w - 6))
            for i, text in enumerate(e["lines"][e["top"]:e["top"] + room], e["top"]):
                lines.append([("│ ", "frame")] + clip([(text[shift:] if i == e["row"] else text, "")], w - 4) + [(" │", "frame")])
            p["cursor"] = (1 + e["row"] - e["top"], e["col"] - shift)
            hint = " ctrl+s save · ctrl+r default · esc cancel "
            return lines + [[("╰─", "frame"), (hint, "dim"), ("─" * (w - 3 - len(hint)) + "╯", "frame")]], None, None
        if p["field"]:
            field_at = len(lines)
            lines.append([("│ ", "frame")] + clip([(p["field"]["label"] + " ", "dim"), (p["field"]["text"], "")], w - 4) + [(" │", "frame")])
        room = max(1, tall - len(lines) - 1)
        start = max(0, min((rows.index(chosen) if chosen else 0) - room // 2, len(rows) - room))
        for r in rows[start:start + room]:
            sel_at = len(lines) if r is chosen else sel_at
            lines.append([("│ ", "frame")] + clip(r["s"], w - 4) + [(" │", "frame")])
        field = p["field"] or {}
        hint = " " + " · ".join(x for x in ("↑↓ move" if len(can) > 1 else "", "type to filter" if field.get("filter") else "",
                                           "enter choose" if can and p["pick"] else "enter confirm" if field.get("submit") else "",
                                           "esc back" if p["back"] else "esc close") if x) + " "
        return lines + [[("╰─", "frame"), (hint, "dim"), ("─" * (w - 3 - len(hint)) + "╯", "frame")]], sel_at, field_at

    def popup_key(self, k):
        p = self.popup
        if p["edit"]:
            return self.edit_key(p["edit"], k)
        _, can = self.shown()
        f = p["field"]
        if k in ("\x1b", "\x03"):
            self.popup = None
            if p["back"]:
                p["back"]()
        elif k in (curses.KEY_UP, curses.KEY_DOWN) and can:
            p["sel"] = (p["sel"] + (1 if k == curses.KEY_DOWN else -1)) % len(can)
            p["move"](can[p["sel"]]["v"]) if p["move"] else None
        elif k in (curses.KEY_PPAGE, curses.KEY_NPAGE) and can:
            p["sel"] = max(0, min(len(can) - 1, p["sel"] + (8 if k == curses.KEY_NPAGE else -8)))
            p["move"](can[p["sel"]]["v"]) if p["move"] else None
        elif k in ("\n", "\r", curses.KEY_ENTER):
            if can and p["pick"]:
                p["pick"](can[p["sel"]]["v"])
            elif f and f.get("submit") and f["text"].strip():
                f["submit"](f["text"].strip())
        elif f and k in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            f["text"], p["sel"] = f["text"][:-1], 0
        elif f and k == "\x15":
            f["text"] = ""
        elif f and isinstance(k, str) and k.isprintable():
            f["text"], p["sel"] = f["text"] + k, 0

    def edit_key(self, e, k):
        """the text editor's keys: type, enter, backspace, arrows, ctrl+u clears the line, ctrl+s saves"""
        lines, r, c = e["lines"], e["row"], e["col"]
        if k in ("\x1b", "\x03"):
            self.popup = None
        elif k == "\x13":
            self.popup = None
            e["save"]("\n".join(lines))
        elif k == "\x12":
            e["lines"], e["row"], e["col"] = e["default"].split("\n"), 0, 0
        elif k in ("\n", "\r", curses.KEY_ENTER):
            lines[r:r + 1] = [lines[r][:c], lines[r][c:]]
            e["row"], e["col"] = r + 1, 0
        elif k in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            if c:
                lines[r], e["col"] = lines[r][:c - 1] + lines[r][c:], c - 1
            elif r:
                e["row"], e["col"] = r - 1, len(lines[r - 1])
                lines[r - 1:r + 1] = [lines[r - 1] + lines[r]]
        elif k == curses.KEY_DC:
            if c < len(lines[r]):
                lines[r] = lines[r][:c] + lines[r][c + 1:]
            elif r + 1 < len(lines):
                lines[r:r + 2] = [lines[r] + lines[r + 1]]
        elif k == "\x15":
            lines[r], e["col"] = "", 0
        elif k in (curses.KEY_LEFT, curses.KEY_RIGHT):
            e["col"] = max(0, min(len(lines[r]), c + (1 if k == curses.KEY_RIGHT else -1)))
        elif k in (curses.KEY_UP, curses.KEY_DOWN):
            e["row"] = max(0, min(len(lines) - 1, r + (1 if k == curses.KEY_DOWN else -1)))
            e["col"] = min(c, len(lines[e["row"]]))
        elif k in (curses.KEY_HOME, "\x01"):
            e["col"] = 0
        elif k in (curses.KEY_END, "\x05"):
            e["col"] = len(lines[r])
        elif isinstance(k, str) and k.isprintable():
            lines[r], e["col"] = lines[r][:c] + k + lines[r][c:], c + 1

    def prompt_popup(self):
        a = self.agent
        default = sprint.SYSTEM_TOOLS if a.tools else sprint.SYSTEM.split("\n", 1)[0]

        def save(text):
            a.custom = "" if text.strip() == default.strip() else text.strip()
            sprint.save_prompt(a.custom)
            self.tell("system prompt saved: it applies from your next message" if a.custom else "system prompt is the default")

        lines = (a.custom or default).split("\n")
        self.pop("System prompt", [], edit={"lines": lines, "row": 0, "col": len(lines[0]), "top": 0, "save": save, "default": default})

    def theme_popup(self):
        was = self.theme

        def show(name):
            self.theme = name
            self.paint()

        def keep(name):
            show(name)
            sprint.save_config(theme=name)
            self.popup = None
            self.tell("theme: " + name)

        swatch = [("██", c) for c in ("red", "green", "yellow", "blue", "magenta", "cyan")]
        rows = [row([("● " if n == was else "  ", self.agent.mode), (f"{n:<9}", "bold"), (f"{THEME_ABOUT[n]:<36}", "dim")] + swatch, n)
                for n in THEMES]
        self.pop("Theme", rows, pick=keep, move=show, back=lambda: show(was), sel=list(THEMES).index(was))

    def popups(self):
        return {"/systemprompt": self.prompt_popup, "/system": self.prompt_popup, "/theme": self.theme_popup,
                "/directory": self.dir_popup, "/dir": self.dir_popup, "/cd": self.dir_popup,
                "/log": lambda: self.pop("Log", [row(list(r)) for r in sprint.command(self.agent, "/log")]),
                "/race": self.race_popup, "/model": self.model_popup, "/mode": self.mode_popup,
                "/permissions": lambda: self.mode_popup("Permissions"),
                "/effort": self.effort_popup, "/context": self.context_popup, "/config": self.config_popup, "/help": self.help_popup}

    def choose(self, line):
        """apply a setting through the shared command, close the popup and say what changed"""
        rows = sprint.command(self.agent, line) or []
        self.popup = None
        self.load_ui()
        self.tell(flat(rows[0]) if rows else "")

    def background(self, work):
        self.job = threading.Thread(target=work, daemon=True)
        self.job.start()

    def model_popup(self):
        a, names = self.agent, self.installed()
        rows = []
        for n in names:
            params, quant, weight = self.details.get(n, ("", "", 0))
            name, quant = short(n) if len(short(n)) <= 34 else short(n)[:33] + "…", "" if quant == "unknown" else quant
            rows.append(row([("● " if n == a.model else "  ", a.mode), (f"{name:<34}", "bold" if n == a.model else ""),
                             (f"{params:>8}  {quant:<8} {size(weight) if weight else '':>8}", "dim")], ("use", n)))
        rows += [] if names else [row([("no models installed yet", "dim")])]
        rows += [row([("─" * 40, "frame")]), row("  Pull from Hugging Face…", ("hf", ""), keep=True),
                 row("  Pull by name from the Ollama library…", ("name", ""), keep=True)]
        self.pop("Model", rows, pick=self.model_pick, field={"label": "›", "text": "", "filter": True},
                 sel=names.index(a.model) if a.model in names else 0)

    def model_pick(self, choice):
        kind, name = choice
        if kind == "use":
            self.use_model(name)
        elif kind == "name":
            self.pop("Pull a model", [row([("a name from ollama.com/library, for example qwen3.5:0.8b", "dim")])],
                     field={"label": "name ›", "text": "", "submit": self.pull}, back=self.model_popup)
        else:
            self.pop("Hugging Face", [row([("search for a GGUF model, for example qwen3.5 0.8b", "dim")])],
                     field={"label": "search ›", "text": "", "submit": self.hf_search}, back=self.model_popup)

    def use_model(self, name):
        sprint.command(self.agent, "/model " + name)
        self.popup, self.models = None, None
        self.tell("model: " + short(name))

    def pull(self, name):
        """download a model through Ollama, showing progress in the popup, then switch to it"""
        mine = self.pop("Pulling " + short(name), [row([("starting…", "dim")])])

        def work():
            try:
                req = urllib.request.Request(self.agent.url.rstrip("/") + "/api/pull",
                                             json.dumps({"model": name, "stream": True}).encode(), {"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=600) as r:
                    for line in r:
                        if self.popup is not mine:
                            return  # closed with esc: stop following it
                        d = json.loads(line) if line.strip() else {}
                        if d.get("error"):
                            raise RuntimeError(d["error"])
                        done, total = d.get("completed", 0), d.get("total", 0)
                        bar = [("━" * (done * 40 // total), self.agent.mode), ("─" * (40 - done * 40 // total), "frame"),
                               (f"  {size(done)} of {size(total)}", "dim")] if total else []
                        mine["rows"] = [row([(d.get("status", ""), "")])] + ([row(bar)] if bar else [])
                self.use_model(name)
                self.tell("pulled and switched to " + short(name))
            except (OSError, ValueError, RuntimeError) as e:
                if self.popup is mine:
                    self.pop("Pull failed", [row([(str(e), "err")]), row([("check the name, and that Ollama is running", "dim")])],
                             back=self.model_popup)

        self.background(work)

    def hf_search(self, query):
        mine = self.pop("Hugging Face", [row([("searching…", "dim")])], back=self.model_popup)

        def work():
            try:
                url = "https://huggingface.co/api/models?" + urllib.parse.urlencode(
                    {"search": query, "filter": "gguf", "sort": "downloads", "direction": "-1", "limit": "20"})
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "sprint"}), timeout=15) as r:
                    found = json.load(r)
                rows = [row([(f"{m['id']:<48}", ""), (f"{kilo(m.get('downloads', 0)):>7} downloads", "dim")], m["id"]) for m in found]
                rows = rows or [row([("nothing found for " + query, "dim")])]
            except (OSError, ValueError, KeyError, TypeError) as e:
                rows = [row([(f"search failed: {e}", "err")])]
            if self.popup is mine:
                self.pop("Hugging Face: " + query, rows, pick=self.hf_files, back=self.model_popup)

        self.background(work)

    def hf_files(self, repo):
        mine = self.pop(repo, [row([("looking for quantizations…", "dim")])], back=self.model_popup)

        def work():
            found = {}
            try:
                url = f"https://huggingface.co/api/models/{repo}/tree/main"
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "sprint"}), timeout=15) as r:
                    for f in json.load(r):
                        m = re.search(r"[-._]((?:I?Q\d\w*|BF16|F16|F32))\.gguf$", f.get("path", ""), re.I)
                        if m and "mmproj" not in f["path"].lower():
                            found[m.group(1).upper()] = f.get("size", 0)
                tags = sorted(found, key=found.get)
                rows = [row([(f"{t:<12}", "bold" if t == "Q4_K_M" else ""), (f"{size(found[t]):>8}", "dim"),
                             ("   a good default" if t == "Q4_K_M" else "", "dim")], t) for t in tags]
                rows = rows or [row([("no single-file GGUF builds in this repository", "dim")])]
            except (OSError, ValueError, KeyError, TypeError) as e:
                tags, rows = [], [row([(f"could not read the repository: {e}", "err")])]
            if self.popup is mine:
                self.pop(repo, rows, pick=lambda t: self.pull(f"hf.co/{repo}:{t}"), back=self.model_popup,
                         sel=tags.index("Q4_K_M") if "Q4_K_M" in tags else 0)

        self.background(work)

    def mode_popup(self, title="Mode"):
        a = self.agent
        free = {"read": True, "write": a.mode != "ask", "edit": a.mode != "ask", "shell": a.mode == "auto", "fetch": a.mode == "auto"}
        rows = [row([("● " if m == a.mode else "  ", m), (f"{m:<7}", m), (ABOUT[m], "")], m) for m in MODES]
        rows += [row(""), row([(f"in {a.mode} mode", "dim")])]
        rows += [row([(f"  {tool:<7}", ""), ("runs without asking", "green") if ok else ("asks first", "yellow")]) for tool, ok in free.items()]
        if title == "Permissions":  # the calls that never ask, whatever the mode: enter removes one
            rows += [row(""), row([("always allowed" if a.allow else "nothing is always allowed yet", "dim")])]
            rows += [row([("  " + rule, ""), ("   enter removes", "dim")], ("rule", n)) for n, rule in enumerate(a.allow)]
            rows += [row("  Add a rule…", ("add", 0))]

        def add(text):
            said = flat((sprint.command(a, "/permissions allow " + text) or [[]])[0])
            self.mode_popup(title)
            self.tell(said if "cannot" in said else "")

        def pick(choice):
            if isinstance(choice, str):
                self.choose("/mode " + choice)
            elif choice[0] == "rule":
                sprint.command(a, f"/permissions forget {choice[1] + 1}")
                self.mode_popup(title)
            else:
                self.pop("Always allow", [row([("a tool and a pattern, for example: shell git status*", "dim")])],
                         field={"label": "rule ›", "text": "", "submit": add}, back=lambda: self.mode_popup(title))

        self.pop(title, rows, pick=pick, sel=MODES.index(a.mode))

    def dir_popup(self, at=None):
        """a small folder browser: enter goes into a folder; nothing changes until you choose to work there"""
        at = os.path.abspath(at or os.getcwd())
        tilde = lambda p: p.replace(str(Path.home()), "~", 1)
        try:
            subs = sorted(d.name for d in os.scandir(at) if d.is_dir() and not d.name.startswith("."))
        except OSError:
            subs = []
        rows = [row([("● work here: ", self.agent.mode), (tilde(at), "bold")], ("use", at), keep=True),
                row([("  ../", "path"), ("   up one folder", "dim")], ("go", os.path.dirname(at)), keep=True)]
        rows += [row([("  " + name + "/", "path")], ("go", os.path.join(at, name))) for name in subs]
        recent = [p for p in sprint.load_config().get("recent", []) if p != at and os.path.isdir(p)]
        if recent:
            rows += [row(""), row([("recent", "dim")])] + [row([("  " + tilde(p), "")], ("use", p)) for p in recent]
        rows += [row("  Type a path…", ("type", at), keep=True)]
        self.pop("Directory", rows, pick=self.dir_pick, field={"label": "›", "text": "", "filter": True})

    def dir_pick(self, choice):
        kind, path = choice
        if kind == "go":
            self.dir_popup(path)
        elif kind == "use":
            self.set_dir(path)
        else:
            self.pop("Directory", [row([("a folder path, for example ~/code/app", "dim")])],
                     field={"label": "path ›", "text": "", "submit": self.set_dir}, back=lambda: self.dir_popup(path))

    def set_dir(self, path):
        if self.busy():
            self.popup = None
            return self.tell("wait for the current turn to finish before changing folder")
        said = flat((sprint.command(self.agent, "/directory " + path) or [[]])[0])
        self.popup = self.popup if "cannot" in said else None
        self.tell(said)

    def race_popup(self):
        rivals = [n for n in self.installed() if n != self.agent.model]
        rows = [row([(f"{short(n) if len(short(n)) <= 40 else short(n)[:39] + '…':<41}", ""), (self.details.get(n, ("",))[0], "dim")], n)
                for n in rivals] or [row([("there is no second model installed: add one with /model", "dim")])]
        self.pop("Race against", rows, pick=self.race_ask, field={"label": "›", "text": "", "filter": True})

    def race_ask(self, rival):
        last = next((i["text"] for i in reversed(self.items) if i["t"] == "user"), "")
        self.pop("Race: " + short(rival), [row([("both models get this message; they may read files but change nothing", "dim")])],
                 field={"label": "message ›", "text": last, "submit": lambda text: self.race(rival, text)}, back=self.race_popup)

    def race(self, rival, task):
        mine = self.pop("Race", [row([("starting…", "dim")])])

        def tell(model):
            mine["rows"] = [row([("running ", "dim"), (short(model), "bold"), (" …", "dim")])]

        def work():
            try:
                results = sprint.race(self.agent, rival, task, tell)
            except Exception as e:  # shown in the popup rather than lost in a thread
                results = str(e)
            if self.popup is mine:
                self.pop("Race", self.race_rows(results), pick=self.use_model) if isinstance(results, list) else \
                    self.pop("Race", [row([(results, "err")])])

        self.background(work)

    def race_rows(self, results, col=35):
        """two columns: each model's time, speed and answer, then the choice of which to carry on with"""
        fastest = min(results, key=lambda r: r["seconds"])
        cols = []
        for r in results:
            stats = f"{r['seconds']:.1f}s · {r['rate']:.0f} tok/s · {r['steps']} tool calls" + ("  ★ faster" if r is fastest else "")
            answer = [x for line in r["answer"].split("\n") for x in wrap([(line, "")], col)]
            cols.append([[(short(r["model"]), "bold")], [(stats, self.agent.mode if r is fastest else "dim")], []] + answer[:14])
        rows = [row(clip(cols[0][i] if i < len(cols[0]) else [], col) + [(" │ ", "frame")] + clip(cols[1][i] if i < len(cols[1]) else [], col))
                for i in range(max(map(len, cols)))]
        return rows + [row(""), row([("keep ", "dim"), (short(results[0]["model"]), "bold")], results[0]["model"]),
                       row([("switch to ", "dim"), (short(results[1]["model"]), "bold")], results[1]["model"])]

    def effort_popup(self):
        a, levels = self.agent, list(sprint.EFFORT)
        rows = [row([("● " if e == a.effort else "  ", a.mode), (f"{e:<8}", "bold" if e == a.effort else ""),
                     (f"replies up to {a.ctx // n:,} tokens, {k} steps per message", "dim")], e) for e, (n, k) in sprint.EFFORT.items()]
        self.pop("Effort", rows, pick=lambda e: self.choose("/effort " + e), sel=levels.index(a.effort))

    def context_popup(self):
        self.pop("Context", [row(list(r)) for r in sprint.context_rows(self.agent)])

    def help_popup(self):
        self.pop("Help", [row([(line, "bold" if line and not line.startswith(" ") else "")]) for line in HELP.split("\n")])

    def config_popup(self, sel=0):
        a = self.agent
        now = {"model": a.model, "url": a.url, "ctx": a.ctx, "steps": a.steps, "mode": a.mode, "think": a.think, "effort": a.effort,
               "sidebar": self.sidebar, "sidebar_w": self.side_w, "history": self.history, "history_w": self.left_w, "motion": self.motion,
               "theme": self.theme, "peek": self.peek, "timeout": a.timeout, "plan": self.plan}
        rows = [row([(f"{k:<11}", "dim"), ("on" if v is True else "off" if v is False else str(v), "")], k) for k, v in now.items()]
        self.pop("Settings", rows, pick=lambda k: self.config_pick(k, now[k], list(now).index(k)), sel=sel)

    def config_pick(self, key, value, at):
        cycle = {"mode": MODES, "effort": tuple(sprint.EFFORT), "theme": tuple(THEMES)}.get(key)
        if isinstance(value, bool):
            self.config_set(key, "off" if value else "on", at)
        elif cycle:
            self.config_set(key, cycle[(cycle.index(value) + 1) % len(cycle)], at)
        else:  # a name or a number: type the new value
            self.pop("Settings: " + key, [row([("enter a new value", "dim")])], back=lambda: self.config_popup(at),
                     field={"label": key + " ›", "text": str(value), "submit": lambda text: self.config_set(key, text, at)})

    def config_set(self, key, text, at):
        rows = sprint.command(self.agent, f"/config {key} {text}") or [[]]
        self.load_ui()
        self.config_popup(at)
        if "cannot" in flat(rows[0]) or "needs" in flat(rows[0]):
            self.tell(flat(rows[0]))

    # --- the input area

    def trail(self):
        """the four shade cells that lead every prompt, in the mode colour; they ripple while the agent works"""
        k = int(time.time() * 8) if self.motion and self.busy() else 0
        return [("".join("░▒▓█"[(i - k) % 4] for i in range(4)), self.agent.mode)]

    def input_rows(self, bw, used=0, side=0):
        """a rule carrying the mode and model, then the prompt (or the approval question) -> (rows, typed lines)"""
        a, tw = self.agent, bw - 5
        typed = [self.buf[i:i + tw] for i in range(0, len(self.buf) + 1, tw)][-5:]
        left = [("── ", "frame"), (a.mode, a.mode), (" ", "")]
        right = f" {short(a.model)}" + ("" if side else f" · {used}%") + " "
        rule = left + [("─" * max(0, bw - len(flat(left)) - len(right) - 2), "frame"), (right, "dim"), ("──", "frame")]
        if self.pending:
            c = self.pending
            what = c.body.split("\n")[0] if c.tool == "shell" else c.arg
            keys = [("     y", "bold"), (" once   ", "dim"), ("a", "bold"), (" always this   ", "dim"), ("n", "bold"), (" no   ", "dim"),
                    ("esc", "bold"), (" stop", "dim")]
            return [rule, self.trail() + [(" allow ", a.mode), (c.tool, "bold"), ("  " + what, "")], keys], typed
        body = [[(t, "")] for t in typed] if self.buf else [[("Ask anything…  / for commands", "dim")]]
        return [rule] + [(self.trail() if n == 0 else [("    ", "")]) + [(" ", "")] + r for n, r in enumerate(body)], typed

    # --- conversation -> lines of (kind, spans, item)

    def window(self, it, w):
        """a tool call as a bordered window, every line exactly w wide"""
        c, inner = it["call"], w - 4
        body = []
        if c.tool == "shell":
            body = [[("$ " if i == 0 else "  ", "dim"), (x, "bold")] for i, x in enumerate(c.body.splitlines())]
        elif c.tool == "edit":
            old, new = split_edit(c.body) or ("", c.body)
            body = [[("- " + x, "del")] for x in old.splitlines()] + [[("+ " + x, "add")] for x in new.splitlines()]
        elif c.tool == "write":
            body = [[("+ " + x, "add")] for x in c.body.splitlines()]
        if it["result"] is not None:
            role = "err" if it["state"] in ("failed", "denied") else "" if c.tool in ("shell", "read", "fetch") else "dim"
            body += [[(x.replace("\t", "    "), role)] for x in it["result"].splitlines()]
        hidden = 0 if it["open"] else max(0, len(body) - self.peek)
        rows = [r for b in body for r in wrap(b, inner)] if it["open"] else body[:self.peek]

        state = {"waiting": "allow?", "running": "running"}.get(it["state"], it["state"])
        srole = {"done": "ok", "failed": "err", "denied": "err", "waiting": self.agent.mode}.get(it["state"], "warn")
        head = [("╭─ " + ("▾ " if it["open"] else "▸ "), "frame"), (c.tool, "bold")]
        tail = [(f" {state} ", srole), ("─╮", "frame")]
        room = w - len(flat(head)) - len(flat(tail)) - 3
        flag = ""
        if c.tool == "edit":
            old, new = split_edit(c.body) or ("", c.body)
            flag = f"  +{len(new.splitlines())} -{len(old.splitlines())}"
        elif c.tool == "write":
            flag = "  replaces file" if it["old"] else "  new file"
        flag = flag if room > 24 else ""
        target = c.body.split("\n")[0] if c.tool == "shell" else c.arg
        target = (f"{len((it['result'] or '').split())} words" if c.tool == "think" else target)[:max(0, room - len(flag))]
        mid = [("  " + target, "url" if c.tool == "fetch" else "" if c.tool == "shell" else "path"),
               (flag, "warn" if it["old"] else "dim")]
        gap = w - len(flat(head + mid + tail))
        top = head + mid + [(" " + "─" * (gap - 1), "frame")] + tail
        hint = f" … +{hidden} lines · ctrl+o " if hidden else " ctrl+o to collapse " if it["open"] and len(body) > self.peek else ""
        foot = [("╰─", "frame"), (hint, "dim"), ("─" * (w - 3 - len(hint)) + "╯", "frame")]
        return [("", top, it)] + [("", [("│ ", "frame")] + clip(r, inner) + [(" │", "frame")], it) for r in rows] + \
               [("", foot, it)]

    def lines(self, w):
        out = []
        for it in self.items:
            key = (w, len(it.get("text", "")), it.get("open"), it.get("state"), self.agent.mode, self.peek, it.get("at"))
            if self.cache.get(id(it), (None,))[0] != key:
                if it["t"] == "tool":
                    rows = self.window(it, w)
                elif it["t"] == "user":
                    rows = [r for line in it["text"].split("\n") for r in wrap([(line, "bold")], w - 5)]
                    rows = [("user", ([("░▒▓█", self.agent.mode), (" ", "")] if n == 0 else [("     ", "")]) + r, it)
                            for n, r in enumerate(rows)]
                elif it["t"] == "plan":  # plan mode's checklist: done, doing now, still to do
                    at, me = it["at"], self.agent.mode
                    rows = [("", [("  ", ""), ("✓ " if n < at else "▸ " if n == at else "· ", "green" if n < at else me if n == at else "dim"),
                                  (step[:max(8, w - 6)], "bold" if n == at else "dim")], None) for n, step in enumerate(it["steps"])]
                elif it["t"] == "note":
                    rows = [("", [("  ", "")] + r, None) for line in it["text"].split("\n")
                            for r in wrap([(line, it["role"])], w - 2)]
                else:
                    rows = [("", [("  ", "")] + r if r else r, None) for spans in prose(it["text"].strip("\n"))
                            for r in wrap(spans, w - 2)]
                self.cache[id(it)] = (key, rows)
            out += self.cache[id(it)][1] + [("", [], None)]
        return out

    # --- drawing

    def attr(self, role, panel=False):
        colour, extra = ROLE.get(role, (0, 0))
        return (curses.color_pair((10 if panel else 0) + colour) if self.colors else 0) | extra

    def mark(self, scr, y, x, n, attr):
        """restyle n cells that are already drawn (a highlight); skipped where the curses at hand cannot"""
        try:
            scr.chgat(y, x, n, attr)
        except (AttributeError, curses.error):
            pass

    def put(self, scr, y, x, spans, panel=False, width=None):
        h, w = scr.getmaxyx()
        if isinstance(spans, str):
            spans = [(spans, "")]
        if width is not None:
            spans = clip(spans, width)
        for text, role in spans:
            if 0 <= y < h and x < w and text:
                try:
                    scr.addstr(y, x, text[:w - x], self.attr(role, panel))
                except curses.error:
                    pass  # writing the bottom-right cell raises after drawing it
            x += len(text)

    def draw(self, scr):
        h, w = scr.getmaxyx()
        scr.erase()
        self.hits, self.screen = {}, {}
        if h < 12 or w < 50:
            self.put(scr, 0, 0, "sprint needs at least 50×12")
            return scr.refresh()
        a, home = self.agent, not self.items
        side = self.side_w if not home and self.sidebar and w - self.side_w >= ROOM else 0
        lw = self.left_w if not home and self.history and w - side - self.left_w >= ROOM else 0
        x0, self.geo = lw + 2, (w, lw, side)
        used = min(100, a.used() * 100 // a.ctx)

        # input panel (or the approval prompt in its place)
        bw = min(w - 8, 76) if home else w - side - lw - 4
        bx = (w - bw) // 2 if home else x0
        rows, typed = self.input_rows(bw, used, side)
        art = []
        if home:  # the wordmark, with its wave unless motion is off or there is no room for it
            art = logo_rows(time.time() if self.motion else None) if w >= LOGO_W + 6 and h >= 20 else \
                [[(c, SHADE[c]) for c in "░▒▓█"] + [(" sprint", "bold")]]
        logo_h = len(art) + 2 if home else 0
        top = max(1, (h - logo_h - len(rows) - 3) // 2) + logo_h if home else h - 1 - len(rows)
        for i, r in enumerate(rows):
            self.put(scr, top + i, bx, r, width=bw)

        if home:
            for i, row in enumerate(art):
                self.put(scr, top - logo_h + i, (w - len(flat(art[0]))) // 2, row)
            tips = [("tab", "bold"), (" mode   ", "dim"), ("/", "bold"), (" commands   ", "dim"),
                    ("ctrl+c", "bold"), (" quit", "dim")]
            self.put(scr, top + len(rows) + 1, (w - len(flat(tips))) // 2, tips)
            self.put(scr, h - 1, 2, [(os.getcwd().replace(str(Path.home()), "~", 1), "dim")], width=w - 20)
            self.put(scr, h - 1, w - 12, [("sprint 0.3", "dim")])
        else:
            # conversation
            mw, vh = w - side - lw - 4, top - 2  # a blank row above and below the conversation
            self.page = max(1, vh - 2)
            lines = self.lines(mw)
            if self.jump is not None:  # a click in a side panel: bring that item to the top
                at = next((i for i, row in enumerate(lines) if row[2] is self.jump), None)
                self.scroll = self.scroll if at is None else len(lines) - vh - max(0, at - 1)
                self.jump = None
            self.scroll = max(0, min(self.scroll, len(lines) - vh))
            first = max(0, len(lines) - vh - self.scroll)
            for i, (kind, spans, item) in enumerate(lines[first:first + vh]):
                self.screen[1 + i] = (x0, flat(spans))
                if kind == "user":  # your own messages stand out: a shaded band, or reverse video without 256 colours
                    self.put(scr, 1 + i, x0, spans, panel=True, width=mw)
                    if not self.rich:
                        self.mark(scr, 1 + i, x0, mw, curses.A_REVERSE)
                else:
                    self.put(scr, 1 + i, x0, spans)
                if item:
                    self.hits[("main", 1 + i)] = item
            if self.select:  # the text being dragged over
                (ya, xa), (yb, xb) = sorted([self.select["a"], self.select["b"]])
                for y in range(max(1, ya), min(top - 1, yb + 1)):
                    left = max(x0, xa) if y == ya else x0
                    right = min(x0 + mw - 1, xb) if y == yb else x0 + mw - 1
                    if right >= left:
                        self.mark(scr, y, left, right - left + 1, curses.A_REVERSE)
            # status line and footer
            keys = [("tab", "bold"), (" mode  ", "dim"), ("ctrl+o", "bold"), (" tools  ", "dim"),
                    ("pgup/pgdn", "bold"), (" scroll  ", "dim"), ("/help", "bold")]
            if self.busy():
                doing = "waiting for you" if self.pending else self.note or "working"  # the trail on the prompt is the spinner
                self.put(scr, h - 1, x0, [(doing, "dim")], width=max(10, mw // 2 - 2))
                keys = [("esc", "bold"), (" interrupt  ", "dim")] + keys[:4]
            elif self.scroll:
                self.put(scr, h - 1, x0, [("↓ newer messages below", "warn")])
            keys = keys if len(flat(keys)) < mw - 24 else keys[:4]
            self.put(scr, h - 1, max(x0, w - side - 2 - len(flat(keys))), keys)
            if side:  # right panel: the context window in small, then what the agent has done
                x, inner = w - side + 1, side - 3
                cells = sprint.context_cells(a, inner * 2)
                self.put(scr, 1, x, [("Context", "dim"), (f"{used}%".rjust(inner - 7), "")], width=inner)
                self.put(scr, 2, x, cells[:inner])
                self.put(scr, 3, x, cells[inner:])
                self.put(scr, 4, x, [(f"{kilo(a.used())} of {kilo(a.ctx)} tokens", "dim")], width=inner)
                self.put(scr, 5, x, [(self.speed_text(), "dim")], width=inner)
                self.put(scr, 6, x, [("● " + a.mode, a.mode), ("  " + short(a.model), "dim")] + ([("  plan", "dim")] if self.plan else []),
                         width=inner)
                self.put(scr, 8, x, [("Activity", "dim")], width=inner)
                log = self.activity()[-max(1, h - 12):]
                for i, (item, spans) in enumerate(log):
                    self.put(scr, 9 + i, x, spans, width=inner)
                    self.hits[("side", 9 + i)] = item
                if not log:
                    self.put(scr, 9, x, [("nothing yet", "dim")])
                self.put(scr, h - 1, x, [(os.getcwd().replace(str(Path.home()), "~", 1), "dim")], width=inner)
                for y in range(h):
                    self.put(scr, y, w - side - 1, [("┃" if self.drag == "right" else "│", a.mode if self.drag == "right" else "frame")])
            if lw:  # left panel: your messages, newest at the bottom
                rows = self.outline()
                self.put(scr, 1, 1, [("Messages", "dim")], width=lw - 3)
                for i, (item, title, meta) in enumerate(rows[-max(1, (h - 4) // 2):]):
                    latest = item is rows[-1][0]
                    self.put(scr, 3 + i * 2, 1, [(t, "bold" if latest and not r else r) for t, r in title], width=lw - 3)
                    self.put(scr, 4 + i * 2, 1, meta, width=lw - 3)
                    self.hits[("left", 3 + i * 2)] = self.hits[("left", 4 + i * 2)] = item
                for y in range(h):
                    self.put(scr, y, lw, [("┃" if self.drag == "left" else "│", a.mode if self.drag == "left" else "frame")])

        if self.note_text() and not self.busy():
            self.put(scr, h - 1, 2 if home else x0, [(self.note_text(), "warn")], width=max(20, (w - side - lw) // 2))

        # completion popup, drawn over whatever is above the input
        every = [] if self.pending or self.popup else self.options()
        chosen = min(self.pick, len(every) - 1)
        start = max(0, min(chosen - 3, len(every) - 8))  # eight at a time, kept around the chosen one
        opts = every[start:start + 8]
        for i, (name, about) in enumerate(opts):
            on = start + i == chosen
            self.put(scr, top - len(opts) + i, bx, [(f"     {name:<24}", "bold" if on else ""), (about, "dim")], width=bw)
            if on:
                self.mark(scr, top - len(opts) + i, bx + 3, bw - 3, curses.A_REVERSE)

        if self.popup:  # the standard popup, centred over everything
            pw = max(40, min(78, w - 6))
            plines, sel_at, field_at = self.popup_lines(pw, tall=h - 6)
            py, px = max(1, (h - len(plines)) // 2), (w - pw) // 2
            for i, spans in enumerate(plines):
                self.put(scr, py + i, px, spans)
                if i == sel_at:
                    self.mark(scr, py + i, px + 2, pw - 4, curses.A_REVERSE)
            curses.curs_set(1 if field_at or self.popup["edit"] else 0)
            try:
                if self.popup["edit"]:
                    scr.move(py + self.popup["cursor"][0], min(px + pw - 3, px + 2 + self.popup["cursor"][1]))
                elif field_at:
                    f = self.popup["field"]
                    scr.move(py + field_at, min(px + pw - 3, px + 3 + len(f["label"]) + len(f["text"])))
            except curses.error:
                pass
        elif self.pending:
            curses.curs_set(0)
        else:
            curses.curs_set(1)
            tw = bw - 5
            row = len(typed) - 1 if self.cur >= (len(typed) - 1) * tw else self.cur // tw
            col = self.cur - (len(self.buf) // tw - (len(typed) - 1) + row) * tw
            try:
                scr.move(top + 1 + row, bx + 5 + max(0, min(col, tw)))
            except curses.error:
                pass
        scr.refresh()

    def loop(self, scr, first=""):
        curses.raw()
        scr.keypad(True)
        scr.timeout(80)
        self.colors = curses.has_colors()
        self.paint()
        self.set_mouse()
        if first:
            self.submit(first)
        if VT:
            sys.stdout.write("\033[?2004h")  # ask the terminal to mark pastes, so a dropped file can become a chip
            sys.stdout.flush()
        fresh = was = True
        while not self.quit:
            now = self.active()
            if fresh or now or was:  # one more frame after things settle, then leave the screen alone
                self.draw(scr)
            quick = self.busy() or self.pending or self.drag or self.select or self.esc is not None or self.pasting is not None
            scr.timeout(80 if quick else 160 if now else 1000)
            was = now
            try:
                self.take(scr.get_wch())
                fresh = True
            except curses.error:
                fresh = self.esc is not None  # no key in time: a held escape was just escape
                self.idle()
        self.interrupt()
        sys.stdout.write("\033[?1002l\033[?2004l" if VT else "")
        sys.stdout.flush()


def run(agent, first=""):
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(Screen(agent).loop, first)
