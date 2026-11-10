import argparse, sys
from .harness import Sprint
from .llms import make_ollama, make_openai_compat, make_fake

def main():
    p = argparse.ArgumentParser("sprint - tiny agent harness")
    p.add_argument("task", nargs="*", help="task description")
    p.add_argument("--backend", default="fake", choices=["ollama","openai","fake"])
    p.add_argument("--model", default=None)
    p.add_argument("--ollama-host", default="http://localhost:11434")
    p.add_argument("--base-url", default="http://localhost:8000/v1")
    p.add_argument("--steps", type=int, default=30)
    args = p.parse_args()

    task = " ".join(args.task) if args.task else sys.stdin.read().strip()
    if not task:
        task = "list files and create hello.txt with 'hi'"

    if args.backend == "ollama":
        llm = make_ollama(model=args.model or "qwen2.5:0.5b", host=args.ollama_host)
    elif args.backend == "openai":
        llm = make_openai_compat(model=args.model or "Qwen/Qwen2.5-0.5B-Instruct", base_url=args.base_url)
    else:
        llm = make_fake()

    bot = Sprint(llm, max_steps=args.steps)
    res = bot.run(task)
    print("\n=== RESULT ===\n", res)

if __name__ == "__main__":
    main()
