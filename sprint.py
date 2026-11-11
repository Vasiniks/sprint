#!/usr/bin/env python3
"""sprint - a tiny terminal agent for tiny models with tiny contexts (8K). stdlib only."""
import argparse
import base64
import collections
import fnmatch
import glob
import html
import http.client
import io
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

SYSTEM = """You are a coding agent in the user's project folder. Use relative paths.
To act, reply with ONE tool block, then stop and wait for the result:

```shell
ls
```
```read main.py
```
```write main.py
entire new file content
```
```edit main.py
exact old lines
====
new lines
```
```fetch https://example.com
```

Any other code block is NOT saved or run.
When the task is done, reply with a short answer and no tool block."""

SYSTEM_TOOLS = """You are a coding agent in the user's project folder. Use relative paths.
Use the tools to do the task, one step at a time. When it is done, reply with a short answer."""


WINDOWS = os.name == "nt"  # there: PowerShell instead of bash, taskkill instead of process groups, %APPDATA%


def here(cap=400):
    """the folder's files, for the system prompt: tiny models stop claiming they can't see them"""
    names = ", ".join(sorted(x.name + "/" * x.is_dir() for x in Path(".").iterdir() if not x.name.startswith(".")))
    return "Files here: " + (names if len(names) <= cap else names[:cap] + " ...") if names else "The folder is empty."


def _tool(name, about, *params):
    return {"type": "function", "function": {"name": name, "description": about, "parameters": {
        "type": "object", "properties": {p.rstrip("?"): {"type": "string"} for p in params},
        "required": [p for p in params if not p.endswith("?")]}}}  # a trailing ? marks an optional one


def bash_path():
    """bash, when there is a real one. On Windows that is Git Bash (never the WSL launcher), or None"""
    found = shutil.which("bash")
    if not WINDOWS:
        return found or "/bin/sh"
    if found and "system32" not in found.lower() and "windowsapps" not in found.lower():
        return found
    git = shutil.which("git")  # Git for Windows keeps its bash beside it, often off the PATH
    guess = os.path.join(os.path.dirname(os.path.dirname(git)), "bin", "bash.exe") if git else ""
    return guess if os.path.isfile(guess) else None


def tools():
    return [_tool("shell", f"Run a {'bash' if bash_path() else 'PowerShell'} command", "command"),
            _tool("read", "Read a file (from_line starts part-way into a long one), or list a folder", "path", "from_line?"),
            _tool("write", "Create or overwrite a file", "path", "content"),
            _tool("edit", "Replace exact text in a file", "path", "old", "new"),
            _tool("fetch", "Get a web page as text", "url")]


TOOLS = tools()
NEXT = "If the task is done, say so in plain words. Otherwise send the next tool block."
REFUSAL = re.compile(r"(do not|don't|cannot|can't|can not|unable to)( directly)? (have )?(access|read|see|open|view)|"
                     r"(provide|share|paste|copy)[^.]{0,40}(content|file|path|location)", re.I)
ALIAS = {"shell": "shell", "bash": "shell", "sh": "shell", "zsh": "shell", "tool": "shell", "read": "read",
         "write": "write", "edit": "edit", "fetch": "fetch", "webfetch": "fetch"}
Call = collections.namedtuple("Call", "tool arg body end start")
MODES = ("ask", "edits", "auto")
FENCE = re.compile(r"^\s*(`{3,})[ \t]*([A-Za-z_]+)[ \t]*(.*)$")


# Every web request notes its socket under its thread, so that another thread can hang up on it. Ollama sends
# nothing at all, not even headers, while a model writes a long tool call; hanging up is the only way to stop it.
SOCKETS = {}


class _Connection(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        SOCKETS[threading.get_ident()] = self.sock


class _Handler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_Connection, req)


urllib.request.install_opener(urllib.request.build_opener(_Handler))


# ---------- reply -> tool call ----------

def _call(tool, arg, body, end, start):
    if tool == "shell":
        arg, body = "", "\n".join(x for x in (arg, body.strip()) if x)
    elif tool in ("read", "fetch"):
        arg, body = (arg or body).strip().split("\n")[0].strip(), ""
    elif not arg:  # write/edit with the path on the first body line
        arg, _, body = body.partition("\n")
    return Call(tool, arg.strip(), body, end, start)


def from_args(name, a, end, start):
    """a JSON-style tool call (native, or typed out by the model) -> Call, or None"""
    try:
        a = json.loads(a) if isinstance(a, str) else a
    except ValueError:
        return None
    tool = ALIAS.get(str(name).lower())
    if not tool or not isinstance(a, dict):
        return None
    get = lambda *keys: next((str(a[k]) for k in keys if a.get(k)), "")
    body = get("old") + "\n====\n" + get("new") if tool == "edit" else get("content", "command", "cmd")
    where = get("from_line", "start", "line", "offset") if tool == "read" else ""
    return _call(tool, get("path", "file", "url") + (" " + where if where.isdigit() else ""), body, end, start)


def _json_call(text):
    """fallback for models that type out {"name": ..., "arguments": {...}} as text"""
    for m in re.finditer(r"\{", text):
        try:
            obj = json.JSONDecoder().raw_decode(text[m.start():])[0]
            call = from_args(obj.get("name") or obj.get("tool"),
                             obj.get("arguments") or obj.get("args") or obj.get("parameters") or {}, len(text), m.start())
        except (ValueError, AttributeError):
            continue
        if call:
            return call


def parse(text, final=False):
    """first tool block in a reply, or None. Unclosed blocks only count once the reply is final."""
    tool, pos = None, 0
    for line in text.splitlines(keepends=True):
        pos += len(line)
        s = line.strip()
        if tool is None:
            m = FENCE.match(line.rstrip("\r\n"))
            if m and m[2].lower() in ALIAS:
                fence, tool, arg, body, depth, start = m[1], ALIAS[m[2].lower()], m[3], [], 0, pos - len(line)
        elif s == fence and not depth:
            return _call(tool, arg, "".join(body), pos, start)
        else:
            if s.startswith("```"):  # a code fence inside the file being written
                depth = depth + 1 if s.strip("`") else (depth - 1 if depth else 1)
            body.append(line)
    if not final:
        return None
    return _call(tool, arg, "".join(body), len(text), start) if tool else _json_call(text)


def unthink(text):
    return re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.S)


# ---------- tools ----------

def clip(s, cap):
    if len(s) <= cap:
        return s
    return f"{s[:cap * 2 // 3]}\n[... {len(s) - cap} chars cut ...]\n{s[-(cap // 3):]}"


def read(arg, cap=4000):
    path, start = arg.strip(), 1
    m = re.match(r"(.+?)[\s:]+(\d+)$", path)
    if m and not os.path.exists(os.path.expanduser(path)):
        path, start = m[1], max(int(m[2]), 1)
    p = Path(path).expanduser()
    if p.is_dir():
        return clip("\n".join(sorted(x.name + "/" * x.is_dir() for x in p.iterdir())) or "(empty dir)", cap)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    out, used, i = [], 0, start - 1
    while i < len(lines) and (not out or used + len(lines[i]) < cap):
        out.append(lines[i][:cap])
        used += len(lines[i]) + 1
        i += 1
    text = "\n".join(out) if out else f"(nothing at line {start}; file has {len(lines)} lines)"
    if i < len(lines):
        text += f"\n[lines {start}-{i} of {len(lines)}; next: read {path} {i + 1}]"  # natively: from_line
    return text


def write(path, body):
    if not body.strip():
        return "error: write needs the file content inside the block"
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body if body.endswith("\n") or not body else body + "\n", encoding="utf-8")
    n = len(body.splitlines())
    return f"wrote {path} ({n} line{'s' * (n != 1)})"


def split_edit(body):
    parts = re.split(r"^={3,}[ \t]*$\n?", body, maxsplit=1, flags=re.M)
    return (parts[0].strip("\n"), parts[1].strip("\n")) if len(parts) == 2 and parts[0].strip() else None


def edit(path, body):
    if not split_edit(body):
        return "error: edit needs the exact old lines, then a line ====, then the new lines"
    old, new = split_edit(body)
    p = Path(path).expanduser()
    src = p.read_text(encoding="utf-8", errors="replace")
    n = src.count(old)
    if n > 1:
        return f"error: old lines match {n} places; include more surrounding lines"
    if n == 1:
        src = src.replace(old, new)
    else:  # retry ignoring indentation, then re-indent the new lines to fit
        have, want = src.split("\n"), [x.strip() for x in old.split("\n")]
        hits = [i for i in range(len(have) - len(want) + 1) if [x.strip() for x in have[i:i + len(want)]] == want]
        if len(hits) != 1:
            return "error: old lines not found; read the file and copy them exactly"
        i = hits[0]
        pad = " " * (len(have[i]) - len(have[i].lstrip()) - len(old) + len(old.lstrip()))
        have[i:i + len(want)] = [pad + x if x.strip() else x for x in new.split("\n")]
        src = "\n".join(have)
    p.write_text(src, encoding="utf-8")
    return f"edited {path}"


RUNNING = []  # shell commands in flight, so an interrupt can stop them


def kill_running():
    for p in list(RUNNING):
        kill_tree(p)


def shell_argv(cmd, tmp):
    """the command line that runs cmd and then notes, in the file tmp, which folder a `cd` left it in"""
    bash = bash_path()
    if bash:  # on Windows, pwd -W gives the folder as a path Windows understands
        return [bash, "-c", f"{cmd}\n__rc=$?; pwd{' -W' if WINDOWS else ''} > '{tmp}'; exit $__rc"]
    new = shutil.which("pwsh")  # PowerShell 7 knows &&; the one built into Windows needs it spelled ;
    script = (f"[Console]::OutputEncoding = [Text.Encoding]::UTF8\n{cmd if new else cmd.replace(' && ', '; ')}\n$__ok = $?\n"
              "$__rc = if ($__ok) { 0 } elseif ($LASTEXITCODE) { $LASTEXITCODE } else { 1 }\n"
              f"(Get-Location).Path | Out-File -Encoding utf8 '{tmp}'\nexit $__rc")
    return [new or shutil.which("powershell") or "powershell", "-NoProfile", "-NonInteractive", "-Command", script]


