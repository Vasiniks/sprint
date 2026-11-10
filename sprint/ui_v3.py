"""
V3 UI - Claude/OpenCode style, fixed:
- no self.log / self.task collisions (App has those properties)
- uses rlog, agent_task, model_name, status_w etc
- pilot tested
"""
import pathlib
import queue
import threading
import time
from dataclasses import dataclass, field

from .harness import MAX_CTX_CHARS, extract_all_json, prune_history
from .prompt import SYSTEM
from .state import kill_session, list_sessions, register_session, unregister_session
from .tools import dispatch


@dataclass
class Event:
    type: str
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class TuiSprint:
    def __init__(self, llm_fn, max_steps=25, event_cb=None, model="tiny", task=""):
        self.llm_fn = llm_fn
        self.max_steps = max_steps
        self.on_event = event_cb or (lambda e: None)
        self.model = model
        self.task = task
        self.msgs = []
        self.sid = None

    def run(self, task: str) -> str:
        self.task = task
        self.sid = register_session(task, self.model)
        self.on_event(Event("session", {"id": self.sid}))
        self.msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"TASK:{task}\nJSON only."}]
        self.on_event(Event("task", {"task": task}))
        try:
            for step in range(self.max_steps):
                pruned = prune_history(self.msgs)
                ctx = sum(len(m.get("content", "")) for m in pruned)
                self.on_event(Event("step", {"step": step, "ctx": ctx, "max": MAX_CTX_CHARS, "msgs": len(self.msgs), "sid": self.sid}))
                self.on_event(Event("llm_start", {"step": step}))
                try:
                    raw = self.llm_fn(pruned)
                except Exception as e:
                    self.on_event(Event("error", {"msg": str(e)}))
                    self.msgs.append({"role": "user", "content": f"ERR {e}"})
                    continue
                self.on_event(Event("llm_done", {"raw": raw, "step": step}))
                actions = extract_all_json(raw)
                if not actions:
                    self.msgs.append({"role": "assistant", "content": raw})
                    self.msgs.append({"role": "user", "content": 'ERR need JSON {"tool":"shell","args":{"cmd":"ls"}}'})
                    self.on_event(Event("error", {"msg": "bad JSON", "raw": raw[:120]}))
                    continue
                for act in actions[:2]:
                    tool = (act.get("tool") or "").strip()
                    args = act.get("args", {})
                    if not isinstance(args, dict):
                        continue
                    if tool == "done":
                        res = args.get("result", "done")
                        self.on_event(Event("done", {"result": res}))
                        return res
                    self.msgs.append({"role": "assistant", "content": str(act)})
                    self.on_event(Event("tool_start", {"tool": tool, "args": args, "step": step}))
                    out = dispatch(tool, args)
                    self.msgs.append({"role": "user", "content": f"OUT:{out}"})
                    self.on_event(Event("tool_done", {"tool": tool, "args": args, "out": out, "step": step}))
        finally:
            if self.sid:
                unregister_session(self.sid)
            self.on_event(Event("final", {"result": "done"}))
        return "max steps"


WELCOME = """◉ sprint
tiny 8K agent — 89tok prompt — 5 tools — Ollama-first

COMMANDS
  sprint              welcome dashboard
  sprint help         help
  sprint list [path]  ls files
  sprint ps           running agents
  sprint kill <id>    stop agent
  sprint init         scaffold TASK.md
  sprint "task"       run agent

TUI SLASH
  /list [path]  /ps  /kill <id>  /clear  /model <name>  /help
  q quit  c clear  p ps  ? help
"""


def _ollama_models(host="http://localhost:11434"):
    try:
        import httpx

        r = httpx.get(f"{host}/api/tags", timeout=1.5)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def _git_branch():
    try:
        import subprocess

        r = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, timeout=0.6, check=False)
        return r.stdout.strip()
    except Exception:
        return ""


def _list_files(path="."):
    try:
        p = pathlib.Path(path)
        return sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:30]
    except Exception:
        return []


