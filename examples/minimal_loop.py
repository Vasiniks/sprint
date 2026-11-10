"""single file, zero-dep copy-paste minimal loop - for tiny models"""
import json, subprocess, pathlib, re, sys

SYSTEM = """You are tiny agent. Tools: read(path), write(path,content), shell(cmd), fetch(url), done(result)
One JSON per turn, no other text: {"tool":"name","args":{}}
"""
MAX_OUT = 1200
def tool_read(path):
    p=pathlib.Path(path)
    return (p.read_text()[:MAX_OUT] if p.is_file() else "\n".join(f.name for f in p.iterdir())[:MAX_OUT]) if p.exists() else "ERR not found"
def tool_write(path,content):
    p=pathlib.Path(path); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content); return f"WROTE {path}"
def tool_shell(cmd):
    r=subprocess.run(cmd,shell=True,capture_output=True,text=True,timeout=10)
    return (r.stdout+r.stderr)[:MAX_OUT]
TOOLS={"read":tool_read,"write":tool_write,"shell":tool_shell,"done":None}

def loop(llm_call, task, max_steps=15):
    hist=f"{SYSTEM}\nTASK: {task}\n"
    for _ in range(max_steps):
        raw = llm_call(hist)  # your function (messages->str)
        try:
            act = json.loads(re.search(r'\{.*\}', raw, re.DOTALL).group(0))
        except:
            hist+="\nERR: output JSON only\n"; continue
        if act["tool"]=="done":
            return act["args"]["result"]
        fn = TOOLS.get(act["tool"])
        out = fn(**act["args"]) if fn else f"ERR unknown {act['tool']}"
        hist+=f"\nASSIST: {act}\nTOOL OUT: {out[:800]}\n"
        if len(hist)>6000: hist=hist[:1000]+"\n[pruned]\n"+hist[-5000:]
    return "max steps"

# usage:
# def my_llm(hist: str) -> str:
#     return openai_client.completions.create(model="tiny", prompt=hist).choices[0].text
# loop(my_llm, "write hello.py")
