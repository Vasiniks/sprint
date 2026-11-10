import argparse, sys
from .harness import Sprint
from .llms import make_ollama, make_ollama_generate, make_openai_compat, make_fake

def main():
    p = argparse.ArgumentParser("sprint - tiny agent harness (ollama + TUI)")
    p.add_argument("task", nargs="*", help="task")
    p.add_argument("--backend", default="ollama", choices=["ollama","ollama-generate","openai","fake"])
    p.add_argument("--model", default="qwen2.5:0.5b", help="ollama model name")
    p.add_argument("--host", default="http://localhost:11434")
    p.add_argument("--base-url", default="http://localhost:8000/v1")
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--native-tools", action="store_true")
    p.add_argument("--tui", action="store_true", help="full-screen claude/opencode inspired TUI")
    p.add_argument("--no-tui", action="store_true", help="force plain output")
    p.add_argument("--verbose", action="store_true", default=False)
    args = p.parse_args()

    task = " ".join(args.task) if args.task else sys.stdin.read().strip()
    if not task:
        task = "list files and create hello.txt with 'hi from tiny agent'"

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
            print(f"[sprint TUI] {args.model} | {task[:80]}")
            res = run_tui(task, llm, model_name=args.model, max_steps=args.steps)
            return
        except Exception as e:
            print(f"TUI failed ({e}), falling back to plain")
            import traceback; traceback.print_exc()

    # plain
    print(f"[sprint] backend={args.backend} model={args.model} task={task[:80]}")
    bot = Sprint(llm, max_steps=args.steps, verbose=True)
    res = bot.run(task)
    print("\n=== RESULT ===\n", res)

if __name__ == "__main__":
    main()
