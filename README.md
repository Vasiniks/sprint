# sprint

A terminal coding agent for small local models. Two Python files, no dependencies.

<p align="center"><img src="assets/logo.svg" alt="sprint: the opening screen, with the logo above the prompt" width="760"></p>

Small models are fast and private, but they lose track of long conversations, garble tool calls and go in circles. sprint is built around those failings: it keeps the context small, checks every tool call, stops loops, and shows you each action before it runs.

## Start

You need Python 3.9 or newer and [Ollama](https://ollama.com).

| system | status |
|---|---|
| macOS | Works; built and tested here. |
| Linux, WSL | Should work: same code paths as macOS. The tests run on Ubuntu in CI. |
| Windows | Written but not yet tried on a real machine. Commands run in Git Bash when Git for Windows is installed, otherwise in PowerShell. `pip install` adds `windows-curses` for the full screen. A CI job runs the Windows-only tests. |

```bash
pip install -e .
sprint
```

- If Ollama isn't running, sprint starts it and leaves it running.
- If the model isn't installed, sprint offers to pull it.
- The first model is Qwen 3.5 0.8B at 4-bit (`hf.co/unsloth/Qwen3.5-0.8B-GGUF:Q4_K_M`, 737 MB). After that it opens with whichever model you used last.

Type a request and press Enter. In the default mode sprint asks before it runs a command or changes a file.

## The screen

| part | what it shows |
|---|---|
| Messages (left) | Your messages in order, each with what it took. Click one to jump to it; what you jump to lights up for a moment. |
| Conversation (middle) | Your messages on a shaded band, the model's replies, and one window per tool call. |
| Sidebar (right) | The context window in small, the model's speed, then a log of every tool call and how it ended. |
| Prompt (bottom) | The line you type on, between two rules: the model and context above it, the mode below it. |
| Status (above the prompt) | While a turn runs: a small sprint animation, what is happening, for how long, tokens going up or coming down, and the effort. |

Tool windows start collapsed to a few lines, and each says how long the call took:

```
╭─ ▸ shell  python3 hello.py && seq 1 12 ─────────── done 0.1s ─╮
│ $ python3 hello.py && seq 1 12                                │
│ hi                                                            │
╰─ … +11 lines · ctrl+o ────────────────────────────────────────╯
╭─ ▾ edit  hello.py  +1 -1 ───────────────────────── done 0.0s ─╮
│ - print('hi')                                                 │
│ + print('hello world')                                        │
╰───────────────────────────────────────────────────────────────╯
```

Drag the line beside either side panel to resize it. A panel that would squeeze the conversation below 44 columns hides itself.

## Writing a message

| you type | what happens |
|---|---|
| `@notes.txt` | That file is attached to the message, so the model doesn't have to go and read it. Tab completes the name. |
| `!git status` | The command runs yourself: no model, no approval. Its output joins the conversation. |
| a pasted or dragged file | It becomes a chip: `[Image #1]`, `[PDF #1]` or `[File #1]`. Pictures go to the model as pictures, other files as text. |
| a large paste | It becomes a chip too, such as `[Pasted #1 +42 lines]`, so the prompt stays readable. The model gets the full text. |
| `ctrl+v`, `alt+v` or `/paste` | The picture on the clipboard (a screenshot, or a copied file) is attached as `[Image #1]`. Terminals paste text only, so sprint asks the system. In Windows Terminal use `alt+v` or `/paste`. |
| a message while it works | It waits in a queue shown above the prompt and runs when the turn ends. |
| `/` | The command list, with completion. |

## Keys

| key | |
|---|---|
| `enter` | send; while a turn runs, queue the message; on an empty prompt with messages queued, stop the turn and run them now |
| `shift+enter`, `alt+enter`, `ctrl+j`, or `\` then `enter` | a new line (shift+enter needs a terminal that reports it; on Windows use `\` then `enter`) |
| `tab`, `shift+tab` | switch mode: ask → edits → auto |
| `esc` | stop the turn at once (hangs up on the model, kills a running command); queued messages go back on the prompt |
| `ctrl+o` | expand or collapse all tool windows; click one to toggle just it |
| `ctrl+y` | copy the last answer |
| `ctrl+l` | clear the conversation |
| mouse wheel, `pgup`, `pgdn` | scroll |
| `up`, `down` | recall earlier messages |
| `ctrl+c` | clear the line, else stop the turn; twice on an empty prompt to quit |

Drag over text in the conversation to copy it. `/mouse` hands the mouse back to the terminal if you prefer its own selection.

## Modes and permissions

| mode | runs without asking |
|---|---|
| `ask` (default) | reading files |
| `edits` | reading, writing and editing files |
| `auto` | everything |

When a call needs approval, the prompt asks: `y` once, `a` always, `n` no. "Always" allows just that command or file from then on (a fetch allows the whole site). `/permissions` lists those rules, removes them, and adds patterns such as `shell git status*`.

## Commands

Anything a command brings up opens in the same popup, over a greyed-out screen: arrows or the mouse move, Enter or a click chooses, Esc or a click outside closes. A command given its value (`/mode auto`, `/effort high`) applies it and says so in the footer.

| command | |
|---|---|
| `/model [name]` | Pick an installed model, pull one by name, or search Hugging Face and pick a quantization. |
| `/race [model message]` | Send one message to two models and compare answers, time and speed. They may read files but change nothing. |
| `/mode`, `/permissions` | Change the mode; manage the always-allowed list. |
| `/think` | Let the model reason before answering. |
| `/showthink` | Show that reasoning in full. Otherwise it is one line saying the model thought; click one to open it. |
| `/effort low\|medium\|high` | How much room a reply always has, and how many steps a message may take (8, 16 or 32). |
| `/plan` | Split each message into a short list of steps first. Off by default. |
| `/context` | What is filling the context window, as a coloured grid. |
| `/compact` | Fold the older conversation into a short summary. sprint also does this by itself. |
| `/copy [code\|all]` | Copy the last answer, the last code block, or everything. |
| `/paste` | Attach the picture on the clipboard. |
| `/export [file]` | Save the conversation as markdown. |
| `/directory [path]` | Change the folder sprint works in, with a small folder browser. |
| `/systemprompt` | Edit what the model is told at the start. |
| `/theme` | default, mono, amber, forest, ocean, sunset, midnight, cmyk, matrix, slate, grape, paper. A theme sets the whole screen, background included; the list previews each in its own colours. |
| `/config [key value]` | Every setting in one list. |
| `/sidebar`, `/history` | Hide, show or size the side panels. |
| `/tools`, `/mouse` | Expand or collapse all tool windows; turn mouse capture on or off. |
| `/log` | The last things that happened, and where the full log is. |
| `/clear` (or `/new`), `/help`, `/exit` | Start fresh, help, quit. |

## Settings and files

Everything sprint keeps lives in `~/.config/sprint/` (`%APPDATA%\sprint` on Windows), as plain files you can edit by hand:

| file | |
|---|---|
| `config.json` | settings: model, url, context size, steps, mode, effort, thinking, theme, timeout, panel sizes, allow list, recent folders |
| `system.md` | your own system prompt, if you wrote one |
| `sprint.log` | messages, tool calls and errors, so there is something to read when a run goes wrong |

Command-line options override the environment, which overrides saved settings.

| option | |
|---|---|
| `sprint "fix the typo in README"` | start with a first message |
| `-m NAME`, `SPRINT_MODEL` | model |
| `-u URL`, `SPRINT_URL` | Ollama URL, or any OpenAI-compatible URL ending in `/v1`; `SPRINT_KEY` for a bearer token |
| `-c 8192` | context window in tokens |
| `--mode edits`, `-y` | starting mode; `-y` is auto |
| `--plain` | a line-by-line session instead of the full screen (also used when output is piped) |

## How it copes with small models

**Tool calls.** On Ollama the five tools (`shell`, `read`, `write`, `edit`, `fetch`) are offered as native tool schemas. Models without tool support fall back automatically to fenced blocks (` ```shell `, ` ```write path `), which need no JSON escaping.

**A short prompt that names the files.** The system prompt lists the folder's contents. Without that, a 0.8B model often claims it cannot see a file instead of reading it.

**Context.**
- An eighth of the window is always kept free for the reply; a reply may use the free room up to half the window.
- Tool results are capped: head and tail are kept, `read` pages through long files, `fetch` strips pages to text.
- When the conversation outgrows the window, the older part is folded into a short summary of what you asked and what was done. File changes outlast things that were only looked at.
- If a reply itself runs out of room, sprint compacts, then lets a plain answer continue or gives a half-written tool call another go.
- Token counts are corrected from what the server reports, only ever toward the safe side.

**Guardrails.**
- Generation is cut the moment a model talks past its tool call.
- A reply cut off by the token limit is never run.
- Going in circles gets one warning and then a stop: the same call again, the same call and result as before, the same file rewritten twice running, or the same failure twice running.
- A model that gives up with "I can't access that file", or shows code without saving it, gets one push to use its tools.

**Speed.** sprint asks Ollama to load the model as soon as it opens and to keep it loaded for 30 minutes. The screen is redrawn only when something changes.

## What to expect

Measured on six one- and two-step tasks (create and run a file, read a line, edit a word, count lines, two edits), eight tries each, through sprint:

| model | passed | speed |
|---|---|---|
| magi 1.2 2B (`hf.co/GEHIRNS/magi-1.2-2B`, 826 MB) | 42 of 48 | 75 tok/s |
| Qwen 3.5 0.8B Q4_K_M (737 MB) | 25 of 48 | 122 tok/s |

Results swing with small changes in wording and in what else is in the folder, so treat these as rough. Review what the model does: `ask` mode shows every command and diff before it runs.

Known limits:
- **Long-running commands.** A command is killed after 60 seconds (`/config timeout`), so a server started by the model does not stay up.
- **Long files.** One reply can hold a file of a few hundred lines at 8K. Beyond that, ask for parts or raise the window with `/config ctx 16384`.
- **Plan mode** did not help the 0.8B model in testing, which is why it is off by default.
- **PDFs** are read roughly unless `pdftotext` is installed. Pictures need a model that can see.
- **Windows.** Install Git for Windows: models write bash, and sprint uses Git Bash when it finds it. Without it, commands run in PowerShell, where common ones (`ls`, `cat`) work but bash syntax often does not. The terminal there does not mark pastes, so sprint treats keys that arrive together as one paste. Dragging with the mouse is untested there; a picture or PDF path dropped into the prompt still becomes a chip when you send.

## As a library

```python
from sprint import Agent

answer = Agent(mode="auto").run("count the lines in notes.txt")
```

## Development

```bash
python -m pytest
```

| file | |
|---|---|
| `sprint.py` | the agent: tools, context handling, model backends, the plain session |
| `sprint_tui.py` | the full-screen interface (stdlib `curses`) |
| `test_sprint.py`, `test_tui.py`, `test_prompt.py`, `test_popups.py`, `test_showthink.py` | tests; they use a scripted model and never touch your settings |
| `test_windows.py` | the Windows-only paths, run on Windows in CI and skipped elsewhere |
| `assets/logo.svg` | the logo as the opening screen, animated; drawn from the same pixel font as the app |

## License

MIT. See `LICENSE`.
