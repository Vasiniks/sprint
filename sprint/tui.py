"""
Full-screen Claude Code / OpenCode inspired TUI for sprint.
Zero heavy deps - uses rich + textual if available, fallback to curses/rich live.
Install: pip install textual rich
Run: sprint --tui "task"
"""
import time, json, threading, queue, os, sys
from typing import List, Dict, Callable, Optional
from dataclasses import dataclass, field

from .prompt import SYSTEM
from .tools import dispatch
from .harness import extract_all_json, prune_history, MAX_CTX_CHARS, KEEP_LAST

# --- event system ---
@dataclass
class Event:
    type: str  # task, llm_start, llm_delta, llm_done, tool_start, tool_done, error, done
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

# Enhanced harness that emits events
class TuiSprint:
    def __init__(self, llm_fn: Callable[[List[Dict]], str], max_steps=25, event_cb=None):
        self.llm_fn = llm_fn
        self.max_steps = max_steps
        self.on_event = event_cb or (lambda e: None)
        self.msgs: List[Dict] = []

    def run(self, task: str) -> str:
        self.msgs = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"TASK:{task}\nJSON only."}
        ]
        self.on_event(Event("task", {"task": task}))
        ctx_chars = 0

        for step in range(self.max_steps):
            pruned = prune_history(self.msgs)
            ctx_chars = sum(len(m.get("content","")) for m in pruned)
            self.on_event(Event("step", {"step": step, "ctx_chars": ctx_chars, "max": MAX_CTX_CHARS, "msgs_len": len(self.msgs)}))

            # LLM
            self.on_event(Event("llm_start", {"step": step}))
            try:
                raw = self.llm_fn(pruned)
            except Exception as e:
                self.on_event(Event("error", {"msg": str(e)}))
                self.msgs.append({"role":"user","content":f"TOOL ERROR: {e}. Next JSON:"})
                continue
            self.on_event(Event("llm_done", {"raw": raw, "step": step}))

            actions = extract_all_json(raw)
            if not actions:
                self.msgs.append({"role":"assistant","content": raw})
                self.msgs.append({"role":"user","content": 'ERR need JSON like {"tool":"shell","args":{"cmd":"ls"}}'})
                self.on_event(Event("error", {"msg": "bad JSON", "raw": raw[:200]}))
                continue

            for act in actions[:2]:
                tool = act.get("tool","").strip()
                args = act.get("args",{})
                if not isinstance(args, dict):
                    continue
                if tool == "done":
                    res = args.get("result","done")
                    self.on_event(Event("done", {"result": res}))
                    return res

                self.msgs.append({"role":"assistant","content": json.dumps(act)})
                self.on_event(Event("tool_start", {"tool": tool, "args": args, "step": step}))
                out = dispatch(tool, args)
                self.msgs.append({"role":"user","content": f"OUT:{out}\nNext JSON:"})
                self.on_event(Event("tool_done", {"tool": tool, "args": args, "output": out, "step": step}))
                if out == "DONE":
                    return args.get("result","")

        self.on_event(Event("done", {"result": "stop: max steps"}))
        return "stop: max steps"

