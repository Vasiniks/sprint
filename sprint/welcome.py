"""
Initial/landing page TUI - shown when `sprint` with no args.
Claude Code / OpenCode inspired: model picker, recent, help.
"""
import sys, shutil, os, json, pathlib

WELCOME_TEXT = """
◉ sprint — tiny agentic harness (8K ctx)

  Tiny models, tiny prompt (89 tok), 5 tools.
  Ollama-first. Works on 135M-3B.

QUICKSTART
  sprint "list files and write report"          # run task (auto TUI if TTY)
  sprint --tui "your task" --model qwen2.5:1.5b # force TUI
  sprint help                                   # this page + examples
  sprint models                                 # list ollama models
  sprint init                                   # create example task

TOOLS
  read(path)  write(path,content)  shell(cmd)  fetch(url)  done(result)

MODELS (tested)
  smollm2:135m         92MB  fragile
  qwen2.5:0.5b        397MB  rec. minimum ← default
  qwen2.5:1.5b        986MB  best tiny agent

EXAMPLES
  sprint "explore repo and make SUMMARY.md"
  sprint "find TODOs and fix them" --model qwen2.5:1.5b
  sprint "fetch https://example.com and summarize"
"""

def _ollama_models(host="http://localhost:11434"):
    try:
        import httpx
        r = httpx.get(f"{host}/api/tags", timeout=3)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models",[])]
    except:
        return []

def run_welcome_tui(model="qwen2.5:0.5b", host="http://localhost:11434"):
    # Try textual landing page, fallback to plain
    try:
        from textual.app import App, ComposeResult
        from textual.widgets import Static, Footer, Input, RichLog, Button
        from textual.containers import Vertical, Horizontal
        from rich.panel import Panel
        import threading, queue

        class WelcomeApp(App):
            CSS = """
            Screen { background: #0f0f10; }
            #header { height: 4; background: #17171a; color: #fafafa; padding: 1 2; }
            #body { height: 1fr; background: #0f0f10; padding: 1 2; }
            #input { dock: bottom; height: 3; background: #1a1a1e; margin: 0 1; }
            #models { height: 6; background: #121214; border: solid #27272a; margin: 1 0; }
            Input { background: #1e1e22; }
            #hint { height: 3; color: #71717a; padding: 0 2; }
            """

            def __init__(self, default_model, host):
                super().__init__()
                self.default_model = default_model
                self.host = host
                self.models = _ollama_models(host)

            def compose(self) -> ComposeResult:
                yield Static(f" ◉ sprint [dim]v0.1.0  —  tiny 8K harness  —  {self.default_model}[/] \n [dim]q quit • enter run • tab model • help docs[/]", id="header")
                yield RichLog(id="body", wrap=True, markup=True)
                yield Input(placeholder='Describe task... e.g. "list files and write hello.txt"  (or type "help", "models")', id="input")
                yield Static(" [dim]↵ to sprint  •  sprint <task> --model qwen2.5:1.5b  •  sprint help[/]", id="hint")
                yield Footer()

            def on_mount(self):
                self.body = self.query_one("#body", RichLog)
                self.input = self.query_one("#input", Input)
                self.body.write("[bold magenta]◉ sprint[/] [dim]— tiny agent, Ollama-first[/]\n")
                self.body.write(WELCOME_TEXT)
                if self.models:
                    self.body.write(f"\n[bold]local models:[/] {', '.join(self.models[:8])}")
                else:
                    self.body.write("\n[dim]no ollama models found — run `ollama pull qwen2.5:0.5b`[/]")
                self.body.write("\n[dim]Type task below and press Enter...[/]")
                self.input.focus()

            def on_input_submitted(self, event: Input.Submitted):
                val = event.value.strip()
                if not val:
                    return
                if val in ("help","h","--help","-h"):
                    self.body.write(Panel(WELCOME_TEXT, title="help", border_style="yellow"))
                    self.input.value=""
                    return
                if val in ("models","model","m"):
                    ms = _ollama_models(self.host)
                    txt = "\n".join(f"  • {m}" for m in ms) if ms else "  (none)"
                    self.body.write(Panel(txt, title="ollama models", border_style="blue"))
                    self.input.value=""
                    return
                # launch main TUI with task
                self.exit(result=val)

        app = WelcomeApp(model, host)
        result = app.run()
        return result

    except Exception as e:
        # plain fallback
        print(WELCOME_TEXT)
        ms = _ollama_models(host)
        if ms:
            print("\nlocal ollama models:")
            for m in ms[:10]:
                print(f"  • {m}")
        print("\n> type task (or 'help'):")
        try:
            task = input("> ").strip()
            return task if task else None
        except KeyboardInterrupt:
            return None

def print_help():
    print(WELCOME_TEXT)
    print("\nCLI\n  sprint [task...] [options]\n")
    print("OPTIONS")
    print("  --model MODEL        ollama model (default qwen2.5:0.5b)")
    print("  --steps N            max steps (default 20)")
    print("  --tui                force full-screen TUI")
    print("  --no-tui             force plain log output")
    print("  --backend ollama|openai|fake")
    print("  --host URL           ollama host")
    print("\nCOMMANDS")
    print("  sprint               -> landing page (initial page)")
    print("  sprint help          -> help")
    print("  sprint models        -> list ollama models")
    print("  sprint init          -> create example files")
    print("  sprint <task>        -> run agent")
    print("")
