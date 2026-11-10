"""
Claude Code / OpenCode inspired full-screen TUI v2
- header cwd + git + model + ctx
- sidebar files + sessions (ps)
- main transcript
- input with /list /ps /kill /clear /help /model /quit
"""
import json
import pathlib
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

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
    def __init__(self, llm_fn: Callable, max_steps=25, event_cb=None, model="tiny", task=""):
        self.llm_fn = llm_fn
        self.max_steps = max_steps
        self.on_event = event_cb or (lambda e: None)
        self.model = model
        self.task = task
        self.msgs: list[dict] = []
        self.sid: str | None = None

    def run(self, task: str) -> str:
        self.task = task
        self.sid = register_session(task, self.model)
        self.on_event(Event("session", {"id": self.sid}))
        self.msgs = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"TASK:{task}\nJSON only."},
        ]
        self.on_event(Event("task", {"task": task}))
        try:
            for step in range(self.max_steps):
                pruned = prune_history(self.msgs)
                ctx_chars = sum(len(m.get("content", "")) for m in pruned)
                self.on_event(
                    Event("step", {"step": step, "ctx_chars": ctx_chars, "max": MAX_CTX_CHARS, "msgs_len": len(self.msgs), "sid": self.sid})
                )
                self.on_event(Event("llm_start", {"step": step}))
                try:
                    raw = self.llm_fn(pruned)
                except Exception as e:
                    self.on_event(Event("error", {"msg": str(e)}))
                    self.msgs.append({"role": "user", "content": f"ERR: {e}. Next JSON:"})
                    continue
                self.on_event(Event("llm_done", {"raw": raw, "step": step}))

                actions = extract_all_json(raw)
                if not actions:
                    self.msgs.append({"role": "assistant", "content": raw})
                    self.msgs.append({"role": "user", "content": 'ERR need JSON {"tool":"shell","args":{"cmd":"ls"}}'})
                    self.on_event(Event("error", {"msg": "bad JSON", "raw": raw[:150]}))
                    continue

                for act in actions[:2]:
                    tool = act.get("tool", "").strip()
                    args = act.get("args", {})
                    if not isinstance(args, dict):
                        continue
                    if tool == "done":
                        res = args.get("result", "done")
                        self.on_event(Event("done", {"result": res}))
                        return res
                    self.msgs.append({"role": "assistant", "content": json.dumps(act)})
                    self.on_event(Event("tool_start", {"tool": tool, "args": args, "step": step}))
                    out = dispatch(tool, args)
                    self.msgs.append({"role": "user", "content": f"OUT:{out}\nNext JSON:"})
                    self.on_event(Event("tool_done", {"tool": tool, "args": args, "output": out, "step": step}))
        finally:
            if self.sid:
                unregister_session(self.sid)
            self.on_event(Event("final", {"result": "done"}))
        self.on_event(Event("done", {"result": "max steps"}))
        return "max steps"


def _git_branch() -> str:
    try:
        r = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, timeout=1, check=False)
        return r.stdout.strip()
    except Exception:
        return ""


def _list_files(path=".") -> list[pathlib.Path]:
    try:
        p = pathlib.Path(path)
        return sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:30]
    except Exception:
        return []


