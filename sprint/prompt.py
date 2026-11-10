# 180 tokens - optimized for 0.5B-3B models
SYSTEM = """You are a tiny coding agent.

TOOLS:
read(path) - read file or ls dir
write(path,content) - write file
shell(cmd) - run bash
fetch(url) - get url
done(result) - finish

RULES:
1. One JSON per turn: {"tool":"name","args":{...}}
2. No text outside JSON.
3. Keep args small. Prefer shell for exploring.
4. After 1-2 reads, ACT.

EXAMPLE:
{"tool":"shell","args":{"cmd":"ls -R | head -n 30"}}
{"tool":"read","args":{"path":"main.py"}}
{"tool":"write","args":{"path":"fix.py","content":"print('ok')"}}
{"tool":"done","args":{"result":"done"}}
"""

def build_tools_hint() -> str:
    return "TOOLS: read, write, shell, fetch, done. Reply JSON only.\n"

# For tool-calling models (optional)
OPENAI_TOOLS = [
    {"type":"function","function":{"name":"read","description":"read file or list dir","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
    {"type":"function","function":{"name":"write","description":"write/create file","parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}}},
    {"type":"function","function":{"name":"shell","description":"run bash command","parameters":{"type":"object","properties":{"cmd":{"type":"string"}},"required":["cmd"]}}},
    {"type":"function","function":{"name":"fetch","description":"fetch url text","parameters":{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}}},
    {"type":"function","function":{"name":"done","description":"finish task","parameters":{"type":"object","properties":{"result":{"type":"string"}},"required":["result"]}}},
]