def build_welcome_app():
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Input, RichLog, Static
    from rich.panel import Panel

    class WelcomeApp(App):
        CSS = """
        Screen { background: #0c0c0e; }
        #header { height: 3; background: #141416; color: #d4d4d8; }
        #body { height: 1fr; }
        #side { width: 30; background: #0e0e10; border-right: solid #1f1f23; }
        #main { width: 1fr; background: #0c0c0e; }
        #log { height: 1fr; background: #0c0c0e; }
        #inp { dock: bottom; height: 3; background: #141416; border-top: solid #27272a; }
        Input { background: #1a1a1e; }
        """

        def __init__(self, model, host):
            super().__init__()
            self._model = model
            self._host = host

        def compose(self) -> ComposeResult:
            yield Static(f" ◉ sprint  {self._model}  •  welcome  —  /help  •  q quit", id="header")
            with Horizontal(id="body"):
                with Vertical(id="side"):
                    yield Static("[b]FILES[/] [dim]/list[/]", id="f_title")
                    yield Static("", id="f_list")
                    yield Static("\n[b]SESSIONS[/] [dim]/ps[/]", id="p_title")
                    yield Static("", id="p_list")
                    yield Static("\n[dim]q quit  c clear\n/list  /ps  /kill\n/help", id="hints")
                with Vertical(id="main"):
                    yield RichLog(id="log", markup=True, wrap=True, highlight=True)
                    yield Input(placeholder='> task or /command  ("summarize repo"  /list  /ps  /help)', id="inp")
            yield Footer()

        def on_mount(self):
            self.rlog = self.query_one("#log", RichLog)
            self.f_list = self.query_one("#f_list", Static)
            self.p_list = self.query_one("#p_list", Static)
            self.input_box = self.query_one("#inp", Input)
            self._refresh_files()
            self._refresh_ps()
            self.rlog.write(f"[b magenta]◉ sprint[/] [dim]v0.3 fixed UI[/]\n")
            self.rlog.write(WELCOME)
            ms = _ollama_models(self._host)
            if ms:
                self.rlog.write(f"[b]local models:[/] {', '.join(ms[:5])}")
            else:
                self.rlog.write("[dim]no ollama — pull qwen2.5:0.5b[/]")
            self.input_box.focus()
            self.set_interval(2.5, self._refresh_ps)

        def _refresh_files(self, path="."):
            try:
                items = _list_files(path)
                lines = []
                for it in items:
                    if it.is_dir():
                        lines.append(f"[#60a5fa]▸ {it.name}/[/]")
                    else:
                        lines.append(f"[dim]·[/] {it.name}")
                self.f_list.update("\n".join(lines) or "[dim]empty[/]")
            except Exception as e:
                self.f_list.update(f"[red]{e}[/]")

        def _refresh_ps(self):
            sess = list_sessions()
            if not sess:
                self.p_list.update("[dim]no agents[/]")
                return
            lines = []
            for s in sess[:7]:
                alive = "[green]●[/]" if s["alive"] else "[dim]○[/]"
                lines.append(f"{alive} {s['id']} {s['model'][:10]}")
            self.p_list.update("\n".join(lines))

        def on_input_submitted(self, e: Input.Submitted):
            val = e.value.strip()
            self.input_box.value = ""
            if not val:
                return
            if val.startswith("/"):
                parts = val[1:].split()
                c = parts[0].lower() if parts else ""
                if c in ("list", "ls"):
                    p = parts[1] if len(parts) > 1 else "."
                    self._refresh_files(p)
                    self.rlog.write(f"[b]/list {p}[/]")
                elif c in ("ps",):
                    self.rlog.write("[b]/ps[/]")
                    for s in list_sessions():
                        self.rlog.write(f"  {s['id']} pid:{s['pid']} {s['task'][:40]}")
                    self._refresh_ps()
                elif c in ("kill", "k"):
                    if len(parts) < 2:
                        self.rlog.write("[red]usage: /kill <id>[/]")
                    else:
                        self.rlog.write(f"[yellow]kill[/] {kill_session(parts[1])}")
                        self._refresh_ps()
                elif c in ("clear", "c"):
                    self.rlog.clear()
                elif c in ("help", "h", "?"):
                    self.rlog.write(Panel(WELCOME, title="help", border_style="yellow"))
                else:
                    self.rlog.write(f"[red]unknown /{c}[/]")
                return
            if val.lower() in ("help", "h"):
                self.rlog.write(Panel(WELCOME, title="help", border_style="yellow"))
                return
            self.exit(result=val)

    return WelcomeApp


