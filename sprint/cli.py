import sys

def main():
    raw = sys.argv[1:]
    cmd = raw[0].lower() if raw else ""

    # no args -> welcome dashboard
    if not raw:
        from .welcome import run_welcome_tui
        task = run_welcome_tui()
        if not task:
            return
        if task.lower() in ("help","h"):
            from .welcome import print_help; print_help(); return
        # run task in main TUI
        sys.argv = ["sprint", task, "--tui"]
        # fallthrough to parse

    elif cmd in ("help","--help","-h","?"):
        from .welcome import print_help; print_help(); return

    elif cmd in ("list","ls","l","dir"):
        from .welcome import cmd_list
        path = raw[1] if len(raw)>1 else "."
        cmd_list(path); return

    elif cmd in ("ps","sessions","jobs"):
        from .welcome import cmd_ps
        cmd_ps(); return

    elif cmd in ("kill","k","stop"):
        from .welcome import cmd_kill
        if len(raw)<2:
            print("usage: sprint kill <id|pid>"); print("sprint ps to list")
            return
        cmd_kill(raw[1]); return

    elif cmd in ("init","init-proj","bootstrap"):
        import pathlib
        pathlib.Path("examples").mkdir(exist_ok=True)
        pathlib.Path("TASK.md").write_text("# Task\n\n- List files\n- Write hello.txt\n")
        print("created TASK.md"); return

    # --- normal agent run ---
    import argparse
    p = argparse.ArgumentParser("sprint", add_help=False)
    p.add_argument("task", nargs="*")
    p.add_argument("--backend", default="ollama", choices=["ollama","ollama-generate","openai","fake"])
    p.add_argument("--model", default="qwen2.5:0.5b")
    p.add_argument("--host", default="http://localhost:11434")
    p.add_argument("--base-url", default="http://localhost:8000/v1")
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--native-tools", action="store_true")
    p.add_argument("--tui", action="store_true")
    p.add_argument("--no-tui", action="store_true")
    p.add_argument("--help", action="store_true", dest="help_flag")
    args, _ = p.parse_known_args()

    if args.help_flag and not args.task:
        from .welcome import print_help; print_help(); return

    # build task string
    if args.task:
        task = " ".join(args.task)
    else:
        task = sys.stdin.read().strip() if not sys.stdin.isatty() else ""

    if not task:
        # if flags only like --model, show welcome
        from .welcome import run_welcome_tui
        t = run_welcome_tui(model=args.model, host=args.host)
        if not t:
            return
        task = t
        args.tui = True

    if task.lower() in ("help","h"):
        from .welcome import print_help; print_help(); return

    from .llms import make_ollama, make_ollama_generate, make_openai_compat, make_fake
    if args.backend=="ollama":
        llm = make_ollama(model=args.model, host=args.host, use_tools=args.native_tools)
    elif args.backend=="ollama-generate":
        llm = make_ollama_generate(model=args.model, host=args.host)
    elif args.backend=="openai":
        llm = make_openai_compat(model=args.model, base_url=args.base_url)
    else:
        llm = make_fake()

    use_tui = args.tui or (not args.no_tui and sys.stdout.isatty())

    if use_tui:
        try:
            from .tui import run_tui
            run_tui(task, llm, model_name=args.model, max_steps=args.steps)
            return
        except KeyboardInterrupt:
            print("\naborted"); return
        except Exception as e:
            print(f"TUI err {e}, fallback plain"); import traceback; traceback.print_exc()

    from .harness import Sprint
    print(f"[sprint] {args.backend}/{args.model} :: {task[:80]}")
    bot = Sprint(llm, max_steps=args.steps, verbose=True)
    res = bot.run(task)
    print("\n=== RESULT ===\n", res)

if __name__=="__main__":
    main()
