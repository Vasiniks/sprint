"""Welcome wrapper -> ui_v3"""
from .ui_v3 import build_welcome_app, WELCOME, _ollama_models
from .state import list_sessions, kill_session
import pathlib

def print_help():
    print(WELCOME)
    ms = _ollama_models()
    if ms:
        print("OLLAMA MODELS")
        for m in ms[:12]:
            print(f"  • {m}")
    print("\nEXAMPLES")
    print('  sprint "list files and write summary.md"')
    print('  sprint "fix TODOs" --model qwen2.5:1.5b')
    print("  sprint list src/")
    print("  sprint ps")
    print("  sprint kill 123456")

def cmd_list(path="."):
    p = pathlib.Path(path)
    if not p.exists():
        print(f"not found: {path}")
        return
    if p.is_file():
        print(p.read_text()[:2000])
        return
    items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    for it in items:
        tag = "DIR" if it.is_dir() else "FILE"
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
        WelcomeApp = build_welcome_app()
        app = WelcomeApp(model, host)
        return app.run()
    except Exception:
        # fallback plain
        print(WELCOME)
        ms = _ollama_models(host)
        if ms:
            print("models:", ", ".join(ms[:5]))
        print("\n> task or command:")
        try:
            return input("> ").strip()
        except Exception:
            return None
