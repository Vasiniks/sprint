"""
Landing page - sprint with no args.
Now proper dashboard with files + sessions + input.
Commands: list, ps, kill, help, etc.
"""
import pathlib, sys
from .state import list_sessions, kill_session

WELCOME = """
◉ sprint — tiny 8K harness + TUI

  89tok prompt, 5 tools, Ollama-first, 135M-3B

COMMANDS
  sprint                -> this dashboard
  sprint help           -> help
  sprint list [path]    -> ls files
  sprint ps             -> running agents
  sprint kill <id|pid>  -> stop agent
  sprint init           -> scaffold TASK.md
  sprint "task"         -> run agent
  sprint --tui "task"   -> force TUI

IN-TUI SLASH
  /list [path]  /ps  /kill <id>  /clear  /model <name>  /help  /quit
  q quit  c clear  f files  p ps  ? help
"""

def _ollama_models(host="http://localhost:11434"):
    try:
        import httpx
        r = httpx.get(f"{host}/api/tags", timeout=2)
        return [m["name"] for m in r.json().get("models",[])]
    except:
        return []

def print_help():
    print(WELCOME)
    ms = _ollama_models()
    if ms:
        print("OLLAMA MODELS")
        for m in ms[:12]:
            print(f"  • {m}")
    print("\nEXAMPLES")
    print("  sprint \"list files and write summary.md\"")
    print("  sprint \"fix TODOs\" --model qwen2.5:1.5b")
    print("  sprint list src/")
    print("  sprint ps")
    print("  sprint kill 123456")
    print("")

def cmd_list(path="."):
    p = pathlib.Path(path)
    if not p.exists():
        print(f"not found: {path}"); return
    if p.is_file():
        print(p.read_text()[:2000]); return
    items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    for it in items:
        tag = "📁" if it.is_dir() else "📄"
        print(f"{tag} {it.name}{'/' if it.is_dir() else ''}")

def cmd_ps():
    sess = list_sessions()
    if not sess:
        print("no running agents")
        return
    print(f"{'ID':<8} {'PID':<7} {'ALIVE':<6} {'MODEL':<18} TASK")
    for s in sess:
        alive = "●" if s["alive"] else "○"
        print(f"{s['id']:<8} {s['pid']:<7} {alive:<6} {s['model'][:16]:<18} {s['task'][:40]}")

def cmd_kill(target: str):
    print(kill_session(target))

def run_welcome_tui(model="qwen2.5:0.5b", host="http://localhost:11434"):
    try:
        from textual.app import App, ComposeResult
        from textual.widgets import Static, Footer, Input, RichLog
        from textual.containers import Horizontal, Vertical
        from rich.panel import Panel
        import pathlib

        class WelcomeApp(App):
            CSS = """
            Screen { background: #0a0a0b; }
            #header { height: 3; background: #121214; color: #e4e4e7; padding: 0 1; border-bottom: solid #27272a; }
            #body { height: 1fr; }
            #sidebar { width: 36; background: #0f0f10; border-right: solid #1f1f23; padding: 1 1; }
            #main { width: 1fr; background: #0a0a0b; }
            #log { height: 1fr; }
            #input { dock: bottom; height: 3; background: #121214; border-top: solid #27272a; }
            Input { background: #1a1a1e; }
            """

            def __init__(self, model, host):
                super().__init__()
                self.model = model
                self.host = host

            def _files(self, path="."):
                p = pathlib.Path(path)
                try:
                    return sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:25]
                except:
                    return []

            def compose(self) -> ComposeResult:
                yield Static(f" ◉ sprint  [dim]{self.model}  •  welcome  •  q quit  •  /help[/]", id="header")
                with Horizontal(id="body"):
                    with Vertical(id="sidebar"):
                        yield Static("[bold]FILES[/] [dim]/list[/]", id="slabel")
                        yield Static("", id="filelist")
                        yield Static("\n[bold]SESSIONS[/] [dim]/ps[/]", id="pslabel")
                        yield Static("", id="pslist")
                        yield Static("\n[dim]Commands:\n/list [path]\n/ps\n/kill <id>\n/help\ninit[/]", id="cmdhelp")
                    with Vertical(id="main"):
                        yield RichLog(id="log", markup=True, wrap=True)
                        yield Input(placeholder='> task or /command  (e.g. "summarize repo"  /list  /ps  /help)', id="input")
                yield Footer()

            def on_mount(self):
                self.log = self.query_one("#log", RichLog)
                self.fl = self.query_one("#filelist", Static)
                self.ps = self.query_one("#pslist", Static)
                self.inp = self.query_one("#input", Input)
                self.refresh_files()
                self.refresh_ps()
                self.log.write("[bold magenta]◉ sprint[/] [dim]tiny 8K harness[/]\n")
                self.log.write(WELCOME)
                ms = _ollama_models(self.host)
                if ms:
                    self.log.write(f"[bold]ollama:[/] {', '.join(ms[:6])}")
                self.inp.focus()
                self.set_interval(3.0, self.refresh_ps)

            def refresh_files(self, path="."):
                its = self._files(path)
                lines=[]
                for it in its:
                    icon="📁" if it.is_dir() else "📄"
                    lines.append(f"{icon} {it.name}{'/' if it.is_dir() else ''}")
                self.fl.update("\n".join(lines) or "[dim]empty[/]")

            def refresh_ps(self):
                sess = list_sessions()
                if not sess:
                    self.ps.update("[dim]no agents[/]"); return
                lines=[]
                for s in sess[:6]:
                    alive="●" if s["alive"] else "○"
                    lines.append(f"{alive} {s['id']} {s['model'][:10]}")
                self.ps.update("\n".join(lines))

            def on_input_submitted(self, event: Input.Submitted):
                val = event.value.strip()
                self.inp.value=""
                if not val: return
                if val.startswith("/"):
                    parts = val[1:].split()
                    c = parts[0] if parts else ""
                    if c in ("list","ls"):
                        p = parts[1] if len(parts)>1 else "."
                        self.refresh_files(p)
                        self.log.write(f"[bold]/list {p}[/]")
                        for it in self._files(p):
                            self.log.write(f"  {it.name}")
                    elif c in ("ps","sessions"):
                        self.log.write("[bold]/ps[/]")
                        for s in list_sessions():
                            self.log.write(f"  {s['id']} pid:{s['pid']} {s['task'][:40]}")
                        self.refresh_ps()
                    elif c in ("kill","k"):
                        if len(parts)<2:
                            self.log.write("[red]/kill <id>[/]")
                        else:
                            self.log.write(f"[yellow]{kill_session(parts[1])}[/]")
                            self.refresh_ps()
                    elif c in ("help","h"):
                        self.log.write(Panel(WELCOME, title="help", border_style="yellow"))
                    elif c in ("clear","c"):
                        self.log.clear()
                    else:
                        self.log.write(f"[red]unknown /{c}[/]")
                    return
                # task -> exit with task as result to run main tui
                if val.lower() in ("help","h"):
                    self.log.write(Panel(WELCOME, title="help", border_style="yellow"))
                    return
                self.exit(result=val)

        app = WelcomeApp(model, host)
        return app.run()

    except Exception as e:
        print(WELCOME)
        ms = _ollama_models(host)
        if ms:
            print("models:", ", ".join(ms[:5]))
        print("\n> task or command:")
        try:
            return input("> ").strip()
        except:
            return None
