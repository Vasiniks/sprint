"""TUI wrapper -> ui_v3 (fixed)"""
from .ui_v3 import TuiSprint, Event, build_agent_app, _ollama_models, _git_branch, WELCOME
import queue, threading, time

def run_textual_tui(task: str, llm_fn, model_name="tiny", max_steps=25):
    AgentApp = build_agent_app()
    app = AgentApp(task, llm_fn, model_name, max_steps)
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
            state["ctx"] = e.data["ctx"]

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
                out = ev.data["out"][:200].replace("\n", " ")
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
        import textual
        return run_textual_tui(task, llm_fn, model_name, max_steps)
    except Exception as e:
        print(f"textual failed {e}, fallback rich")
        return run_rich_tui(task, llm_fn, model_name, max_steps)
