# sprint — tiny agentic harness (8K ctx)

Works on **135M-3B** models, Ollama-first.

`read / write / shell / fetch / done` — 89 token system prompt, JSON-only, aggressive pruning.

## install

```bash
pip install -e .
ollama serve &
# recommended tiny but capable models:
ollama pull qwen2.5:0.5b        # 397MB, best minimal
ollama pull qwen2.5:1.5b        # 986MB, reliable
ollama pull smollm2:135m        # 92MB, extreme tiny (fragile)
```

## use with Ollama (primary)

```bash
# 0.5B model - fastest reliable
sprint "find csv files and print first 3 lines" --model qwen2.5:0.5b

# 1.5B - your best tiny agent (tested)
sprint "ls, then write hello.txt with hi" --model qwen2.5:1.5b --steps 12

# via python
from sprint.llms import make_ollama
from sprint.harness import Sprint

llm = make_ollama(model="qwen2.5:1.5b")  # chat API, 8192 ctx, 150tok temp
bot = Sprint(llm, max_steps=15, verbose=True)
bot.run("read README and summarize into SUMMARY.md")
```

## use with any OpenAI-compatible (vLLM, llama.cpp server)

```bash
sprint "task" --backend openai --base-url http://localhost:8000/v1 --model Qwen2.5-0.5B
```

## how it works for 8K ctx

- System prompt: 89 tokens
- Tools: 5 only, max 700 chars output trimmed (`tools.py:MAX_OUT`)
- History: keep system + task + last 5 turns, pruned to 5500 chars (`harness.py:prune_history`)
- Parser: handles `<think>`, markdown fences, single quotes, multi-JSON per turn (`harness.py:extract_all_json`)
- No function-calling required — pure JSON text

## models tested

| model | size | works? | notes |
|-------|------|--------|-------|
| smollm2:135m | 92MB | fragile | loops, needs temp 0.15 |
| MNN-Pocket 0.3B | 191MB | partial | base model, use generate API |
| qwen2.5:0.5b | 397MB | yes | recommended minimum |
| qwen2.5:1.5b abl. | 986MB | yes | **best for tiny agents**, reliable |

## structure

- `sprint/prompt.py` - minimal prompt (89 tok)
- `sprint/tools.py` - 5 tools
- `sprint/harness.py` - loop + pruning
- `sprint/llms.py` - ollama chat/generate + openai-compat
- `examples/minimal_loop.py` - 40 line zero-dep copy-paste