# --- RICH LIVE FALLBACK (always works) ---
def run_rich_tui(task: str, llm_fn, model_name="tiny", max_steps=25):
    try:
        from rich.live import Live
        from rich.panel import Panel
        from rich.console import Console, Group
        from rich.markdown import Markdown
        from rich.text import Text
        from rich.table import Table
    except ImportError:
        # ultimate fallback - no rich
        print("rich not installed, using plain mode. pip install rich")
        from .harness import Sprint
        bot = Sprint(llm_fn, max_steps=max_steps, verbose=True)
        return bot.run(task)

    console = Console()
    events: List[Event] = []
    state = {"step":0, "ctx":0, "current_tool":None, "outputs":[]}

    def event_cb(e: Event):
        events.append(e)
        if e.type == "step":
            state["step"]=e.data["step"]
            state["ctx"]=e.data["ctx_chars"]
        if e.type == "tool_start":
            state["current_tool"]=e.data

    def render():
        # header
        hdr = Table.grid(padding=(0,1))
        hdr.add_column()
        hdr.add_column()
        hdr.add_column()
        hdr.add_row(
            Text(f"◉ sprint", style="bold magenta"),
            Text(f"model: {model_name}", style="dim"),
            Text(f"step {state['step']}/{max_steps} | ctx {state['ctx']}/{MAX_CTX_CHARS}", style="dim cyan")
        )

        # transcript - last 12 events
        lines = []
        for ev in events[-14:]:
            if ev.type == "task":
                lines.append(Panel(ev.data["task"], title="TASK", border_style="bold yellow"))
            elif ev.type == "llm_done":
                raw = ev.data["raw"][:400]
                lines.append(Panel(raw, title=f"[{ev.data['step']}] agent", border_style="dim", padding=(0,1)))
            elif ev.type == "tool_start":
                t = ev.data["tool"]; a = ev.data["args"]
                arg_str = ", ".join(f"{k}={str(v)[:40]}" for k,v in a.items())
                lines.append(Text(f"  ● {t}({arg_str})", style="bold blue"))
            elif ev.type == "tool_done":
                out = ev.data["output"][:300].replace("\n"," | ")[:120]
                style = "green" if not out.startswith("ERR") else "red"
                lines.append(Text(f"    └─ {out[:160]}", style=style))
            elif ev.type == "error":
                lines.append(Text(f"    ! {ev.data['msg']}", style="red"))
            elif ev.type == "done":
                lines.append(Panel(ev.data["result"], title="RESULT", border_style="bold green"))

        if not lines:
            lines=[Text("waiting...", style="dim")]

        body = Group(*lines[-18:])  # crop
        return Group(
            Panel(hdr, style="on black", height=3),
            Panel(body, title="session", border_style="dim", padding=(0,1)),
            Text(" sprint • q:quit  • ollama-first • JSON-only tiny agent", style="dim")
        )

    # run bot in thread
    q = queue.Queue()
    def run_bot():
        bot = TuiSprint(llm_fn, max_steps=max_steps, event_cb=event_cb)
        res = bot.run(task)
        q.put(res)

    t = threading.Thread(target=run_bot, daemon=True)
    t.start()

    with Live(render(), console=console, refresh_per_second=8, screen=True) as live:
        while t.is_alive():
            live.update(render())
            time.sleep(0.08)
        live.update(render())
        time.sleep(0.5)
    try:
        final = q.get_nowait()
    except:
        final = "aborted"
    console.print(Panel(final, title="final", border_style="green"))
    return final

# --- TEXTUAL FULL-SCREEN (claude/opencode style) ---
TEXTUAL_APP = '''
from textual.app import App, ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Header, Footer, Static, Input, RichLog
from textual.reactive import reactive
from rich.panel import Panel
from rich.text import Text
import asyncio, threading, queue

class SprintApp(App):
    CSS = """
    Screen { background: #0f0f10; }
    #header { height: 3; background: #17171a; color: #a1a1aa; }
    #main { height: 1fr; }
    #log { height: 1fr; background: #0f0f10; border: none; }
    #status { height: 3; background: #17171a; dock: bottom; }
    #input { dock: bottom; background: #1a1a1e; }
    .tool { color: #60a5fa; }
    .ok { color: #4ade80; }
    .err { color: #f87171; }
    .dim { color: #71717a; }
    """
    BINDINGS = [("q","quit","quit"), ("c","clear","clear")]

    def __init__(self, task, llm_fn, model_name, max_steps):
        super().__init__()
        self.task=task
        self.llm_fn=llm_fn
        self.model_name=model_name
        self.max_steps=max_steps
        self.event_q=queue.Queue()

    def compose(self) -> ComposeResult:
        yield Static(f" ◉ sprint  |  {self.model_name}  |  ollama-first tiny agent", id="header")
        yield RichLog(id="log", highlight=True, markup=True, wrap=True)
        yield Static(" idle", id="status")
        yield Footer()

    def on_mount(self):
        self.log=self.query_one("#log", RichLog)
        self.status=self.query_one("#status", Static)
        self.log.write(f"[bold yellow]TASK:[/] {self.task}")
        threading.Thread(target=self.run_bot, daemon=True).start()
        self.set_interval(0.08, self.poll_events)

    def run_bot(self):
        from sprint.tui import TuiSprint
        def cb(e):
            self.event_q.put(e)
        bot=TuiSprint(self.llm_fn, max_steps=self.max_steps, event_cb=cb)
        res=bot.run(self.task)
        self.event_q.put({"type":"final","result":res})

    def poll_events(self):
        while not self.event_q.empty():
            ev=self.event_q.get_nowait()
            # ev can be dataclass or dict
            typ = ev.type if hasattr(ev,'type') else ev.get('type')
            data = ev.data if hasattr(ev,'data') else ev
            if typ=="step":
                self.status.update(f" step {data['step']}/{self.max_steps} | ctx {data['ctx_chars']} chars | {self.model_name}")
            elif typ=="llm_done":
                raw=data['raw'][:500]
                self.log.write(f"[dim][{data['step']}] agent:[/] {raw}")
            elif typ=="tool_start":
                args=", ".join(f"{k}={str(v)[:40]}" for k,v in data['args'].items())
                self.log.write(f"[#60a5fa]● {data['tool']}[/] ({args})")
            elif typ=="tool_done":
                out=data['output'][:300].replace('\\n',' ')
                col="green" if "OK" in out or "wrote" in out.lower() else ("red" if out.startswith("ERR") else "dim")
                self.log.write(f"  [{col}]└─ {out[:200]}[/]")
            elif typ=="done":
                self.log.write(Panel(data['result'], title="DONE", border_style="green"))
                self.status.update(f" done: {data['result'][:60]}")
            elif typ=="final":
                self.log.write(Panel(data['result'], title="FINAL RESULT", border_style="bold green"))
            elif typ=="error":
                self.log.write(f"[red]! {data.get('msg','err')}[/]")

    def action_clear(self):
        self.log.clear()
'''