def build_agent_app():
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Input, RichLog, Static
    from rich.panel import Panel

    class AgentApp(App):
        CSS = """
        Screen { background: #0c0c0e; }
        #header { height: 3; background: #141416; color: #e4e4e7; }
        #body { height: 1fr; }
        #side { width: 30; background: #0e0e10; border-right: solid #1f1f23; }
        #main { width: 1fr; background: #0c0c0e; }
        #log { height: 1fr; background: #0c0c0e; }
        #inp { dock: bottom; height: 3; background: #141416; border-top: solid #27272a; }
        #status { height: 1; background: #141416; color: #71717a; }
        Input { background: #1a1a1e; }
        """

        def __init__(self, task_text, llm_fn, model, max_steps):
            super().__init__()
            self._task_text = task_text
            self._llm_fn = llm_fn
            self._model = model
            self._max_steps = max_steps
            self._queue: queue.Queue = queue.Queue()

        def compose(self) -> ComposeResult:
            cwd = str(pathlib.Path.cwd())[-38:]
            branch = _git_branch()
            git_s = f" {branch}" if branch else ""
            yield Static(f" ◉ sprint  [dim]{cwd}{git_s} | {self._model} | {self._max_steps} steps[/]", id="header")
            with Horizontal(id="body"):
                with Vertical(id="side"):
                    yield Static("[b]FILES[/]", id="f_t")
                    yield Static("", id="f_l")
                    yield Static("\n[b]AGENTS[/] [dim]/ps[/]", id="p_t")
                    yield Static("", id="p_l")
                    yield Static("\n[dim]q quit  c clear\n/list /ps /kill /help[/]", id="hint")
                with Vertical(id="main"):
                    yield RichLog(id="log", markup=True, wrap=True, highlight=True)
                    yield Static(" idle", id="status")
                    yield Input(placeholder='> new task or /command  (/list /ps /kill /clear /model /help)', id="inp")
            yield Footer()

        def on_mount(self):
            self.rlog = self.query_one("#log", RichLog)
            self.status_w = self.query_one("#status", Static)
            self.f_list = self.query_one("#f_l", Static)
            self.p_list = self.query_one("#p_l", Static)
            self.input_box = self.query_one("#inp", Input)
            self._refresh_files()
            self._refresh_ps()
            self.rlog.write(f"[b yellow]TASK[/] {self._task_text}")
            self.input_box.focus()
            threading.Thread(target=self._run_bot, daemon=True).start()
            self.set_interval(0.12, self._poll)
            self.set_interval(2.0, self._refresh_ps)

        def _refresh_files(self, path="."):
            try:
                items = _list_files(path)
                lines = []
                for it in items:
                    if it.is_dir():
                        lines.append(f"[#60a5fa]▸ {it.name}/[/]")
                    else:
                        lines.append(f"· {it.name}")
                self.f_list.update("\n".join(lines)[:1200] or "[dim]empty[/]")
            except Exception:
                self.f_list.update("[dim]err[/]")

        def _refresh_ps(self):
            sess = list_sessions()
            if not sess:
                self.p_list.update("[dim]no agents[/]")
                return
            lines = []
            for s in sess[:8]:
                dot = "[green]●[/]" if s["alive"] else "[dim]○[/]"
                lines.append(f"{dot} {s['id']} {s['model'][:10]}")
            self.p_list.update("\n".join(lines))

        def _run_bot(self):
            def cb(e):
                self._queue.put(e)

            bot = TuiSprint(self._llm_fn, max_steps=self._max_steps, event_cb=cb, model=self._model, task=self._task_text)
            bot.run(self._task_text)

        def _poll(self):
            while not self._queue.empty():
                ev = self._queue.get_nowait()
                t = ev.type
                d = ev.data
                if t == "step":
                    self.status_w.update(f" step {d['step']}/{self._max_steps} ctx {d['ctx']}/{MAX_CTX_CHARS} msgs {d['msgs']} sid:{d.get('sid','')} {self._model}")
                elif t == "llm_done":
                    self.rlog.write(f"[dim][{d['step']}] {self._model}:[/] {d['raw'][:500]}")
                elif t == "tool_start":
                    arg_s = ", ".join(f"{k}={str(v)[:28]}" for k, v in d["args"].items())
                    self.rlog.write(f"[#60a5fa b]● {d['tool']}[/] {arg_s}")
                elif t == "tool_done":
                    out = d["out"]
                    if "OK wrote" in out:
                        self.rlog.write(f"  [green]└─ {out}[/]")
                        self._refresh_files()
                    elif out.startswith("ERR"):
                        self.rlog.write(f"  [red]└─ {out[:320]}[/]")
                    else:
                        self.rlog.write(f"  [dim]└─ {out[:320]}[/]")
                elif t == "done":
                    self.rlog.write(Panel(d["result"], title="DONE", border_style="green"))
                elif t == "final":
                    self.status_w.update(" done")
                elif t == "error":
                    self.rlog.write(f"[red]! {d.get('msg','')} {d.get('raw','')[:100]}[/]")

        def on_input_submitted(self, e: Input.Submitted):
            val = e.value.strip()
            self.input_box.value = ""
            if not val:
                return
            if val.startswith("/"):
                parts = val[1:].split()
                c = parts[0].lower() if parts else ""
                if c in ("list", "ls"):
                    p = parts[1] if len(parts) > 1 else "."
                    self._refresh_files(p)
                    self.rlog.write(f"[b]/list {p}[/]")
                elif c in ("ps",):
                    for s in list_sessions():
                        self.rlog.write(f"  {s['id']} pid:{s['pid']} {s['task'][:40]}")
                    self._refresh_ps()
                elif c in ("kill", "k"):
                    if len(parts) < 2:
                        self.rlog.write("[red]usage: /kill <id>[/]")
                    else:
                        self.rlog.write(f"[yellow]{kill_session(parts[1])}[/]")
                        self._refresh_ps()
                elif c in ("clear", "c"):
                    self.rlog.clear()
                elif c in ("help", "h", "?"):
                    self.rlog.write(Panel(WELCOME, title="help", border_style="yellow"))
                elif c in ("model", "m"):
                    if len(parts) > 1:
                        self._model = parts[1]
                        self.rlog.write(f"[yellow]model -> {self._model}[/]")
                elif c in ("quit", "q"):
                    self.exit()
                else:
                    self.rlog.write(f"[red]unknown /{c}[/]")
                return
            self._task_text = val
            self.rlog.write(f"[b yellow]TASK[/] {val}")
            threading.Thread(target=self._run_bot, daemon=True).start()

    return AgentApp
