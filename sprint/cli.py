import argparse, sys, os

def main():
    # --- pre-parse aliases/comands before argparse ---
    raw_args = sys.argv[1:]
    # normalize: first token might be command
    cmd = raw_args[0] if raw_args else ""
    # if no args -> welcome/landing page
    if not raw_args:
        from .welcome import run_welcome_tui
        task = run_welcome_tui()
        if not task or task.lower() in ("help","h"):
            from .welcome import print_help
            print_help()
            return
        # run that task via normal flow
        sys.argv = ["sprint", task, "--tui"]
        # fallthrough to normal parser below with task injected

    elif cmd.lower() in ("help","--help","-h","?"):
        from .welcome import print_help
        print_help()
        return

    elif cmd.lower() in ("models","model","ls"):
        from .welcome import _ollama_models
        ms = _ollama_models()
        if ms:
            print("ollama models:")
            for m in ms:
                print(f"  • {m}")
        else:
            print("no ollama models found. pull with:")
            print("  ollama pull qwen2.5:0.5b")
            print("  ollama pull qwen2.5:1.5b")
        return

    elif cmd.lower() in ("init","init-proj"):
        # scaffold starter
        import pathlib
        pathlib.Path("examples").mkdir(exist_ok=True)
        with open("TASK.md","w") as f:
            f.write("# Example task\n\n- List files\n- Write hello.txt\n- Summarize repo\n")
        print("created TASK.md and examples/. run:")
        print("  sprint --tui \"read TASK.md and do it\"")
        return

    # --- normal arg parsing ---
    p = argparse.ArgumentParser("sprint - tiny agent harness", add_help=False)
    p.add_argument("task", nargs="*", help="task")
    p.add_argument("--backend", default="ollama", choices=["ollama","ollama-generate","openai","fake"])
    p.add_argument("--model", default="qwen2.5:0.5b")
    p.add_argument("--host", default="http://localhost:11434")
    p.add_argument("--base-url", default="http://localhost:8000/v1")
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--native-tools", action="store_true")
    p.add_argument("--tui", action="store_true")
    p.add_argument("--no-tui", action="store_true")
    p.add_argument("--verbose", action="store_true", default=False)
    p.add_argument("-h","--help", action="store_true", dest="help_flag")

    # if we came from welcome path, raw_args was replaced
    args, _ = p.parse_known_args()

    if args.help_flag and not args.task:
        from .welcome import print_help
        print_help()
        return

    # task can be injected via welcome path or explicit
    # If still raw_args started with no flag and we didn't go through welcome, use join
    if args.task and args.task[0] not in ("help","models","init"):
        task_str = " ".join(args.task)
    else:
        # if piped stdin?
        if not sys.stdin.isatty():
            task_str = sys.stdin.read().strip()
        else:
            task_str = ""

    # special: if task_str is "help" handle
    if task_str.lower() in ("help","h"):
        from .welcome import print_help
        print_help()
        return

    if not task_str:
        # no task provided but flags given? e.g. sprint --model x -> show welcome
        if len(sys.argv) <=2 and not args.task:
            from .welcome import run_welcome_tui
            t = run_welcome_tui(model=args.model, host=args.host)
            if not t:
                return
            task_str = t
            args.tui = True
        else:
            task_str = "list files and write hello.txt with 'hi'"

    # llm backend
    from .llms import make_ollama, make_ollama_generate, make_openai_compat, make_fake
    if args.backend == "ollama":
        llm = make_ollama(model=args.model, host=args.host, use_tools=args.native_tools)
    elif args.backend == "ollama-generate":
        llm = make_ollama_generate(model=args.model, host=args.host)
    elif args.backend == "openai":
        llm = make_openai_compat(model=args.model, base_url=args.base_url)
    else:
        llm = make_fake()

    use_tui = args.tui or (not args.no_tui and sys.stdout.isatty())

    if use_tui:
        try:
            from .tui import run_tui
            res = run_tui(task_str, llm, model_name=args.model, max_steps=args.steps)
            return
        except KeyboardInterrupt:
            print("\naborted")
            return
        except Exception as e:
            print(f"TUI failed ({e}), falling back")
            import traceback; traceback.print_exc()

    from .harness import Sprint
    print(f"[sprint] {args.backend}/{args.model} :: {task_str[:80]}")
    bot = Sprint(llm, max_steps=args.steps, verbose=True)
    res = bot.run(task_str)
    print("\n=== RESULT ===\n", res)

if __name__ == "__main__":
    main()
