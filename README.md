# sprint — tiny agentic harness (8K ctx) + TUI

Ollama-first harness for 135M-3B models with Claude Code / OpenCode inspired full-screen TUI.

`read / write / shell / fetch / done` — 89 token prompt, JSON-only, 5.5K ctx pruning.

## install
```bash
pip install -e .   # needs httpx, rich, textual
# minimal without TUI: pip install -e . --no-deps + httpx only
ollama pull qwen2.5:0.5b
ollama pull qwen2.5:1.5b  # best tiny
```

## commands

```bash
sprint                # landing page / initial page (TUI with task input + model list)
sprint help           # help + quickstart
sprint models         # list local ollama models
sprint init           # scaffold TASK.md example
sprint "your task"    # run agent (auto TUI if TTY)
sprint --no-tui "task" --backend fake  # plain logs
```

## TUI (claude/opencode inspired)

- `sprint` no args -> full-screen welcome: task input, local models, examples, q=quit
- `sprint <task>` -> full-screen agent TUI:
  - header: `◉ sprint | model | steps | ctx`
  - log: `● tool(args)` -> `└─ output`
  - status bar live ctx usage

```bash
# auto-enables when TTY
sprint "explore repo and write report" --model qwen2.5:1.5b

# force TUI / plain
sprint --tui "task" --model qwen2.5:0.5b --steps 20
sprint --no-tui "task" --backend fake
```

Implementation: `sprint/tui.py`
- `TuiSprint` emits events: `task, step, llm_start, llm_done, tool_start, tool_done, done`
- `run_rich_tui` fallback (rich.live, threading, 8fps, screen=True)
- `run_textual_tui` (textual 0.60+): Header + RichLog + status + Footer, async poll of event queue

## Ollama
```bash
sprint "ls, then write ok.txt" --model qwen2.5:1.5b --steps 12
```
`make_ollama` uses `/api/chat`, 8192 ctx, temp 0.15, num_predict 350.
`make_ollama_generate` for base GGUFs (MNN-Pocket 0.3B).

## Tiny models tested
| model | size | ok? |
|-------|------|-----|
| smollm2:135m | 92MB | fragile loops |
| Pocket 0.3B | 191MB | partial |
| qwen2.5:0.5b | 397MB | yes, rec min |
| qwen2.5:1.5b | 986MB | yes, best |

## files
- `prompt.py` 89 tok prompt
- `tools.py` 5 tools truncated
- `harness.py` JSON extract + pruning
- `llms.py` ollama chat/generate
- `tui.py` Claude/opencode TUI (new)
- `cli.py` --tui / --no-tui
- `examples/ollama_tiny.py` python example
- `examples/minimal_loop.py` 40-line zero-dep
