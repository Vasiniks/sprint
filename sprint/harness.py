import json
import re
from typing import Callable

from .prompt import SYSTEM
from .tools import dispatch

MAX_CTX_CHARS = 5500
MAX_STEPS = 25
KEEP_LAST = 5


def _try_json(s: str):
    """try strict, then single-quote fix"""
    try:
        j = json.loads(s)
        if isinstance(j, dict) and "tool" in j:
            return j
    except Exception:
        pass
    try:
        fixed = s.replace("'", '"')
        j = json.loads(fixed)
        if isinstance(j, dict) and "tool" in j:
            return j
    except Exception:
        pass
    return None


def extract_all_json(s: str) -> list[dict]:
    """extract all JSON tool calls - handles nested braces and single quotes"""
    s = re.sub(r'<think>.*?</think>', '', s, flags=re.DOTALL)
    s = re.sub(r'```json|```', '', s).strip()
    results: list[dict] = []

    # Find candidate JSON objects by scanning for { and balancing
    i = 0
    while i < len(s):
        if s[i] != '{':
            i += 1
            continue
        # balance count
        depth = 0
        start = i
        in_string = False
        escape = False
        for j in range(i, min(i + 1000, len(s))):  # limit 1000 chars per object
            ch = s[j]
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"' and not in_string:
                # check if single-quoted string start? ignore
                in_string = True
                string_char = '"'
                continue
            if ch == "'" and not in_string:
                # allow single-quoted strings as potential JSON
                # treat as string delimiter for balancing purposes
                in_string = True
                string_char = "'"
                continue
            if in_string:
                if ch == string_char:
                    in_string = False
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    candidate = s[start : j + 1]
                    parsed = _try_json(candidate)
                    if parsed:
                        results.append(parsed)
                    i = j  # move past this object
                    break
        i += 1

    return results


def prune_history(hist: list[dict], max_chars=MAX_CTX_CHARS, keep_last=KEEP_LAST) -> list[dict]:
    if len(hist) <= keep_last + 2:
        return hist
    head = hist[:2]
    tail = hist[-keep_last:]
    for m in tail:
        c = m.get("content", "")
        if len(c) > 700:
            m["content"] = c[:700] + "...[cut]"
    return head + [{"role": "user", "content": f"[pruned {len(hist)-len(head)-len(tail)}]"}] + tail


class Sprint:
    def __init__(self, llm_fn: Callable[[list[dict]], str], max_steps=MAX_STEPS, verbose=True):
        self.llm_fn = llm_fn
        self.max_steps = max_steps
        self.verbose = verbose
        self.seen: set[str] = set()

    def run(self, task: str) -> str:
        msgs: list[dict] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"TASK:{task}\nJSON only."},
        ]
        for step in range(self.max_steps):
            pruned = prune_history(msgs)
            raw = self.llm_fn(pruned)
            if self.verbose:
                print(f"\n[{step}] {raw[:500]}")

            actions = extract_all_json(raw)
            if not actions:
                msgs.append({"role": "assistant", "content": raw})
                msgs.append({"role": "user", "content": 'ERR need JSON like {"tool":"shell","args":{"cmd":"ls"}}'})
                continue

            key = f"{actions[0].get('tool')}:{str(actions[0].get('args'))}"
            self.seen.add(key)

            executed = False
            for act in actions[:2]:
                tool = act.get("tool", "").strip()
                args = act.get("args", {})
                if not isinstance(args, dict):
                    continue
                if tool == "done":
                    return args.get("result", "done")

                msgs.append({"role": "assistant", "content": json.dumps(act)})
                out = dispatch(tool, args)
                if self.verbose:
                    print(f"  -> {tool} {args} => {out[:300]}")
                if out == "DONE":
                    return args.get("result", "")
                msgs.append({"role": "user", "content": f"OUT:{out}\nNext JSON:"})
                executed = True
                if tool == "write" and "OK wrote" in out:
                    self.seen.clear()

            if not executed:
                msgs.append({"role": "user", "content": "ERR no valid tool"})
        return "stop:max steps"
