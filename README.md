# sprint — 8K-context harness for tiny agents

Ultra-minimal agentic loop optimized for <1B-3B models with 8K context.

## principles
- <300 token system prompt
- tools = 5 only: `read, write, shell, fetch, done`
- single JSON line per action (no CoT XML hell)
- truncated outputs, aggressive history pruning
- no streaming, no function-calling API required - works on raw text completion

## quickstart
```bash
pip install -e .
python -m sprint "build a todo app in ./app"
```

Uses `llama.cpp`, `ollama`, `openai-compatible`, or any HF endpoint.

## how it works
```
[SYSTEM ~180 tok] + [USER TASK] + [HISTORY pruned to ~5K] -> MODEL
MODEL outputs: {"tool":"shell","args":{"cmd":"ls"}} or {"tool":"done","args":{"result":"..."}}
```