def run_textual_tui(task: str, llm_fn, model_name="tiny", max_steps=25):
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Input, RichLog, Static
    from rich.panel import Panel

    class SprintApp(App):
        CSS = """
        Screen { background: #0a0a0b; }
        #header { height: 3; background: #121214; color: #e4e4e7; padding: 0 1; border-bottom: solid #27272a; }
        #body { height: 1fr; }
        #sidebar { width: 32; background: #0f0f10; border-right: solid #1f1f23; padding: 1 1; }
        #main { width: 1fr; background: #0a0a0b; }
        #log { height: 1fr; background: #0a0a0b; }
        #input { dock: bottom; height: 3; background: #121214; margin: 0 0; border-top: solid #27272a; }
        #status { height: 1; background: #121214; color: #71717a; padding: 0 1; }
        Input { background: #1a1a1e; border: none; }
        .label { color: #71717a; text-style: bold; }
        """
        BINDINGS = [
            ("q", "quit", "Quit"),
            ("c", "clear_log", "Clear"),
            ("f", "toggle_files", "Files"),
            ("p", "show_ps", "PS"),
            ("question_mark", "help", "Help"),
        ]

        def __init__(self, task, llm_fn, model_name, max_steps):
            super().__init__()
            self.task = task
            self.llm_fn = llm_fn
            self.model_name = model_name
            self.max_steps = max_steps
            self.eq: queue.Queue = queue.Queue()
            self.show_sidebar = True

        def compose(self) -> ComposeResult:
            cwd = str(pathlib.Path.cwd())[-40:]
            branch = _git_branch()
            git_str = f" |  {branch}" if branch else ""
            yield Static(f" ◉ sprint  [dim]{cwd}{git_str}  |  {self.model_name}  |  {self.max_steps} steps[/]", id="header")
            with Horizontal(id="body"):
                with Vertical(id="sidebar"):
                    yield Static("[bold]FILES[/] [dim](list)[/]", classes="label")
                    yield Static("", id="filelist")
                    yield Static("", id="spacer")
                    yield Static("[bold]SESSIONS[/] [dim](ps)[/]", classes="label")
                    yield Static("", id="pslist")
                    yield Static("\n[dim]q quit  c clear  f files\np ps  ? help  / + cmd[/]", id="helpmini")
                with Vertical(id="main"):
                    yield RichLog(id="log", highlight=True, markup=True, wrap=True)
                    yield Static(" idle", id="status")
                    yield Input(placeholder='> task or /command  (/list /ps /kill <id> /clear /help /model)', id="input")
            yield Footer()

        def on_mount(self):
            self.rlog = self.query_one("#log", RichLog)
            self.status_w = self.query_one("#status", Static)
            self.filelist_w = self.query_one("#filelist", Static)
            self.pslist_w = self.query_one("#pslist", Static)
            self.input_w = self.query_one("#input", Input)
            self.refresh_files()
            self.refresh_ps()
            self.rlog.write(f"[bold magenta]◉ sprint[/] [dim]tiny agent, 8K ctx[/]")
            self.rlog.write(f"[yellow]TASK:[/] {self.task}\n")
            self.input_w.focus()
            threading.Thread(target=self.run_bot, daemon=True).start()
            self.set_interval(0.1, self.poll)
            self.set_interval(2.0, self.refresh_ps)

        def refresh_files(self, path="."):
            items = _list_files(path)
            lines = []
            for it in items:
                icon = "📁" if it.is_dir() else "📄"
                name = it.name + ("/" if it.is_dir() else "")
                if it.is_dir():
                    lines.append(f"[#60a5fa]{icon} {name}[/]")
                else:
                    lines.append(f"[dim]{icon}[/] {name}")
            self.filelist_w.update("\n".join(lines[:20]) or "[dim](empty)[/]")

        def refresh_ps(self):
            sess = list_sessions()
            if not sess:
                self.pslist_w.update("[dim]no running agents[/]")
                return
            lines = []
            for s in sess[:8]:
                alive = "●" if s["alive"] else "○"
                col = "green" if s["alive"] else "dim"
                lines.append(f"[{col}]{alive} {s['id']} {s['model'][:10]} pid:{s['pid']}[/]\n[dim]  {s['task'][:28]}[/]")
            self.pslist_w.update("\n".join(lines))

        def run_bot(self):
            def cb(e):
                self.eq.put(e)

            bot = TuiSprint(self.llm_fn, max_steps=self.max_steps, event_cb=cb, model=self.model_name, task=self.task)
            bot.run(self.task)

        def poll(self):
            while not self.eq.empty():
                ev = self.eq.get_nowait()
                typ = ev.type
                d = ev.data
                if typ == "step":
                    self.status_w.update(f" step {d['step']}/{self.max_steps} | ctx {d['ctx_chars']}/{MAX_CTX_CHARS} | {self.model_name} | sid:{d.get('sid','')}")
                elif typ == "llm_done":
                    self.rlog.write(f"[dim][{d['step']}] {self.model_name}:[/] {d['raw'][:400]}")
                elif typ == "tool_start":
                    a = ", ".join(f"{k}={str(v)[:25]}" for k, v in d["args"].items())
                    self.rlog.write(f"[#60a5fa bold]● {d['tool']}[/] [dim]{a}[/]")
                elif typ == "tool_done":
                    out = d["output"]
                    if "OK wrote" in out:
                        self.rlog.write(f"  [green]└─ {out}[/]")
                        self.refresh_files()
                    elif out.startswith("ERR"):
                        self.rlog.write(f"  [red]└─ {out[:300]}[/]")
                    else:
                        self.rlog.write(f"  [dim]└─ {out[:300]}[/]")
                elif typ == "done":
                    self.rlog.write(Panel(d["result"], title="DONE", border_style="green"))
                elif typ == "final":
                    self.status_w.update(" done")
                elif typ == "error":
                    self.rlog.write(f"[red]! {d.get('msg','')} {d.get('raw','')[:80]}[/]")

        def on_input_submitted(self, event: Input.Submitted):
            val = event.value.strip()
            self.input_w.value = ""
            if not val:
                return
            if val.startswith("/"):
                cmd = val[1:].split()
                if not cmd:
                    return
                c = cmd[0]
                if c in ("list", "ls", "l"):
                    path = cmd[1] if len(cmd) > 1 else "."
                    self.refresh_files(path)
                    items = _list_files(path)
                    self.rlog.write(f"[bold]list {path}[/]")
                    for it in items[:20]:
                        self.rlog.write(f"  {it.name} {'(dir)' if it.is_dir() else ''}")
                elif c in ("ps", "sessions"):
                    sess = list_sessions()
                    self.rlog.write(f"[bold]ps — {len(sess)} sessions[/]")
                    for s in sess:
                        self.rlog.write(f"  {s['id']} pid:{s['pid']} alive:{s['alive']} {s['task']}")
                    self.refresh_ps()
                elif c.startswith("kill"):
                    target = cmd[1] if len(cmd) > 1 else ""
                    if not target:
                        self.rlog.write("[red]usage: /kill <id|pid>[/]")
                    else:
                        res = kill_session(target)
                        self.rlog.write(f"[yellow]kill:[/] {res}")
                        self.refresh_ps()
                elif c in ("clear", "cls"):
                    self.rlog.clear()
                elif c in ("help", "h", "?"):
                    self.rlog.write(
                        Panel(
                            "/list [path]  - list files\n/ps - running agents\n/kill <id> - kill agent\n/clear - clear log\n/model <name> - switch model\n/help - this",
                            title="commands",
                            border_style="yellow",
                        )
                    )
                elif c in ("model", "m"):
                    if len(cmd) > 1:
                        self.model_name = cmd[1]
                        self.rlog.write(f"[yellow]model -> {self.model_name}[/]")
                    else:
                        self.rlog.write(f"[bold]model: {self.model_name}[/]")
                elif c in ("quit", "q", "exit"):
                    self.exit()
                else:
                    self.rlog.write(f"[red]unknown /{c} - /help[/]")
                return
            self.rlog.write(f"[bold yellow]TASK:[/] {val}")
            self.task = val
            threading.Thread(target=self.run_bot, daemon=True).start()

        def action_clear_log(self):
            self.rlog.clear()

        def action_show_ps(self):
            sess = list_sessions()
            self.rlog.write(f"[bold]ps:[/] {len(sess)} sessions")
            for s in sess:
                self.rlog.write(f"  {s['id']} {s['model']} pid:{s['pid']}")

        def action_toggle_files(self):
            self.show_sidebar = not self.show_sidebar
            self.query_one("#sidebar").display = self.show_sidebar

        def action_help(self):
            self.rlog.write(Panel("/list /ps /kill /clear /model /help", title="help", border_style="yellow"))

    app = SprintApp(task, llm_fn, model_name, max_steps)
    app.run()
    return "tui exit"