def run_textual_tui(task: str, llm_fn, model_name="tiny", max_steps=25):
    try:
        from textual.app import App
    except ImportError:
        return run_rich_tui(task, llm_fn, model_name, max_steps)

    # dynamic exec of app to avoid heavy import at module load
    import tempfile, pathlib, sys
    # create temp file with app
    tmp = pathlib.Path(tempfile.gettempdir()) / "sprint_textual_app.py"
    tmp.write_text(TEXTUAL_APP)
    sys.path.insert(0, str(tmp.parent.parent))

    # Build app inline without file for simpler
    from textual.app import App, ComposeResult
    from textual.widgets import Static, Footer, RichLog
    from textual.containers import Vertical
    from rich.panel import Panel
    import queue, threading

    class SprintApp(App):
        CSS = """
        Screen { background: #0f0f10; }
        #header { height: 3; background: #17171a; color: #a1a1aa; padding: 1 2; }
        #log { height: 1fr; background: #0f0f10; }
        #status { height: 3; background: #17171a; padding: 0 2; }
        """
        BINDINGS = [("q","quit","Quit"),("c","clear","Clear")]

        def __init__(self, task, llm_fn, model_name, max_steps):
            super().__init__()
            self.task=task; self.llm_fn=llm_fn; self.model_name=model_name; self.max_steps=max_steps
            self.eq=queue.Queue()

        def compose(self) -> ComposeResult:
            yield Static(f" ◉ sprint   {self.model_name}   {self.max_steps} steps   ollama-first  • q=quit c=clear", id="header")
            yield RichLog(id="log", highlight=True, markup=True, wrap=True)
            yield Static(" starting...", id="status")
            yield Footer()

        def on_mount(self):
            self.log=self.query_one("#log", RichLog)
            self.status=self.query_one("#status", Static)
            self.log.write(f"[bold yellow]TASK[/] {self.task}")
            self.log.write("[dim]Waiting for tiny model...[/]")
            threading.Thread(target=self.run_bot, daemon=True).start()
            self.set_interval(0.08, self.poll)

        def run_bot(self):
            def cb(e): self.eq.put(e)
            bot=TuiSprint(self.llm_fn, max_steps=self.max_steps, event_cb=cb)
            res=bot.run(self.task)
            self.eq.put(Event("final", {"result":res}))

        def poll(self):
            while not self.eq.empty():
                ev=self.eq.get_nowait()
                typ=ev.type
                d=ev.data
                if typ=="step":
                    self.status.update(f" step {d['step']}/{self.max_steps} | ctx {d['ctx_chars']}/{MAX_CTX_CHARS} chars | msgs {d['msgs_len']} | {self.model_name}")
                elif typ=="llm_done":
                    self.log.write(f"[dim][{d['step']}] agent:[/] {d['raw'][:600]}")
                elif typ=="tool_start":
                    arg_s=", ".join(f"{k}={str(v)[:30]}" for k,v in d['args'].items())
                    self.log.write(f"[#60a5fa bold]● {d['tool']}[/] [dim]({arg_s})[/]")
                elif typ=="tool_done":
                    out=d['output']
                    if "OK wrote" in out:
                        self.log.write(f"  [green]└─ {out}[/]")
                    elif out.startswith("ERR"):
                        self.log.write(f"  [red]└─ {out[:250]}[/]")
                    else:
                        self.log.write(f"  [dim]└─ {out[:250]}[/]")
                elif typ=="done":
                    self.log.write(Panel(d['result'], title="DONE", border_style="green"))
                elif typ=="final":
                    self.log.write(Panel(d['result'], title="RESULT", border_style="bold green"))
                    self.status.update(f" done • {d['result'][:80]}")
                elif typ=="error":
                    self.log.write(f"[red]! {d.get('msg','')} {d.get('raw','')[:100]}[/]")
                elif typ=="task":
                    pass

        def action_clear(self):
            self.log.clear()

    app=SprintApp(task, llm_fn, model_name, max_steps)
    app.run()
    return "tui exit"

def run_tui(task: str, llm_fn, model_name="tiny", max_steps=25):
    # try textual first, else rich
    try:
        import textual
        return run_textual_tui(task, llm_fn, model_name, max_steps)
    except Exception:
        return run_rich_tui(task, llm_fn, model_name, max_steps)
