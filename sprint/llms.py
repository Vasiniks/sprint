"""pluggable backends - all return str (raw JSON action)"""
import json


def make_ollama(model="smollm2:135m-instruct-q4_0", host="http://localhost:11434", use_tools=False, think=False):
    """
    Optimized for tiny ctx models. Uses chat API.
    use_tools=True needs model that supports tools (qwen2.5+).
    """
    import httpx

    def fn(msgs: list[dict]) -> str:
        payload: dict = {
            "model": model,
            "messages": msgs,
            "stream": False,
            "options": {
                "num_ctx": 8192,
                "temperature": 0.15,
                "num_predict": 350,
                "top_p": 0.8,
            },
        }
        if not think:
            payload["think"] = False

        if use_tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": "read", "description": "read file or list dir", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
                {"type": "function", "function": {"name": "write", "description": "write file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
                {"type": "function", "function": {"name": "shell", "description": "run bash cmd", "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}},
                {"type": "function", "function": {"name": "fetch", "description": "fetch url", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
                {"type": "function", "function": {"name": "done", "description": "finish", "parameters": {"type": "object", "properties": {"result": {"type": "string"}}, "required": ["result"]}}},
            ]

        r = httpx.post(f"{host}/api/chat", json=payload, timeout=90)
        r.raise_for_status()
        data = r.json()

        if data.get("message", {}).get("tool_calls"):
            tc = data["message"]["tool_calls"][0]
            name = tc["function"]["name"]
            args = tc["function"]["arguments"]
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"_raw": args}
            return json.dumps({"tool": name, "args": args})

        return data.get("message", {}).get("content", "") or data.get("response", "")

    return fn


def make_ollama_generate(model="smollm2:135m-instruct-q4_0", host="http://localhost:11434"):
    """fallback for base/completion-only GGUFs"""
    import httpx

    def fn(msgs: list[dict]) -> str:
        prompt = ""
        for m in msgs:
            role = m["role"]
            if role == "system":
                prompt += f"[SYSTEM] {m['content']}\n"
            elif role == "user":
                prompt += f"[USER] {m['content']}\n"
            else:
                prompt += f"[ASSIST] {m['content']}\n"
        prompt += "[ASSIST]"
        r = httpx.post(
            f"{host}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {"num_ctx": 8192, "temperature": 0.2, "num_predict": 300, "stop": ["[USER]", "[SYSTEM]"]},
            },
            timeout=90,
        )
        r.raise_for_status()
        return r.json()["response"]

    return fn


def make_openai_compat(model="Qwen/Qwen2.5-0.5B-Instruct", base_url="http://localhost:8000/v1", api_key="x"):
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key)

    def fn(msgs: list[dict]) -> str:
        res = client.chat.completions.create(model=model, messages=msgs, temperature=0.15, max_tokens=350)
        return res.choices[0].message.content or ""

    return fn


def make_fake(steps=None):
    steps = steps or [
        '{"tool":"shell","args":{"cmd":"ls -la"}}',
        '{"tool":"read","args":{"path":"README.md"}}',
        '{"tool":"done","args":{"result":"ok fake run"}}',
    ]
    it = iter(steps)

    def fn(_: list[dict]) -> str:
        try:
            return next(it)
        except StopIteration:
            return '{"tool":"done","args":{"result":"done"}}'

    return fn
