import json, time, re
from typing import List, Dict, Callable

from .prompt import SYSTEM, build_tools_hint
from .tools import dispatch

# --- 8K context management ---
MAX_CTX_CHARS = 6000        # ~1500 tokens for history
MAX_STEPS = 30
KEEP_LAST = 6              # only keep last N turns for tiny ctx

JSON_RE = re.compile(r'\{.*\}', re.DOTALL)

def extract_json(s: str) -> dict:
    """robust json extract for tiny models that add noise"""
    s = s.strip()
    # direct
    try:
        return json.loads(s)
    except:
        pass
    # find first {...} block
    m = JSON_RE.search(s)
    if m:
        for chunk in [m.group(0), s[s.find('{'):s.rfind('}')+1]]:
            try:
                return json.loads(chunk)
            except:
                continue
    # fallback: try to fix single quotes
    try:
        return json.loads(s.replace("'", '"'))
    except:
        raise ValueError(f"no JSON in: {s[:200]}")

def prune_history(hist: List[Dict], max_chars=MAX_CTX_CHARS, keep_last=KEEP_LAST) -> List[Dict]:
    """keep system+task, drop middle, keep tail. aggressive truncation for 8K."""
    if len(hist) <= keep_last + 1:
        return hist
    # always keep first (system+task)
    head = hist[:1]
    tail = hist[-keep_last:]
    # estimate chars
    total = sum(len(str(m.get("content",""))) for m in tail)
    # if still too big, truncate tool outputs
    if total > max_chars:
        for m in tail:
            if isinstance(m.get("content"), str) and len(m["content"]) > 800:
                m["content"] = m["content"][:800] + "\n...[cut]"
    return head + [{"role":"user","content":f"[pruned {len(hist)-len(head)-len(tail)} steps]"}] + tail

class Sprint:
    def __init__(self, llm_fn: Callable[[List[Dict]], str], max_steps=MAX_STEPS, verbose=True):
        """
        llm_fn: (messages: List[dict]) -> str  returns model text (should be JSON)
        messages: OpenAI style [{"role":...,"content":...}]
        """
        self.llm_fn = llm_fn
        self.max_steps = max_steps
        self.verbose = verbose

    def run(self, task: str) -> str:
        msgs: List[Dict] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"TASK: {task}\n{build_tools_hint()}"},
        ]
        for step in range(self.max_steps):
            pruned = prune_history(msgs)
            raw = self.llm_fn(pruned)
            if self.verbose:
                print(f"\n[{step}] LLM: {raw[:300]}")

            try:
                act = extract_json(raw)
                tool = act.get("tool")
                args = act.get("args", {})
            except Exception as e:
                feedback = f"ERR: must output JSON. got: {raw[:200]} error: {e}"
                msgs.append({"role":"assistant","content":raw})
                msgs.append({"role":"user","content":feedback})
                continue

            if tool == "done":
                return args.get("result","done")

            # exec
            msgs.append({"role":"assistant","content":json.dumps(act)})
            out = dispatch(tool, args)
            if self.verbose:
                print(f" -> {tool} => {out[:200]}")

            if out == "DONE":
                return args.get("result","")

            msgs.append({"role":"user","content":f"TOOL {tool} OUTPUT:\n{out}\nNext JSON:"})

        return "stopped: max steps"

def run(task: str, llm_fn: Callable, **kw) -> str:
    return Sprint(llm_fn, **kw).run(task)
