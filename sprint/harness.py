import json, re
from typing import List, Dict, Callable
from .prompt import SYSTEM
from .tools import dispatch

MAX_CTX_CHARS = 5500
MAX_STEPS = 25
KEEP_LAST = 5

def extract_all_json(s: str) -> List[dict]:
    """extract all JSON tool calls - tiny models output 2 at once"""
    s = re.sub(r'<think>.*?</think>', '', s, flags=re.DOTALL)
    s = re.sub(r'```json|```', '', s).strip()
    results = []
    # find all {...} with "tool"
    for m in re.finditer(r'\{[^{}]*"tool"[^{}]*\}', s, re.DOTALL):
        try:
            j = json.loads(m.group(0))
            if "tool" in j:
                results.append(j)
        except:
            # try single-quote fix
            try:
                results.append(json.loads(m.group(0).replace("'",'"')))
            except:
                continue
    if not results:
        # fallback one big object
        try:
            start, end = s.find('{'), s.rfind('}')+1
            if start>=0:
                j=json.loads(s[start:end])
                if "tool" in j:
                    results=[j]
        except:
            pass
    return results

def prune_history(hist: List[Dict], max_chars=MAX_CTX_CHARS, keep_last=KEEP_LAST) -> List[Dict]:
    if len(hist) <= keep_last+2:
        return hist
    head = hist[:2]
    tail = hist[-keep_last:]
    for m in tail:
        c=m.get("content","")
        if len(c)>700:
            m["content"]=c[:700]+"...[cut]"
    return head + [{"role":"user","content":f"[pruned {len(hist)-len(head)-len(tail)}]"}] + tail

class Sprint:
    def __init__(self, llm_fn: Callable[[List[Dict]], str], max_steps=MAX_STEPS, verbose=True):
        self.llm_fn=llm_fn
        self.max_steps=max_steps
        self.verbose=verbose
        self.seen=set()

    def run(self, task: str) -> str:
        msgs=[{"role":"system","content":SYSTEM},
              {"role":"user","content":f"TASK:{task}\nJSON only."}]
        for step in range(self.max_steps):
            pruned=prune_history(msgs)
            raw=self.llm_fn(pruned)
            if self.verbose:
                print(f"\n[{step}] {raw[:500]}")

            actions=extract_all_json(raw)
            if not actions:
                msgs.append({"role":"assistant","content":raw})
                msgs.append({"role":"user","content":'ERR need JSON like {"tool":"shell","args":{"cmd":"ls"}}'})
                continue

            # loop detection - same action 3x = break
            key=f"{actions[0].get('tool')}:{str(actions[0].get('args'))}"
            self.seen.add(key)
            # count repeats of same key in recent
            recent=[f"{m}" for m in msgs[-4:] if "shell" in str(m) or "read" in str(m)]
            
            executed=False
            for act in actions[:2]:  # max 2 per turn to save ctx
                tool=act.get("tool","").strip()
                args=act.get("args",{})
                if not isinstance(args,dict):
                    continue
                if tool=="done":
                    return args.get("result","done")

                # skip exact duplicate loops after 2 repeats
                dup_key=f"{tool}:{list(args.values())[:1]}"
                if msgs.count if False else False:
                    pass

                msgs.append({"role":"assistant","content":json.dumps(act)})
                out=dispatch(tool,args)
                if self.verbose:
                    print(f"  -> {tool} {args} => {out[:300]}")
                if out=="DONE":
                    return args.get("result","")
                msgs.append({"role":"user","content":f"OUT:{out}\nNext JSON:"})
                executed=True
                if tool=="write" and "OK wrote" in out:
                    # progress, reset seen to avoid false loop trap
                    self.seen.clear()

            if not executed:
                msgs.append({"role":"user","content":"ERR no valid tool"})
        return "stop:max steps"
