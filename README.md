# sprint — tiny 8K harness + Claude/OpenCode TUI

Tiny agent harness for 135M-3B models (89 tok prompt, 5 tools) with full-screen TUI.

## commands
```bash
sprint                # welcome dashboard TUI (files + sessions + task input)
sprint help           # help
sprint list [path]    # ls files (like opencode: list)
sprint ls src/        # alias
sprint ps             # running agents (pid + task)
sprint kill <id|pid>  # kill agent
sprint init           # scaffold TASK.md
sprint "your task"    # run agent (auto TUI)
sprint --no-tui "task" --backend fake  # plain logs for CI
```

## TUI v2 (fixed)
**Welcome (`sprint`):**
- header: `◉ sprint | model | cwd | branch`
- left sidebar (32 cols):
  - FILES `📁 📄` live list (refresh on /list or after write)
  - SESSIONS live `ps` (● alive / ○ dead) with id/model/pid
  - help hints: `q quit c clear f files p ps ? help`
- main: log + task input
  - input supports `/list [path] /ps /kill <id> /clear /model <name> /help`

**Agent TUI (`sprint "task"`):**
- header: cwd + git branch + model + steps + ctx usage
- sidebar: files + sessions (auto-refresh every 2s on write)
- main: transcript `● tool(args)` → `└─ output`, panels for DONE
- bottom input: new task or `/command`
- status bar: `step x/y | ctx n/5500 | sid`

Slash commands in both TUIs mimic opencode/claude:
- `/list [path]` — refresh file tree + list in log
- `/ps` — show sessions + refresh sidebar
- `/kill <id>` — kills session (uses ~/.sprint/sessions pid files)
- `/clear`, `/model <name>`, `/help`, `/quit`

Sessions tracked in `~/.sprint/sessions/*.json` with pid, task, model, started. `ps` checks alive via `kill -0`, `kill` SIGTERM→SIGKILL.

## install
```bash
pip install -e .   # httpx, rich, textual, psutil
ollama pull qwen2.5:0.5b
ollama pull qwen2.5:1.5b  # best tiny
```

## Ollama (tested)
```bash
sprint "ls and write ok.txt" --model qwen2.5:1.5b --steps 12
```
`make_ollama` uses `/api/chat`, 8192 ctx, 0.15 temp. `make_ollama_generate` for base GGUFs.

## structure
- `prompt.py` 89 tok
- `tools.py` 5 tools
- `harness.py` JSON multi-extract + pruning
- `state.py` NEW session register/ps/kill
- `tui.py` NEW v2 full-screen with sidebar + slash
- `welcome.py` NEW dashboard + CLI list/ps/kill
- `cli.py` aliases: sprint -> dashboard, sprint help/list/ps/kill/init
