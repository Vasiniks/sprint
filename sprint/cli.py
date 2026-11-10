import argparse, sys
from .harness import Sprint
from .llms import make_ollama, make_ollama_generate, make_openai_compat, make_fake

def main():
    p = argparse.ArgumentParser("sprint - tiny agent harness (ollama-first)")
    p.add_argument("task", nargs="*", help="task")
    p.add_argument("--backend", default="ollama", choices=["ollama","ollama-generate","openai","fake"])
    p.add_argument("--model", default="smollm2:135m-instruct-q4_0", help="ollama model name")
    p.add_argument("--host", default="http://localhost:11434")
    p.add_argument("--base-url", default="http://localhost:8000/v1")
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--native-tools", action="store_true", help="use ollama native tool calling (needs qwen2.5 1.5B+)")
    p.add_argument("--verbose", action="store_true", default=True)
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

    print(f"[sprint] backend={args.backend} model={args.model} task={task[:80]}")
    bot = Sprint(llm, max_steps=args.steps, verbose=args.verbose)
    res = bot.run(task)
    print("\n=== RESULT ===\n", res)

if __name__ == "__main__":
    main()