def job_for(p):
    """Windows: put a command in a job object, the one thing that reliably ends everything it started
    (Git Bash's children slip out of the process tree that taskkill follows)"""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.CreateJobObjectW.restype = ctypes.c_void_p
        k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        job = k.CreateJobObjectW(None, None)
        return job if job and k.AssignProcessToJobObject(job, int(p._handle)) else None
    except (AttributeError, ImportError, OSError):
        return None


def kill_tree(p):
    """stop a command and everything it started"""
    try:
        if WINDOWS:
            if getattr(p, "job", None):
                import ctypes
                ctypes.windll.kernel32.TerminateJobObject(ctypes.c_void_p(p.job), 1)
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass


def own_group():
    """Popen options that give a command its own process group, so stopping it never stops sprint"""
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}


def shell(cmd, cap=4000, timeout=60):
    fd, tmp = tempfile.mkstemp()
    os.close(fd)
    p = subprocess.Popen(shell_argv(cmd, tmp), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", **own_group())
    p.job = job_for(p) if WINDOWS else None
    RUNNING.append(p)
    note = ""
    try:
        out = p.communicate(timeout=timeout)[0]
        cwd = Path(tmp).read_text(encoding="utf-8-sig", errors="replace").strip()
        if cwd:
            os.chdir(cwd)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as e:
        kill_tree(p)
        try:
            out = p.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:  # something still holds its output open: do not wait for it
            out = ""
        note = f"\n[timed out after {timeout}s]"
        if isinstance(e, KeyboardInterrupt):
            raise
    finally:
        RUNNING.remove(p)
        os.unlink(tmp)
    if p.returncode and not note:
        note = f"\n[exit {p.returncode}]"
    return (clip(out.strip(), cap) or "(no output)") + note


def html_text(page):
    page = re.sub(r"(?is)<(script|style|head|svg|noscript)\b.*?</\1>", " ", page)
    page = re.sub(r"(?i)<(br|/p|/div|/h\d|/li|/tr)\b[^>]*>", "\n", page)
    page = html.unescape(re.sub(r"(?s)<[^>]+>", " ", page))
    lines = (re.sub(r"\s+", " ", x).strip() for x in page.split("\n"))
    return "\n".join(x for x in lines if x)


def fetch(url, cap=4000):
    url = url if "://" in url else "https://" + url
    if not url.startswith(("http://", "https://")):
        raise ValueError("only http(s) URLs")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; sprint)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read(2_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
        if "html" in r.headers.get("Content-Type", ""):
            text = html_text(text)
    return text[:cap] + (f"\n[{len(text) - cap} more chars cut]" if len(text) > cap else "")


IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    return "Image" if ext in IMAGES else "PDF" if ext == ".pdf" else "File"


def pdf_text(path):
    """the text of a PDF: pdftotext when it is installed, else a rough read of the page streams"""
    tool = shutil.which("pdftotext")
    if tool:
        try:
            got = subprocess.run([tool, "-layout", path, "-"], capture_output=True, text=True, errors="replace", timeout=30).stdout
            if got.strip():
                return got
        except (OSError, subprocess.SubprocessError):
            pass
    out = []
    for stream in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", Path(path).read_bytes(), re.S):
        try:
            data = zlib.decompress(stream.group(1))
        except zlib.error:
            data = stream.group(1)
        for op in re.finditer(rb"\[(.*?)\]\s*TJ|\(((?:\\.|[^\\()])*)\)\s*Tj", data, re.S):
            raw = b"".join(re.findall(rb"\(((?:\\.|[^\\()])*)\)", op.group(1))) if op.group(1) is not None else op.group(2)
            out.append(re.sub(rb"\\(.)", rb"\1", raw).decode("latin-1"))
    return "\n".join(out)


def attach(text, cap=4000, chips=None):
    """a message that mentions @path or holds pasted-file chips like [Image #1] -> (the message with those files'
    contents added, [images as base64]); the model then need not go and read them"""
    found = [(label, path) for label, path in (chips or {}).items() if label in text]
    for mention in re.findall(r"(?:^|\s)@(\S+)", text):
        hit = next((p for p in (mention, mention.rstrip(".,;:!?)")) if p and os.path.exists(os.path.expanduser(p))), None)
        if hit and hit not in [p for _, p in found]:
            found.append((f"[{hit}]", hit))
    images = []
    for label, path in found:  # they share the room one tool result would get
        path, each = os.path.expanduser(path), max(400, cap // len(found))
        try:
            if kind_of(path) == "Image":
                images.append(base64.b64encode(Path(path).read_bytes()).decode())
                text += f"\n\n{label} is a picture you can see directly. It is not a text file: do not read or edit it."
                continue
            elif kind_of(path) == "PDF":
                body = clip(pdf_text(path).strip() or "(no text could be read from this PDF)", each)
            else:
                body = read(path, each)
        except OSError as e:
            body = f"error: {e}"
        text += f"\n\n{label}\n{body}"
    return text, images


# ---------- context budget ----------

def weigh(m):  # characters, with a rough allowance for each attached image
    return len(m["content"]) + (len(json.dumps(m["tool_calls"])) if "tool_calls" in m else 0) + 1000 * len(m.get("images", ()))


def fit(msgs, budget, cpt=3.5):
    """shrink [system, task, (assistant, user)...] to the token budget: clip old turns, then drop them."""
    size = lambda: sum(weigh(m) / cpt + 4 for m in msgs)
    dropped = 0
    for m in msgs[2:-4]:
        if size() <= budget:
            return dropped
        if m.get("human"):
            continue
        if len(m["content"]) > 200:
            c = m["content"][:200]
            m["content"] = c + " [...]" + ("\n```" if c.count("```") % 2 else "")
        for t in m.get("tool_calls", []):
            a = t["function"].get("arguments")
            for k in (a if isinstance(a, dict) else {}):
                if isinstance(a[k], str) and len(a[k]) > 200:
                    a[k] = a[k][:200] + " [...]"
    while size() > budget and len(msgs) > 4:
        if msgs[3].get("human"):  # a newer request replaces the old one as the anchor
            msgs[1] = msgs[3]
        del msgs[2:4]
        dropped += 1
    return dropped


# ---------- agent ----------

EFFORT = {"low": (16, 8), "medium": (8, 16), "high": (4, 32)}  # reply budget as window/N tokens, steps per message
SPIN = "You are repeating yourself. If the task is done, say so in plain words. Otherwise do something different."


PLAN = """Break the user's request into a short numbered list of small steps, at most 5.
Each step is one concrete action. Reply with only the list."""


class Agent:
    bare = False  # True while asking for a plan: that call offers no tools
    sampling = {}  # extra Ollama options, e.g. {"temperature": 0.7}; empty keeps the model's own defaults

    def __init__(self, model="hf.co/unsloth/Qwen3.5-0.8B-GGUF:Q4_K_M", url="http://localhost:11434", ctx=8192, steps=16,
                 mode="ask", key="", chat=None, ask=None, out=None, think=False, effort="medium"):
        self.model, self.url, self.ctx, self.steps, self.mode, self.key = model, url, ctx, steps, mode, key
        self.think, self.effort = think, effort  # ask the model to reason first? how much room and how many steps?
        self.custom, self.timeout = "", 60       # your own system prompt, if any; seconds a shell command may run
        self.allow = []                          # calls that never ask: "shell git status*", "write notes/*", ...
        self.rate, self.took = 0.0, 0.0          # tokens a second in the last reply, and how long it took
        self.tally, self.unsaved, self.started = blank(), blank(), time.time()  # this session's use; the part not yet on disk
        self.thread = 0                          # the thread running the current turn, so stop() can hang up on it
        self.limit = 0                           # tokens the next reply may use: free room, up to half the window
        self.cpt = 3.5            # chars per token; only ever revised downward, which is the safe side
        self.tools = not url.rstrip("/").endswith("/v1")  # native tool calls on Ollama, until a model refuses them
        self.out = out or sys.stdout
        self.color = self.out.isatty()
        self.col0, self.busy = True, False  # cursor at line start? status line showing?
        if chat:
            self.chat = chat
        if ask:
            self.ask = ask
        self.reset()

    @property
    def max_out(self):  # tokens always kept free for the reply; it may use more when the window has room
        return self.ctx // EFFORT[self.effort][0]

    @property
    def cap(self):  # max chars of one tool result (~1/7 of the context)
        return self.ctx // 2

    def set_effort(self, level):
        self.effort, self.steps = level, EFFORT[level][1]

    def reset(self):
        self.msgs = [{"role": "system", "content": self.system()}]
        self.touched, self.cancel = [], False  # files changed so far; set cancel to stop the current turn
        self.forgotten = 0  # older turns dropped to stay inside the window

    def compact(self):
        """fold everything before the latest exchange into one short summary of what was asked and done, so the
        conversation can go on in less room -> how many messages that saved (0 when there is nothing to fold)"""
        m = self.msgs
        tail = m[-1:] if m[-1]["role"] == "assistant" else m[-2:]
        old = m[1:len(m) - len(tail)]
        if len(old) < 2:
            return 0
        asks = [x for x in old if x.get("human")]  # an earlier summary counts: it may be holding the request
        current = None if tail[-1].get("human") else (asks[-1] if asks else None)
        lines, doing = [], ""
        for x in old:
            if x.get("summary"):  # an earlier summary: keep its lines, and its request becomes one more thing asked
                lines += [ln for ln in x["content"].split("\n") if ln.startswith("- ")]
                was = x["content"].partition("The current request: ")[2].split("\n")[0]
                lines += [f"- you asked: {was[:100]}"] if was and x is not current else []
            elif x.get("human"):
                lines += [f"- you asked: {x['content'].split(chr(10))[0][:100]}"] if x is not current else []
            elif x["role"] == "assistant":
                raw = (x.get("tool_calls") or [{}])[0].get("function")
                c = from_args(raw.get("name"), raw.get("arguments"), 0, 0) if raw else parse(x["content"], final=True)
                doing = f"{c.tool} {self.target(c).split(chr(10))[0][:60]}" if c else ""
                lines += [] if c or not x["content"].strip() else [f"- answered: {x['content'].strip().split(chr(10))[0][:100]}"]
            elif doing:  # what that call gave back
                got = re.sub(r"^\[\w+ result\]\n", "", x["content"]).strip().split("\n")[0]
                lines += [f"- {doing} → {got[:50]}"]
                doing = ""
        request = (current["content"] if current and not current.get("summary")
                   else current["content"].partition("The current request: ")[2] if current else "")
        room = max(6, self.ctx // 400)
        if len(lines) > room:  # what you asked and what changed files outlast what was merely looked at
            vital = [i for i, ln in enumerate(lines) if ln.startswith(("- you asked", "- write ", "- edit "))]
            vital = vital[:1] + vital[1:][-(room // 2 - 1):]
            rest = [i for i in range(len(lines)) if i not in vital][-(room - len(vital)):]
            lines = [lines[i] for i in sorted(vital + rest)]
        text = "Summary of the conversation so far (older steps, shortened):\n" + "\n".join(lines)
        text += f"\n\nThe current request: {request}" if request else ""
        self.msgs = [m[0], {"role": "user", "content": text, "human": True, "summary": True}] + tail
        self.forgotten += len(old) // 2
        return len(old) - 1

    def count(self, **more):
        """add to this session's tally, and to what the next save writes into the lifetime file"""
        add(self.tally, more)
        add(self.unsaved, more)

    def receipt(self, before, began):
        """what one message cost, given the tally and the time from before it: tokens up and down | how long"""
        sent, wrote, secs = self.tally["sent"] - before["sent"], self.tally["written"] - before["written"], time.time() - began
        return f"↑ {human(sent)} ↓ {human(wrote)} tokens | " + (f"{secs:.1f}s" if secs < 10 else clock(secs))

    def breakdown(self):
        """where the context window goes, in tokens: [(what, tokens), ...] ending with what is free"""
        tok = lambda group: int(sum(map(weigh, group)) / self.cpt) + 4 * len(group)
        rest = self.msgs[1:]
        parts = [("system prompt", tok(self.msgs[:1])), ("tool definitions", int(self.extra() / self.cpt)),
                 ("your messages", tok([m for m in rest if m["role"] == "user" and m.get("human")])),
                 ("model replies", tok([m for m in rest if m["role"] == "assistant"])),
                 ("tool results", tok([m for m in rest if m["role"] == "tool" or m["role"] == "user" and not m.get("human")])),
                 ("reserved for the reply", self.max_out)]
        return parts + [("free", max(0, self.ctx - sum(n for _, n in parts)))]

    def system(self):
        base = SYSTEM_TOOLS if self.tools else SYSTEM
        if self.custom:  # your own prompt; a model without tool calls still needs the block format spelled out
            base = self.custom if self.tools else self.custom + "\n" + SYSTEM.split("\n", 1)[1]
        shell_note = f"\nThis is Windows: shell commands run in {'Git Bash' if bash_path() else 'PowerShell'}." if WINDOWS else ""
        return base + shell_note + "\n" + here()

    def used(self):
        return int((self.extra() + sum(weigh(m) for m in self.msgs)) / self.cpt + 4 * len(self.msgs))

    def extra(self):
        """prompt chars the server adds on top of the messages: the tool schemas"""
        return len(json.dumps(tools())) if self.tools else 0

    def say(self, s, style=""):
        if s:
            self.col0 = s.endswith("\n")
            self.emit(s, style)

    def emit(self, s, style):
        """where output goes; a front end replaces this (and status, ask) with its own"""
        if self.busy:
            self.out.write("\r\033[K")
            self.busy = False
        self.out.write(f"\033[{style}m{s}\033[0m" if style and self.color else s)
        self.out.flush()

    def status(self, s):
        """a transient one-line note (terminals only); the next output replaces it"""
        if not self.color:
            return
        if not self.col0:
            self.out.write("\n")
            self.col0 = True
        self.out.write(f"\r\033[K\033[2m  ⋯ {s}\033[0m" if s else "\r\033[K")
        self.busy = bool(s)
        self.out.flush()

    def cycle(self):
        self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
        return self.mode

    def ask(self, call):
        if not sys.stdin.isatty():
            return False
        a = input("  allow? [Y]es · [n]o · [a]lways this one ").strip().lower()[:1]
        return "always" if a == "a" else a in ("", "y")

    def show_call(self, c):
        self.say("" if self.col0 else "\n")
        self.say("● ", "36")
        self.say(c.tool, "1")
        self.say(f" {c.arg}")
        self.say(" (replaces existing file)" if c.tool == "write" and os.path.isfile(os.path.expanduser(c.arg)) else "", "33")
        self.say("\n")
        if c.tool == "shell":
            rows = [("", ("$ " if i == 0 else "  ") + x) for i, x in enumerate(c.body.splitlines())]
        elif c.tool == "edit":
            old, new = split_edit(c.body) or ("", c.body)
            rows = [("31", "- " + x) for x in old.splitlines()] + [("32", "+ " + x) for x in new.splitlines()]
        else:
            rows = [("32", "+ " + x) for x in c.body.splitlines()]
        for style, x in rows[:10]:
            self.say(f"  │ {x[:160]}\n", style)
        self.say(f"  │ … +{len(rows) - 10} lines\n" if len(rows) > 10 else "", "2")

    def show_result(self, r):
        lines = r.splitlines()
        for x in lines[:8]:
            self.say(f"  │ {x[:160]}\n", "31" if r.startswith(("error", "denied")) else "2")
        self.say(f"  │ … +{len(lines) - 8} lines\n" if len(lines) > 8 else "", "2")

    def chat(self, msgs):
        """stream a reply: yields text chunks, plus {"prompt": n} / {"cut": True} / {"call": {...}} facts"""
        native = not self.url.rstrip("/").endswith("/v1")  # Ollama's own API lets us set num_ctx
        body = {"model": self.model, "messages": msgs, "stream": True}
        if native:  # thinking off: it eats a tiny model's reply budget and made tool use worse in testing
            body.update(options={"num_ctx": self.ctx, "num_predict": self.limit or self.max_out, **self.sampling},
                        think=self.effort if self.think else False, keep_alive="30m")
            if self.tools and not self.bare:
                body["tools"] = tools()
        else:
            body.update(max_tokens=self.limit or self.max_out, stream_options={"include_usage": True})
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        req = urllib.request.Request(self.url.rstrip("/") + ("/api/chat" if native else "/chat/completions"),
                                     json.dumps(body).encode(), headers)
        try:
            r = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors="replace")[:300]
            if e.code == 400 and self.tools and "tools" in err:  # no tool-call template: use fenced blocks instead
                self.tools = False
                self.msgs[0]["content"] = self.system()
                yield from self.chat([dict(m, content=self.system()) if m["role"] == "system" else m for m in msgs])
                return
            if e.code == 400 and self.think and "thinking" in err:  # this model cannot reason first
                self.think = False
                yield from self.chat(msgs)
                return
            hint = f" - try: ollama pull {self.model}" if e.code == 404 and native else ""
            raise RuntimeError(f"{e.code} from {self.url}: {err}{hint}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"cannot reach {self.url} ({e.reason}) - is the model server running?") from None
        thinking = False
        with r:
            for line in r:
                line = line.decode("utf-8", "replace").strip()
                line = line[5:].strip() if line.startswith("data:") else line
                if not line.startswith("{"):
                    continue
                d = json.loads(line)
                if d.get("error"):
                    raise RuntimeError(str(d["error"]))
                choice = (d.get("choices") or [{}])[0]
                msg = d.get("message") or choice.get("delta") or {}
                think = msg.get("thinking") or msg.get("reasoning_content") or msg.get("reasoning")
                if think:
                    yield ("" if thinking else "<think>") + think
                    thinking = True
                if msg.get("content"):
                    yield ("</think>" if thinking else "") + msg["content"]
                    thinking = False
                for t in msg.get("tool_calls") or []:
                    yield {"call": t["function"]}
                prompt = d.get("prompt_eval_count") or (d.get("usage") or {}).get("prompt_tokens")
                if prompt:
                    yield {"prompt": prompt}
                wrote = d.get("eval_count") or (d.get("usage") or {}).get("completion_tokens")
                if wrote:
                    yield {"out": wrote, "secs": d.get("eval_duration", 0) / 1e9}
                if "length" in (d.get("done_reason"), choice.get("finish_reason")):
                    yield {"cut": True}

    def turn(self):
        """one model reply -> (text, call or None, cut, the native tool call if that is how it came)"""
        if self.cancel:
            raise KeyboardInterrupt
        budget = self.ctx - self.max_out - self.ctx // 16 - self.extra() / self.cpt
        if sum(weigh(m) / self.cpt + 4 for m in self.msgs) > budget and self.compact():  # summarise before anything is dropped
            self.say("" if self.col0 else "\n")
            self.say("  (older steps folded into a summary to make room)\n", "2")
        self.forgotten += fit(self.msgs, budget, self.cpt)
        sent = self.extra() + sum(weigh(m) for m in self.msgs)
        room = self.ctx - self.used() - self.ctx // 16  # a long file needs a long reply: up to half the window,
        self.limit = max(self.max_out, min(self.ctx // 2, room))  # no more, or a rambling model wastes minutes
        buf, cut, shown, raw, told = "", False, 0, None, False
        keep = ("role", "content", "tool_calls", "tool_name") + (() if self.url.rstrip("/").endswith("/v1") else ("images",))
        stream = self.chat([{k: m[k] for k in keep if k in m} for m in self.msgs])
        began, wrote, secs, asked, self.thread = time.time(), 0, 0.0, 0, threading.get_ident()
        self.status("thinking")
        try:
            for chunk in stream:
                if self.cancel:
                    raise KeyboardInterrupt
                if isinstance(chunk, dict):
                    if chunk.get("prompt"):
                        asked = chunk["prompt"]
                        self.cpt = max(1.0, min(self.cpt, sent / chunk["prompt"]))
                    cut = cut or bool(chunk.get("cut"))
                    raw = raw or chunk.get("call")
                    wrote, secs = chunk.get("out", wrote), chunk.get("secs", secs)
                    continue
                buf += chunk
                text = unthink(buf).lstrip()
                if text and not told:  # the reasoning is over: show it before the answer
                    told = self.thought(buf)
                peek = parse(text, final=True)  # a tool block is hidden while it streams, then drawn properly
                vis = text[:peek.start] if peek else text
                if not peek and vis[vis.rfind("\n") + 1:].lstrip().startswith("`"):
                    vis = vis[:vis.rfind("\n") + 1]  # this line may yet turn into a tool fence
                if len(vis) > shown:
                    self.say(vis[shown:])
                    shown = len(vis)
                elif peek:
                    self.status(f"{peek.tool} {peek.arg}".strip() + f" · {len(peek.body.splitlines())} lines")
                call = parse(text)
                if call and text[call.end:].strip():  # it kept talking past its tool call: stop it
                    break
        except Exception as e:
            if self.cancel:  # stop() hung up on the model: whatever that raised, this is an interruption
                raise KeyboardInterrupt from None
            if not isinstance(e, RuntimeError):
                raise
            log("error", error=str(e)[:500], model=self.model)
            if "invalid tool call arguments" not in str(e) and "unexpected end of JSON" not in str(e):
                raise
            cut = True  # the reply ran out of room in the middle of a tool call
        finally:
            getattr(stream, "close", lambda: None)()
            SOCKETS.pop(self.thread, None)
            self.status("")
        if self.cancel:
            raise KeyboardInterrupt
        self.took = time.time() - began
        self.rate = wrote / (secs or self.took or 1e-9) if wrote else self.rate
        out = wrote or int(len(buf) / self.cpt)  # what the server counted, else an estimate from the text
        used = {"replies": 1, "sent": asked or int(sent / self.cpt), "written": out, "secs": round(secs or self.took, 3)}
        self.count(reasoning=min(out, int((len(buf) - len(unthink(buf))) / self.cpt)), models={self.model: dict(used)}, **used)
        text = unthink(buf).lstrip()
        told = told or self.thought(buf)
        call = from_args(raw.get("name"), raw.get("arguments"), len(text), len(text)) if raw else None
        if not call:
            raw, call = None, parse(text, final=not cut)
        self.say((text[:call.start] if call else text)[shown:])
        self.say("" if self.col0 else "\n")
        return (text[:call.end] if call else text).strip(), call, cut, raw

    def stop(self):
        """stop the current turn right now: cancel it, kill a running command and hang up on the model, which
        otherwise would not notice until its next piece of output (a long tool call sends nothing until it is done)"""
        self.cancel = True
        kill_running()
        try:
            SOCKETS[self.thread].shutdown(socket.SHUT_RDWR)
        except (KeyError, OSError):
            pass

    def target(self, c):
        return c.body.strip() if c.tool == "shell" else c.arg if c.tool != "fetch" or "://" in c.arg else "https://" + c.arg

    def allowed(self, c):
        return any(rule.partition(" ")[0] == c.tool and fnmatch.fnmatchcase(self.target(c), rule.partition(" ")[2]) for rule in self.allow)

    def rule_for(self, c):
        """the allow rule "always" adds: this exact command or file, or for a fetch the whole site"""
        if c.tool == "fetch":
            u = urllib.parse.urlparse(self.target(c))
            return f"fetch {u.scheme}://{u.netloc}/*"
        return f"{c.tool} {glob.escape(self.target(c))}"

    def thought(self, buf):
        text = " ".join(re.findall(r"<think>(.*?)(?:</think>|$)", buf, re.S)).strip()
        if text and self.think:
            self.show_thought(text)
        return True

    def show_thought(self, text):
        lines = text.splitlines()
        self.say("" if self.col0 else "\n")
        self.say("".join(f"  ┊ {x[:160]}\n" for x in lines[:6]), "2")
        self.say(f"  ┊ … +{len(lines) - 6} lines\n" if len(lines) > 6 else "", "2")

    def do(self, c):
        free = c.tool == "read" or self.mode == "auto" or (self.mode == "edits" and c.tool in ("write", "edit")) or self.allowed(c)
        answer = free or self.ask(c)
        if answer == "always":  # never ask about this one again
            self.allow.append(self.rule_for(c))
            save_config(allow=self.allow)
        elif not answer:
            return "denied by the user" if sys.stdin.isatty() else "denied: no terminal to ask on (start with -y)"
        try:
            if c.tool == "shell":
                return shell(c.body, self.cap, self.timeout)
            if c.tool == "read":
                return read(c.arg, self.cap)
            if c.tool == "fetch":
                return fetch(c.arg, self.cap)
            result = (write if c.tool == "write" else edit)(c.arg, c.body)
            if not result.startswith("error") and c.arg not in self.touched:
                self.touched.append(c.arg)
            return result
        except Exception as e:
            return f"error: {e}"

    def bang(self, cmd):
        """run a command yourself (typed as !command): no model, no approval; its output goes where the model will see it"""
        self.show_call(Call("shell", "", cmd, 0, 0))
        out = shell(cmd, self.cap, self.timeout)
        log("tool", tool="!", target=cmd[:200], result=out[:300])
        self.show_result(out)
        self.reply(None, f"I ran this myself:\n$ {cmd}\n{out}", human=True)
        return out

    def reply(self, assistant, user, human=False):
        if assistant is not None:
            self.msgs.append({"role": "assistant", "content": assistant or "..."})
        if self.msgs[-1]["role"] == "user":  # an interrupted turn left a user message dangling
            self.msgs[-1]["content"] += "\n\n" + user
            self.msgs[-1]["human"] = self.msgs[-1].get("human") or human
        else:
            self.msgs.append({"role": "user", "content": user, "human": human})

    def plan(self, task):
        """ask the model, without tools, for a short numbered plan -> its steps, or [] when there is nothing to split"""
        self.bare = True
        try:
            text = unthink("".join(c for c in self.chat([{"role": "system", "content": PLAN}, {"role": "user", "content": task}])
                                   if isinstance(c, str)))
        finally:
            self.bare = False
        steps = [m.group(1).strip() for m in re.finditer(r"^\s*(?:\d+[.)]|[-*•])\s+(.+)$", text, re.M)]
        return steps[:6] if len(steps) > 1 else []

    def show_plan(self, steps, at):
        self.say("" if self.col0 else "\n")
        for n, step in enumerate(steps):
            self.say(f"  {'✓' if n < at else '▸' if n == at else '·'} {step}\n", "1" if n == at else "2")

    def run_plan(self, task, chips=None):
        """plan mode: get a list of steps first, then do them one message at a time"""
        steps = self.plan(task)
        if not steps:
            return self.run(task, chips)
        answer = ""
        for n, step in enumerate(steps):
            self.show_plan(steps, n)
            ask = f"Now do only step {n + 1} of {len(steps)}: {step}"
            answer = self.run(f"{task}\n\nWe will do this one step at a time. {ask}", chips) if n == 0 else \
                self.run(ask, human=False)
            if answer.startswith("stopped:"):
                return answer
        self.show_plan(steps, len(steps))
        return answer

    def run(self, task, chips=None, human=True):
        """answer one message; whatever it used is added to the lifetime tally when it ends, however it ends"""
        self.count(messages=int(human))
        try:
            return self._run(task, chips)
        finally:
            keep_usage(self.unsaved)
            self.unsaved = blank()

    def _run(self, task, chips=None):
        self.msgs[0]["content"] = self.system()  # refresh the file listing
        self.cancel = False
        log("message", text=task[:300], model=self.model, mode=self.mode, cwd=os.getcwd())
        text, images = attach(task, self.cap, chips)
        self.reply(None, text, human=True)
        if images:
            self.msgs[-1].setdefault("images", []).extend(images)
        last, seen, spins, empties, nudged, cuts, said, wrote, failed = None, set(), 0, 0, False, 0, [], None, None
        for _ in range(self.steps):
            text, call, cut, raw = self.turn()
            if call is None and cut:
                log("cut", limit=self.limit)
                cuts += 1
                if cuts > 2:
                    return ("stopped: the reply keeps running out of room. Ask for something shorter or in parts, "
                            f"or give it a bigger window: /config ctx {self.ctx * 2}")
                freed = self.compact()  # make what room there is to make
                if text and not parse(text, final=True):  # a plain reply: keep what it said and let it carry on
                    said.append(text)
                    self.reply(text, "Continue from exactly where you stopped. Do not repeat anything.")
                elif not freed:  # a half-written tool call, and no more room to give it
                    self.reply(text, "That was cut off: it is too long for one reply. Write a shorter version, "
                                     "or write the first part now and add the rest with edit.")
            elif call is None and not text:
                empties += 1
                if empties > 2:
                    return "stopped: the model keeps sending empty replies"
                self.reply(text, "Empty reply. " + NEXT)
            elif call is None and "```" in text and not nudged and last is None:  # showed code instead of acting
                nudged = True
                self.reply(text, "That code was only shown: it was not saved or run. "
                                 "Use write to save it to a file. If you are finished, just say so.")
            elif call is None and REFUSAL.search(text) and not nudged and last is None:  # gave up without looking
                nudged = True
                self.reply(text, "You do have access: your tools work on the files in this folder. "
                                 "Use them now instead of asking.")
            elif call is None:
                self.msgs.append({"role": "assistant", "content": text})
                return "".join(part + ("" if text[:1] in ".,;:!?)]" else " ") for part in said) + text
            else:
                key = call[:3]
                if key == last:
                    self.show_call(call)
                    result = "You just ran this exact call; its result is above."
                else:
                    if call.tool == "write" and not call.body.strip():  # tiny models show code, then "write" it
                        prior = next((m["content"] for m in reversed(self.msgs) if m["role"] == "assistant"), "")
                        shown = [b for lang, b in re.findall(r"```([\w+-]*)[^\n]*\n(.*?)```", prior + "\n" + text, re.S)
                                 if lang.lower() not in ALIAS]
                        call = call._replace(body=shown[-1] if shown else "")
                    self.show_call(call)
                    result = self.do(call)
                    bad = result.startswith("error") or re.search(r"\[(exit -?[1-9]\d*|timed out[^\]]*)\]$", result)
                    self.count(tools={call.tool: 1}, failed=int(bool(bad)))
                    log("tool", tool=call.tool, target=self.target(call)[:200], result=result[:300])
                # going in circles: the same call again, the same call and result as some time before, the same file
                # written whole twice running, or the same failure twice running. One warning, then it is stopped.
                again = (key == last or key + (result,) in seen or (call.tool == "write" and self.target(call) == wrote)
                         or (result.startswith("error") and result == failed))
                spins = spins + 1 if again else 0
                seen.add(key + (result,))
                last, wrote, failed = key, self.target(call) if call.tool == "write" else None, result
                if spins > 1:
                    self.show_result(result)
                    log("stopped", why="repeating", tool=call.tool, target=self.target(call)[:120])
                    return "stopped: the model keeps repeating itself"
                saved = call.tool in ("write", "edit") and result.startswith(("wrote", "edited"))
                extra = "\n\n" + SPIN if spins else ". Now do the next step of the task." if saved and raw else ""
                if raw:  # keep the model's own tool-call format in the history
                    self.msgs += [{"role": "assistant", "content": text, "tool_calls": [{"function": raw}]},
                                  {"role": "tool", "tool_name": call.tool, "content": result + extra}]
                else:
                    self.msgs.append({"role": "assistant", "content": text + ("" if text.endswith("```") else "\n```")})
                    self.reply(None, f"[{call.tool} result]\n{result}\n\n{SPIN if spins else NEXT}")
                self.show_result(result)
        return f"stopped: step limit reached ({self.steps} steps). Say continue to let it go on."


# ---------- terminal ----------

def answers(url):
    """is a model server answering at url?"""
    try:
        urllib.request.urlopen(url.rstrip("/") + "/api/version", timeout=2).close()
    except urllib.error.HTTPError:
        pass  # something answered, which is all we need to know
    except OSError:
        return False
    return True


def local(url):
    return urllib.parse.urlparse(url).hostname in ("localhost", "127.0.0.1", "::1") and not url.rstrip("/").endswith("/v1")


def stop_server(url):
    """stop the Ollama on this machine -> did it stay down? (its own app or a service may start it again)"""
    kills = [["taskkill", "/IM", "ollama app.exe", "/F"], ["taskkill", "/IM", "ollama.exe", "/F"]] if WINDOWS else \
        ([["pkill", "-x", "Ollama"]] if sys.platform == "darwin" else []) + [["pkill", "-f", "ollama serve"]]
    for argv in kills:
        try:
            subprocess.run(argv, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
    for _ in range(12):
        if not answers(url):
            return True
        time.sleep(0.25)
    return False


def serve(url, note=lambda s: None):
    """make sure a model server answers at url, starting a local Ollama if need be"""
    up = lambda: answers(url)
    u = urllib.parse.urlparse(url)
    if up():
        return True
    if not local(url) or not shutil.which("ollama"):
        return False
    note("starting ollama")
    subprocess.Popen(["ollama", "serve"], env={**os.environ, "OLLAMA_HOST": u.netloc}, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **own_group())
    for _ in range(60):  # it stays up after sprint exits, like the Ollama app would
        time.sleep(0.25)
        if up():
            return True
    return False


def installed(url, model):
    with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=5) as r:
        names = [m["name"] for m in json.load(r)["models"]]
    return model in names or model + ":latest" in names


def config_dir(env=os.environ, windows=WINDOWS):
    """where settings live: $XDG_CONFIG_HOME/sprint if that is set, else %APPDATA%\\sprint on Windows, else ~/.config/sprint"""
    return Path(env.get("XDG_CONFIG_HOME") or (windows and env.get("APPDATA")) or Path.home() / ".config") / "sprint"


CONFIG = config_dir() / "config.json"
SETTINGS = {"model": str, "url": str, "ctx": int, "steps": int, "mode": str, "think": bool, "effort": str,
            "sidebar": bool, "sidebar_w": int, "history": bool, "history_w": int, "motion": bool,
            "timeout": int, "theme": str, "peek": int, "allow": list, "plan": bool, "recent": list, "showthink": bool,
            "update_check": bool}
SHADE = {"system prompt": "blue", "tool definitions": "magenta", "your messages": "cyan", "model replies": "green",
         "tool results": "yellow", "reserved for the reply": "dim", "free": "dim"}
ANSI = {"": "", "dim": "2", "bold": "1", "blue": "34", "magenta": "35", "cyan": "36", "green": "32", "yellow": "33", "red": "31"}


def load_config():
    try:
        saved = json.loads(CONFIG.read_text(encoding="utf-8"))
        return {k: v for k, v in saved.items() if k in SETTINGS} if isinstance(saved, dict) else {}
    except (OSError, ValueError):
        return {}


def log_file():
    return CONFIG.with_name("sprint.log")


def log(kind, **data):
    """one line per event in sprint.log beside the settings, so there is something to look at when things go wrong"""
    try:
        path = log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 1_000_000:  # keep the recent third
            path.write_text(path.read_text(encoding="utf-8", errors="replace")[-300_000:], encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, **data}) + "\n")
    except OSError:
        pass


def transcript(agent):
    """the conversation as markdown"""
    out = ["# sprint conversation", "", f"{agent.model} · {time.strftime('%Y-%m-%d %H:%M')} · {os.getcwd()}", ""]
    for m in agent.msgs[1:]:
        if m["role"] == "user" and m.get("human"):
            out += ["## You", "", m["content"], ""]
        elif m["role"] == "assistant":
            out += ["## sprint", "", m["content"].strip(), ""] if m["content"].strip() else []
            for t in m.get("tool_calls", []):
                c = from_args(t["function"].get("name"), t["function"].get("arguments"), 0, 0)
                if c:
                    out += [f"**{c.tool}** `{c.arg or c.body.strip().splitlines()[0]}`"]
                    out += ["```", c.body.rstrip("\n"), "```"] if c.body.strip() and c.tool != "shell" else []
                    out += [""]
        else:  # what a tool gave back
            out += ["```", m["content"].strip(), "```", ""]
    return "\n".join(out)


def clipboard(text):
    for tool in (["clip"],) if WINDOWS else (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["clip.exe"]):
        if shutil.which(tool[0]):
            try:  # Windows' clip reads UTF-16; the others take text as it is
                subprocess.run(tool, input=text.encode("utf-16") if tool[0].startswith("clip") else text.encode(), timeout=5, check=True)
                return True
            except (OSError, subprocess.SubprocessError):
                pass
    return False


def clipboard_image():
    """the picture on the clipboard, saved as a file -> its path, or "" when there is none. A file copied in the
    file manager comes back as itself. A terminal cannot paste a picture, so sprint asks the system for it."""
    out = os.path.join(tempfile.gettempdir(), f"sprint-paste-{int(time.time() * 1000)}.png")
    if WINDOWS:
        quoted = out.replace("'", "''")  # PowerShell's way to put an apostrophe inside '...'
        tries = [["powershell", "-NoProfile", "-STA", "-Command",
                  "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; $i = [Windows.Forms.Clipboard]::GetImage(); "
                  f"if ($i) {{ $i.Save('{quoted}', [Drawing.Imaging.ImageFormat]::Png) }} "
                  "else { $f = [Windows.Forms.Clipboard]::GetFileDropList(); if ($f.Count) { $f[0] } }"]]
    elif sys.platform == "darwin":
        save = ['-e', f'set f to open for access POSIX file "{out}" with write permission', "-e", "write d to f", "-e", "close access f"]
        tries = [["osascript", "-e", "POSIX path of (the clipboard as «class furl»)"],
                 ["osascript", "-e", "set d to the clipboard as «class PNGf»"] + save,
                 ["osascript", "-e", "set d to the clipboard as «class TIFF»"] + save]
    else:
        tries = [["wl-paste", "--type", "image/png"], ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"]]
    for argv in tries:
        if not shutil.which(argv[0]):
            continue
        try:
            got = subprocess.run(argv, capture_output=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        named = got.decode(errors="replace").strip()
        if got.startswith(b"\x89PNG"):  # the tool printed the picture itself
            Path(out).write_bytes(got)
        if os.path.isfile(out) and os.path.getsize(out):
            if "TIFF" in " ".join(argv) and shutil.which("sips"):  # macOS kept it as TIFF: make it the PNG its name says
                subprocess.run(["sips", "-s", "format", "png", out, "--out", out], capture_output=True, timeout=10)
            return out
        if os.path.isfile(out):
            os.remove(out)  # opened but nothing written
        if named and "\n" not in named and os.path.isfile(named):
            return named
    return ""


def prompt_file():
    return CONFIG.with_name("system.md")  # your own system prompt lives beside the settings, as plain text


def load_prompt():
    try:
        return prompt_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_prompt(text):
    try:
        if text.strip():
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            prompt_file().write_text(text.strip() + "\n", encoding="utf-8")
        elif prompt_file().exists():
            prompt_file().unlink()
    except OSError:
        pass


def save_config(**changes):
    try:
        merged = {**load_config(), **changes}
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass  # settings just don't stick


def context_cells(agent, n=200):
    """the context window as n cells: what is used (coloured by kind), free space, then the reply's reserve"""
    parts = agent.breakdown()
    total = max(agent.ctx, sum(k for _, k in parts))
    used = [c for what, k in parts[:5] for c in [("█", SHADE[what])] * (max(1, round(k * n / total)) if k else 0)]
    hold = [("▒", "dim")] * max(1, round(agent.max_out * n / total))
    used = used[:n - len(hold)]
    return used + [("·", "dim")] * (n - len(used) - len(hold)) + hold


def blank():
    """an empty tally of use: what /usage adds up for a session, a day, or a lifetime"""
    return {"messages": 0, "replies": 0, "sent": 0, "written": 0, "reasoning": 0, "secs": 0.0, "tools": {}, "failed": 0, "models": {}}


def add(total, more):
    """add one tally into another, tool by tool and model by model"""
    for k, v in more.items():
        if isinstance(v, dict):
            add(total.setdefault(k, {}), v)
        else:
            total[k] = total.get(k, 0) + v
    return total


def usage_file():
    return CONFIG.with_name("usage.json")  # beside the settings: one tally per day, as plain JSON


def load_usage():
    try:
        days = json.loads(usage_file().read_text(encoding="utf-8"))["days"]
        return {day: add(blank(), t) for day, t in days.items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}  # none yet, or not readable: start again rather than fail


def keep_usage(more):
    """add a tally to today's line of the lifetime file"""
    if not (more["messages"] or more["replies"]):
        return
    try:
        days = load_usage()
        add(days.setdefault(time.strftime("%Y-%m-%d"), blank()), more)
        usage_file().parent.mkdir(parents=True, exist_ok=True)
        usage_file().write_text(json.dumps({"days": dict(sorted(days.items())[-730:])}, indent=1) + "\n", encoding="utf-8")
    except OSError:
        pass  # the count just does not stick


def human(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1000 else str(int(n))


def clock(secs):
    secs = int(secs)
    return f"{secs // 3600}h {secs % 3600 // 60:02d}m" if secs >= 3600 else f"{secs // 60}m {secs % 60:02d}s" if secs >= 60 else f"{secs}s"


def bar(n, top, width=20):
    return "█" * max(1 if n else 0, round(width * n / top)) if top else ""


def usage_rows(agent):
    """/usage: this session, what is in the window now, the last fourteen days, each model, and everything so far"""
    s, days = agent.tally, load_usage()
    add(days.setdefault(time.strftime("%Y-%m-%d"), blank()), agent.unsaved)  # the turn in progress is not on disk yet
    life = blank()
    for t in days.values():
        add(life, t)
    head = lambda title, more="": [(title, "bold"), (more, "dim")]
    line = lambda *spans: [("  ", "")] + list(spans)
    name = lambda model: re.sub(r"^hf\.co/[^/]+/|-GGUF", "", model)

    def about(t):  # counts, the three kinds of token as bars, time, tools
        calls, top = sum(t["tools"].values()), max(t["sent"], t["written"], 1)
        kinds = (("sent", "cyan", "  the conversation, every reply"), ("written", "magenta", ""), ("reasoning", "yellow", "  part of written"))
        return [line((f"messages {t['messages']} · replies {t['replies']} · tool calls {calls}"
                      + (f" ({t['failed']} failed)" if t["failed"] else ""), ""))] + \
            [line((f"{kind:<10}", ""), (f"{human(t[kind]):>7} ", "bold"), (bar(t[kind], top), colour), (note, "dim"))
             for kind, colour, note in kinds] + \
            [line((f"generating {clock(t['secs'])}" + (f" at {t['written'] / t['secs']:.0f} tok/s" if t["secs"] else ""), "dim")),
             line((" · ".join(f"{k} {v}" for k, v in sorted(t["tools"].items(), key=lambda kv: -kv[1])) or "no tool calls", "dim"))]

    rows = [head("Session", f"  started {time.strftime('%H:%M', time.localtime(agent.started))}, "
                            f"{clock(time.time() - agent.started)} ago · {name(agent.model)[:30]}")]
    rows += about(s) if s["messages"] or s["replies"] else [line(("nothing yet this session", "dim"))]

    parts = agent.breakdown()
    top = max(n for _, n in parts) or 1
    rows += [[], head("In the window now", f"  {human(agent.used())} of {human(agent.ctx)} tokens")]
    rows += [line((f"{what:<24}", ""), (f"{human(n):>6} ", "bold"), (bar(n, top, 24), SHADE.get(what, "dim"))) for what, n in parts]

    last = [time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400 * back)) for back in range(13, -1, -1)]
    wrote = [days[d]["written"] if d in days else 0 for d in last]
    peak = max(wrote)
    spark = "".join("▁▂▃▄▅▆▇█"[min(7, n * 8 // (peak + 1))] if n else " " for n in wrote)
    rows += [[], head("Last 14 days", "  tokens written each day"),
             line((spark, "magenta"), (f"  most: {human(peak)} on {last[wrote.index(peak)][5:]}" if peak else "  nothing written", "dim"))]
    rows += [line((d[5:] + "  ", "dim"), (f"↑ {human(days[d]['sent']):>6}  ↓ {human(days[d]['written']):>6}  ", ""),
                  (f"{days[d]['replies']:>4} replies  ", "dim"), (bar(days[d]["written"], peak, 16), "magenta"))
             for d in last[-7:] if d in days and days[d]["replies"]]

    rows += [[], head("Models", "  every session")]
    for model, m in sorted(life["models"].items(), key=lambda kv: -kv[1].get("written", 0))[:8]:
        speed = f" · {m['written'] / m['secs']:.0f} tok/s" if m.get("secs") else ""
        rows.append(line((f"{name(model)[:24]:<24} ", ""), (f"↑ {human(m.get('sent', 0)):>6}  ↓ {human(m.get('written', 0)):>6}  ", "bold"),
                         (f"{m.get('replies', 0)} replies{speed}", "dim")))
    if not life["models"]:
        rows.append(line(("none yet", "dim")))

    return rows + [[], head("Lifetime", f"  since {min(days)}, {len(days)} day{'s' * (len(days) != 1)} with use")] + about(life)


def context_rows(agent):
    """the context window as a 20x10 grid of cells, with what each colour is beside it"""
    parts, cells = agent.breakdown(), context_cells(agent)
    spent = sum(n for _, n in parts[:5])
    side = [[(re.sub(r"^hf\.co/[^/]+/|-GGUF", "", agent.model), "bold")],
            [(f"{spent:,} of {agent.ctx:,} tokens in use ({spent * 100 // agent.ctx}%)", "")], []]
    side += [[("□ " if what == "free" else "■ ", SHADE[what]), (f"{what:<23}{n:>6,} {n * 100 / agent.ctx:5.1f}%", "")] for what, n in parts]
    rows = [[("  ", "")] + [(glyph, colour) for glyph, colour in cells[r * 20:r * 20 + 20]] + [("   ", "")] + side[r] for r in range(10)]
    note = f"{agent.forgotten} older turns were dropped to fit · " if agent.forgotten else ""
    return rows + [[]] + [[(f"  {note}counted at {agent.cpt:.1f} characters per token", "dim")]]


def command(agent, line):
    """slash commands both front ends share -> rows of (text, colour) to show, or None if it is not one of them"""
    cmd, _, arg = line.partition(" ")
    arg = arg.strip()
    note = lambda *lines: [[(x, "dim")] for x in lines]
    if cmd == "/permissions" and arg.split(" ")[0] in ("allow", "forget"):
        verb, _, rest = arg.partition(" ")
        if verb == "allow" and rest.partition(" ")[0] in ("shell", "write", "edit", "fetch") and rest.partition(" ")[2].strip():
            agent.allow.append(rest.strip())
        elif verb == "forget" and rest.strip().isdigit() and 0 < int(rest) <= len(agent.allow):
            del agent.allow[int(rest) - 1]
        else:
            return note("cannot do that: /permissions allow shell git status*   or   /permissions forget 1")
        save_config(allow=agent.allow)
        return note("always allowed: " + (", ".join(agent.allow) or "nothing"))
    if cmd == "/plan":
        on = not load_config().get("plan", False)
        save_config(plan=on)
        return note("plan mode on: each message is split into steps first" if on else "plan mode off")
    if cmd == "/race":
        rival, _, task = arg.partition(" ")
        if not task.strip():
            return note("race two models on one message: /race OTHER-MODEL your message")
        rows = []
        for r in race(agent, rival, task.strip()):
            rows += [[(r["model"], "bold"), (f"   {r['seconds']:.1f}s · {r['rate']:.0f} tok/s · {r['steps']} tool calls", "dim")]]
            rows += [[("  " + line, "")] for line in r["answer"].split("\n")] + [[]]
        return rows
    if cmd in ("/mode", "/permissions"):
        if arg in MODES:
            agent.mode = arg
        elif cmd == "/mode":
            agent.cycle()
        if cmd == "/mode" or arg:
            return note(f"{agent.mode}: {ABOUT[agent.mode]}")
        free = {"read": True, "write": agent.mode != "ask", "edit": agent.mode != "ask",
                "shell": agent.mode == "auto", "fetch": agent.mode == "auto"}
        return note(f"mode {agent.mode}: {ABOUT[agent.mode]}") + \
            [[(f"  {tool:<7}", ""), ("runs without asking", "green") if ok else ("asks first", "yellow")] for tool, ok in free.items()] + \
            note("always allowed:" if agent.allow else "nothing is always allowed yet") + \
            [[(f"  {n}  ", "dim"), (rule, "")] for n, rule in enumerate(agent.allow, 1)] + \
            note("change with /permissions ask | edits | auto · allow TOOL PATTERN · forget N")
    if cmd == "/think":
        agent.think = not agent.think
        save_config(think=agent.think)
        return note("thinking on: the model reasons first, out of each reply's token budget" if agent.think else "thinking off")
    if cmd == "/effort":
        if arg in EFFORT:
            agent.set_effort(arg)
            save_config(effort=arg)
        return note(f"effort {agent.effort}: replies up to {agent.max_out:,} tokens, {agent.steps} steps per message",
                    "levels: low · medium · high")
    if cmd == "/model":
        if arg:
            agent.model, agent.tools = arg, not agent.url.rstrip("/").endswith("/v1")
            save_config(model=arg)
            warm(agent.url, arg)
            return note(f"model: {arg}")
        try:
            with urllib.request.urlopen(agent.url.rstrip("/") + "/api/tags", timeout=5) as r:
                names = [m["name"] for m in json.load(r)["models"]]
        except (OSError, ValueError, KeyError):
            return note("cannot list models: is Ollama running?")
        return note("installed models (switch with /model NAME):", *("  " + n + ("   ← current" if n == agent.model else "") for n in names))
    if cmd == "/compact":
        before, folded = agent.used(), agent.compact()
        return note(f"folded {folded} messages into a summary: {before:,} → {agent.used():,} tokens" if folded else "nothing to compact yet")
    if cmd == "/context":
        return context_rows(agent)
    if cmd == "/usage":
        return usage_rows(agent)
    if cmd == "/export":
        path = os.path.expanduser(arg) if arg else time.strftime("sprint-%Y%m%d-%H%M%S.md")
        try:
            Path(path).write_text(transcript(agent) + "\n", encoding="utf-8")
        except OSError as e:
            return note(f"cannot save there: {e}")
        return note(f"conversation saved to {path}")
    if cmd == "/copy":
        answers = [m["content"] for m in agent.msgs if m["role"] == "assistant" and m["content"].strip()]
        wrote = [c.body for m in agent.msgs for t in m.get("tool_calls", [])
                 for c in [from_args(t["function"].get("name"), t["function"].get("arguments"), 0, 0)] if c and c.tool == "write"]
        blocks = re.findall(r"```[^\n]*\n(.*?)```", "\n".join(answers), re.S)
        text, what = (transcript(agent), "the whole conversation") if arg == "all" and len(agent.msgs) > 1 else \
            ((blocks or wrote or [""])[-1], "the last code block") if arg == "code" else ((answers or [""])[-1], "the last answer")
        if not text.strip():
            return note("nothing to copy yet")
        return note(f"copied {what} ({len(text):,} characters)" if clipboard(text) else
                    "cannot copy: no clipboard tool found (pbcopy, wl-copy or xclip)")
    if cmd == "/log":
        try:
            lines = [json.loads(x) for x in log_file().read_text(encoding="utf-8", errors="replace").splitlines()[-15:]]
        except (OSError, ValueError):
            lines = []
        rows = [[(x.pop("t", "")[11:] + "  ", "dim"), (f"{x.pop('kind', ''):<8}", "bold"),
                 ("  ".join(str(v).replace("\n", " ")[:90] for v in x.values()), "red" if "error" in x else "")] for x in lines]
        return (rows or note("nothing logged yet")) + note("full log: " + str(log_file()).replace(str(Path.home()), "~", 1))
    if cmd in ("/directory", "/dir", "/cd"):
        if arg:
            path = os.path.abspath(os.path.expanduser(arg))
            if not os.path.isdir(path):
                return note(f"cannot go there: {arg} is not a folder")
            os.chdir(path)  # the file listing in the system prompt follows at the next message
            save_config(recent=([path] + [p for p in load_config().get("recent", []) if p != path])[:6])
        return note("working in " + os.getcwd().replace(str(Path.home()), "~", 1))
    if cmd in ("/systemprompt", "/system"):
        if arg == "reset":
            agent.custom = ""
            save_prompt("")
            return note("system prompt back to the default")
        text = agent.custom or (SYSTEM_TOOLS if agent.tools else SYSTEM.split("\n", 1)[0])
        where = str(prompt_file()).replace(str(Path.home()), "~", 1)
        return [[(line, "")] for line in text.split("\n")] + \
            note(f"{'yours' if agent.custom else 'the default'} · to change it edit {where} · /systemprompt reset undoes it")
    if cmd == "/config":
        key, _, value = arg.partition(" ")
        value = value.strip()
        if key in SETTINGS and value and SETTINGS[key] is not list:
            kind = SETTINGS[key]
            if kind is int and not value.isdigit():
                return note(f"{key} needs a number")
            new = int(value) if kind is int else value.lower() in ("on", "true", "yes", "1") if kind is bool else value
            if key == "mode" and new not in MODES or key == "effort" and new not in EFFORT or key == "ctx" and new < 1024:
                return note(f"{key} cannot be {value}")
            if key == "effort":
                agent.set_effort(new)
            elif key in ("model", "url", "ctx", "steps", "mode", "think", "timeout"):
                setattr(agent, key, new)
            agent.tools = agent.tools if key not in ("url", "model") else not agent.url.rstrip("/").endswith("/v1")
            save_config(**{key: new})
        kept = load_config()
        now = {"model": agent.model, "url": agent.url, "ctx": agent.ctx, "steps": agent.steps, "mode": agent.mode,
               "think": agent.think, "effort": agent.effort, "sidebar": kept.get("sidebar", True),
               "sidebar_w": kept.get("sidebar_w", 32), "history": kept.get("history", True), "history_w": kept.get("history_w", 26),
               "motion": kept.get("motion", True), "timeout": agent.timeout, "theme": kept.get("theme", "sprint"),
               "peek": kept.get("peek", 6), "plan": kept.get("plan", False)}
        return [[(f"  {k:<10}", "dim"), (str(v).lower() if isinstance(v, bool) else str(v), "")] for k, v in now.items()] + \
            note(f"change with /config KEY VALUE · kept in {str(CONFIG).replace(str(Path.home()), '~', 1)}")
    return None


def warm(url, model):
    """ask Ollama, in the background, to load the model now, so the first reply does not have to wait for it"""
    if url.rstrip("/").endswith("/v1"):
        return None

    def load():
        try:
            req = urllib.request.Request(url.rstrip("/") + "/api/generate", json.dumps({"model": model, "keep_alive": "30m"}).encode(),
                                         {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=120).read()
        except (OSError, ValueError):
            pass

    t = threading.Thread(target=load, daemon=True)
    t.start()
    return t


def race(agent, rival, task, tell=lambda model: None):
    """the same message to two models, one after the other. Read-only: they may read files but change nothing.
    -> [{"model", "answer", "seconds", "rate", "steps"}, ...], the agent's own model first"""
    results = []
    for model in (agent.model, rival):
        r = Agent(model, agent.url, agent.ctx, agent.steps, "ask", agent.key, vars(agent).get("chat"), lambda call: False,
                  io.StringIO(), agent.think, agent.effort)
        r.custom = agent.custom
        tell(model)
        began = time.time()
        try:
            answer = r.run(task)
        except (RuntimeError, OSError) as e:
            answer = f"error: {e}"
        calls = sum(1 for m in r.msgs if m["role"] == "assistant" and ("tool_calls" in m or parse(m["content"], final=True)))
        results.append({"model": model, "answer": answer, "seconds": time.time() - began, "rate": r.rate, "steps": calls})
    return results


TINT = {"ask": "36", "edits": "33", "auto": "35"}
ABOUT = {"ask": "asks before every action", "edits": "file edits run without asking",
         "auto": "everything runs without asking"}
# what plain mode understands, a thing to a line: (what you type, what it does)
HELP = [("@file", "attach a file"), ("!command", "run the command yourself, without the model"),
        ("/model [NAME]", "switch model"), ("/mode", "ask, edits or auto"), ("/permissions", "what runs without asking"),
        ("/think", "let the model reason first, on or off"), ("/effort [LEVEL]", "low, medium or high"),
        ("/context", "what is filling the context window"), ("/usage", "tokens, time and tool calls"),
        ("/compact", "fold the older conversation into a short summary"), ("/copy [code|all]", "copy the last answer"),
        ("/export [FILE]", "save the conversation as markdown"), ("/log", "what happened recently"),
        ("/directory [PATH]", "change the folder sprint works in"), ("/plan", "split each message into steps first, on or off"),
        ("/race MODEL MESSAGE", "one message, two models, side by side"),
        ("/systemprompt", "edit what the model is told at the start"), ("/config", "see or change saved settings"),
        ("/clear", "clear the conversation and start fresh"), ("/bye", "quit")]


def help_rows():
    """plain mode's help as rows of (text, colour): the name, what it takes greyed, then what it does, in one column"""
    wide = max(len(what) for what, _ in HELP) + 2
    return [[("  " + what.split(" ")[0], "cyan"), (what[len(what.split(" ")[0]):].ljust(wide - len(what.split(" ")[0])), "dim"), (about, "")]
            for what, about in HELP]


DEFAULT_MODEL = "hf.co/unsloth/Qwen3.5-0.8B-GGUF:Q4_K_M"
VERSION = "0.4.8"  # the same as in pyproject.toml
REPO = "Vasiniks/sprint"
# sprint help: the words sprint takes on the command line, in sections
USAGE = [("Start", [("sprint", "open sprint in this folder"), ('sprint "fix the typo"', "open it with a first message")]),
         ("Local server", [("sprint serve", "start the local Ollama server if it is not running"), ("sprint serve stop", "stop it"),
                           ("sprint serve status", "say whether it is running")]),
         ("Settings and updates", [("sprint config", "open the settings file in your editor"), ("sprint update", "get the newest sprint"),
                                   ("sprint help", "this list (sprint --help lists the options)"),
                                   ("sprint --version", "say which version this is")])]


def usage(colour=False):
    """what `sprint help` prints: headings bold and commands in colour on a terminal, the same text bare anywhere else"""
    paint = (lambda s, code: f"\033[{code}m{s}\033[0m") if colour else (lambda s, code: s)
    wide = max(len(cmd) for _, rows in USAGE for cmd, _ in rows) + 4
    out = [paint("sprint " + VERSION, "1") + paint("  a terminal coding agent for small local models", "2")]
    for title, rows in USAGE:
        out += ["", paint(title, "1")] + ["  " + paint(cmd, "36") + " " * (wide - len(cmd)) + about for cmd, about in rows]
    return "\n".join(out + ["", "Inside sprint, " + paint("/help", "36") + " lists the keys and commands."])


def open_file(path):
    """open a file for editing: in $VISUAL or $EDITOR if set, else in whatever the system opens text with"""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    try:
        if editor:
            return subprocess.run(shlex.split(editor) + [path]).returncode == 0
        if WINDOWS:
            os.startfile(path)
            return True
        return subprocess.run(["open", "-t", path] if sys.platform == "darwin" else ["xdg-open", path], capture_output=True).returncode == 0
    except (OSError, ValueError):
        return False


def update(home=None):
    """get the newest sprint: pull if this copy is a git checkout, else reinstall with pip -> exit code"""
    home = Path(home or Path(__file__).resolve().parent)
    argv = ["git", "-C", str(home), "pull", "--ff-only"] if (home / ".git").exists() else \
        [sys.executable, "-m", "pip", "install", "--upgrade", f"git+https://github.com/{REPO}.git"]
    print("  " + " ".join(argv))
    try:
        ok = subprocess.run(argv).returncode == 0
    except OSError:
        ok = False
    print("  done: start sprint again" if ok else "  the update did not go through; the command above is what was tried")
    try:
        update_file().unlink()  # look again for what is newest next time
    except OSError:
        pass
    return 0 if ok else 1


def update_file():
    return CONFIG.with_name("update.json")  # the newest version seen, and when sprint last looked


def newer(a, b):
    try:
        return tuple(int(x) for x in a.split(".")) > tuple(int(x) for x in b.split("."))
    except ValueError:
        return False


def fetch_latest():
    with urllib.request.urlopen(f"https://raw.githubusercontent.com/{REPO}/main/pyproject.toml", timeout=3) as r:
        return re.search(r'^version\s*=\s*"([^"]+)"', r.read().decode(), re.M).group(1)


def check_update():
    """the newest published version if it is newer than this one, else "". Asks GitHub at most once a day, sends
    nothing but the request, and /config update_check off stops it"""
    if load_config().get("update_check") is False:
        return ""
    try:
        kept = json.loads(update_file().read_text(encoding="utf-8"))
        latest, checked = str(kept.get("latest", "")), float(kept.get("checked", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        latest, checked = "", 0.0
    if abs(time.time() - checked) > 86400:
        try:
            latest = fetch_latest()
        except Exception:
            pass  # offline, or GitHub said no: keep what was known and try again tomorrow
        try:
            update_file().parent.mkdir(parents=True, exist_ok=True)
            update_file().write_text(json.dumps({"latest": latest, "checked": time.time()}) + "\n", encoding="utf-8")
        except OSError:
            pass
    return latest if newer(latest, VERSION) else ""


def cli(argv):
    """the words sprint takes besides a message: help, config, serve, update -> exit code, or None to carry on"""
    word, rest = (argv[0], argv[1:]) if argv else ("", [])
    if word in ("--version", "version") and not rest:
        print("sprint " + VERSION)
    elif word == "help" and not rest:
        colour = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        if colour and WINDOWS:
            os.system("")  # switches on colour codes in the classic Windows console
        print(usage(colour))
    elif word == "update" and not rest:
        return update()
    elif word == "config" and not rest:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        if not CONFIG.exists():
            CONFIG.write_text("{}\n", encoding="utf-8")
        print(f"  {CONFIG}")
        if not open_file(str(CONFIG)):
            print("  could not open an editor: open that file in any editor")
    elif word == "serve" and rest in ([], ["stop"], ["status"]):
        url = os.environ.get("SPRINT_URL") or load_config().get("url", "http://localhost:11434")
        if rest == ["status"]:
            up = answers(url)
            print(f"  a model server is running at {url}" if up else f"  not running: nothing answers at {url}")
            return 0 if up else 1
        if rest == ["stop"]:
            if not local(url):
                print(f"  {url} is not on this machine: stop it there")
                return 1
            down = stop_server(url)
            print("  ollama stopped" if down else f"  still answering at {url}: something starts it again (quit the Ollama app, or stop its service)")
            return 0 if down else 1
        up = serve(url, lambda s: print("  " + s) if s else None)
        print(f"  a model server is running at {url}" if up else f"  could not start one at {url}: is Ollama installed?")
        return 0 if up else 1
    else:
        return None
    return 0


def main():
    done = cli(sys.argv[1:])
    if done is not None:
        return done
    ap = argparse.ArgumentParser(prog="sprint", description=__doc__, epilog="sprint help lists the other commands: config, serve, update")
    ap.add_argument("task", nargs="*", help="optional first message")
    ap.add_argument("-m", "--model", help="model name (default: the one used last, else Qwen 3.5 0.8B Q4_K_M)")
    ap.add_argument("-u", "--url", help="Ollama URL, or any OpenAI-compatible URL ending in /v1")
    ap.add_argument("-c", "--ctx", type=int, help="context window in tokens (default 8192)")
    ap.add_argument("-s", "--steps", type=int, help="max tool calls per message (default 30)")
    ap.add_argument("--mode", choices=MODES,
                    help="ask: confirm every action (default) · edits: accept file edits · auto: run everything")
    ap.add_argument("-y", "--yes", action="store_true", help="same as --mode auto")
    ap.add_argument("--plain", action="store_true", help="line-by-line session instead of the full screen")
    a = ap.parse_args()
    if WINDOWS:
        os.system("")  # switches on colour codes in the classic Windows console
    saved = load_config()  # flags beat the environment, which beats saved settings
    a.model = a.model or os.environ.get("SPRINT_MODEL") or saved.get("model", DEFAULT_MODEL)
    a.url = a.url or os.environ.get("SPRINT_URL") or saved.get("url", "http://localhost:11434")
    effort = saved.get("effort", "medium") if saved.get("effort") in EFFORT else "medium"
    agent = Agent(a.model, a.url, a.ctx or saved.get("ctx", 8192), a.steps or saved.get("steps", EFFORT[effort][1]),
                  "auto" if a.yes else a.mode or saved.get("mode", "ask"), os.environ.get("SPRINT_KEY", ""),
                  think=bool(saved.get("think")), effort=effort)
    agent.custom, agent.timeout, agent.allow = load_prompt(), saved.get("timeout", 60), list(saved.get("allow", []))
    warm(a.url, a.model)
    try:
        ready = serve(a.url, agent.status)
        agent.status("")
        if ready and not a.url.rstrip("/").endswith("/v1") and not installed(a.url, a.model) and sys.stdin.isatty():
            if input(f"  {a.model} is not installed - pull it now? [Y/n] ").strip().lower()[:1] in ("", "y"):
                subprocess.run(["ollama", "pull", a.model],
                               env={**os.environ, "OLLAMA_HOST": urllib.parse.urlparse(a.url).netloc})
    except (OSError, ValueError, KeyboardInterrupt):
        pass  # the first message will report whatever is wrong
    if sys.stdin.isatty() and sys.stdout.isatty() and not a.plain:
        try:
            import sprint_tui
            return sprint_tui.run(agent, " ".join(a.task))
        except ImportError:  # no curses here: fall through to the line-by-line session
            if WINDOWS:
                print("for the full screen on Windows: pip install windows-curses")
    say, tty, rl, hotkey = agent.say, sys.stdout.isatty(), None, False
    try:
        import readline as rl
        if tty and "libedit" not in (rl.__doc__ or ""):
            rl.parse_and_bind(r'"\e[Z": "\C-a/~ \C-m"')  # shift+tab submits the line as "/~ <draft>"
            hotkey = True
    except ImportError:
        pass
    say("◆ sprint", "1;35")
    say(f"  {agent.model} · {os.getcwd().replace(str(Path.home()), '~', 1)}\n", "2")
    say(f"  {'shift+tab' if hotkey else '/mode'} switches ask → edits → auto · /help for more\n", "2")
    line, draft, gap = " ".join(a.task), "", "\n"
    while True:
        if not line:
            on, off = (f"\033[1;{TINT[agent.mode]}m", "\033[0m") if tty else ("", "")
            if on and rl:  # tell readline the colour codes take no room
                on, off = f"\001{on}\002", f"\001{off}\002"
            if rl:
                rl.set_startup_hook((lambda d=draft: rl.insert_text(d)) if draft else None)
            sys.stdout.write(gap)
            try:
                line = input(f"{on}{agent.mode} ❯{off} ").strip()
            except (EOFError, KeyboardInterrupt):
                return print()
            finally:
                if rl:
                    rl.set_startup_hook(None)  # or the draft would be typed into the next prompt too
            draft, gap = "", "\n"
        cmd, _, arg = line.partition(" ")
        line = ""
        try:
            if cmd == "/~":  # shift+tab: next mode, prompt redrawn in place, typed text kept
                agent.cycle()
                draft, gap = arg, "\033[A\033[2K\r" if tty else ""
                rl.remove_history_item(rl.get_current_history_length() - 1)
            elif cmd in ("/bye", "/quit", "/exit"):
                return
            elif cmd == "/clear":
                agent.reset()
                say("  context cleared\n", "2")
            elif (rows := command(agent, cmd + _ + arg)) is not None:
                for row in rows:
                    for text, colour in row:
                        say(text, ANSI[colour])
                    say("\n")
            elif cmd.startswith("/"):
                for row in help_rows():
                    for text, colour in row:
                        say(text, ANSI[colour])
                    say("\n")
            elif cmd.startswith("!") and (cmd + _ + arg)[1:].strip():
                agent.bang((cmd + _ + arg)[1:].strip())
            elif cmd:
                before, began = dict(agent.tally), time.time()
                stop = (agent.run_plan if load_config().get("plan") else agent.run)(cmd + _ + arg)
                say(f"  {stop}\n" if stop.startswith("stopped:") else "", "33")
                say(f"  {agent.receipt(before, began)}\n", "2")
                n = round(min(agent.used() / agent.ctx, 1) * 10)
                speed = f" · {agent.rate:.0f} tok/s" if agent.rate else ""
                say(f"  {'━' * n}{'─' * (10 - n)} {agent.used() * 100 // agent.ctx}% of {agent.ctx} ctx{speed}\n", "2")
        except KeyboardInterrupt:
            say("\n  interrupted\n", "33")
        except (RuntimeError, OSError) as e:
            say(f"  {e}\n", "31")


if __name__ == "__main__":
    main()
