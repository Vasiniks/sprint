import pathlib
import subprocess

import httpx

MAX_OUT = 1200


def _trunc(s: str) -> str:
    if len(s) > MAX_OUT:
        return s[: MAX_OUT - 50] + "\n...[truncated]"
    return s


def tool_read(path: str) -> str:
    try:
        p = pathlib.Path(path)
        if p.is_dir():
            items = [f.name for f in p.iterdir()][:100]
            return _trunc("\n".join(items))
        return _trunc(p.read_text(errors="ignore"))
    except Exception as e:
        return f"ERR: {e}"


def tool_write(path: str, content: str) -> str:
    try:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"OK wrote {len(content)}b to {path}"
    except Exception as e:
        return f"ERR: {e}"


def tool_shell(cmd: str, timeout: int = 15) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, check=False)
        out = (r.stdout + "\n" + r.stderr).strip()
        if r.returncode != 0:
            out = f"[exit {r.returncode}]\n" + out
        return _trunc(out or "(no output)")
    except subprocess.TimeoutExpired:
        return "ERR: timeout"
    except Exception as e:
        return f"ERR: {e}"


def tool_fetch(url: str) -> str:
    try:
        with httpx.Client(follow_redirects=True, timeout=10) as c:
            r = c.get(url)
            r.raise_for_status()
            return _trunc(r.text)
    except Exception as e:
        return f"ERR fetch: {e}"


TOOLS = {
    "read": {"fn": tool_read, "desc": "read file/ls dir: {path}"},
    "write": {"fn": tool_write, "desc": "write file: {path, content}"},
    "shell": {"fn": tool_shell, "desc": "bash cmd: {cmd}"},
    "fetch": {"fn": tool_fetch, "desc": "http get: {url}"},
    "done": {"fn": None, "desc": "finish task: {result}"},
}


def dispatch(tool: str, args: dict) -> str:
    if tool not in TOOLS:
        return f"ERR unknown tool {tool}. use: {','.join(TOOLS)}"
    if tool == "done":
        return "DONE"
    try:
        return TOOLS[tool]["fn"](**args)  # type: ignore
    except TypeError as e:
        return f"ERR bad args for {tool}: {e}"