def run_rich_tui(task: str, llm_fn, model_name="tiny", max_steps=25):
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = Console()
    events: list[Event] = []
    state = {"step": 0, "ctx": 0}

    def cb(e: Event):
        events.append(e)
        if e.type == "step":
            state["step"] = e.data["step"]
            state["ctx"] = e.data["ctx_chars"]

    def render():
        hdr = Table.grid(padding=(0, 1))
        hdr.add_row(Text("◉ sprint", style="bold magenta"), Text(f"{model_name} | step {state['step']}/{max_steps} ctx {state['ctx']}", style="dim"))
        lines = []
        for ev in events[-16:]:
            if ev.type == "task":
                lines.append(Panel(ev.data["task"], title="TASK", border_style="yellow"))
            elif ev.type == "tool_start":
                lines.append(Text(f" ● {ev.data['tool']} {ev.data['args']}", style="blue"))
            elif ev.type == "tool_done":
                out = ev.data["output"][:200].replace("\n", " ")
                lines.append(Text(f" └─ {out}", style="green" if "OK" in out else "dim"))
            elif ev.type == "done":
                lines.append(Panel(ev.data["result"], title="DONE", border_style="green"))
        body = Group(*(lines[-16:] or [Text("waiting...", style="dim")]))
        return Group(Panel(hdr, height=3), Panel(body, title="session", border_style="dim"), Text(" /list /ps /kill /clear /help | q:quit", style="dim"))

    q: queue.Queue = queue.Queue()

    def run_bot():
        bot = TuiSprint(llm_fn, max_steps=max_steps, event_cb=cb, model=model_name, task=task)
        q.put(bot.run(task))

    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    with Live(render(), console=console, refresh_per_second=10, screen=True) as live:
        while t.is_alive():
            live.update(render())
            time.sleep(0.08)
    try:
        final = q.get_nowait()
    except Exception:
        final = "exit"
    console.print(Panel(final, title="result", border_style="green"))
    return final


def run_tui(task: str, llm_fn, model_name="tiny", max_steps=25):
    try:
        import textual  # noqa: F401

        return run_textual_tui(task, llm_fn, model_name, max_steps)
    except Exception:
        return run_rich_tui(task, llm_fn, model_name, max_steps)
