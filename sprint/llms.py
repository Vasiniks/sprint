"""pluggable backends - all return str"""
import os
from typing import List, Dict

def make_ollama(model="qwen2.5:0.5b", host="http://localhost:11434"):
    import httpx
    def fn(msgs: List[Dict]) -> str:
        prompt = "\n".join(f"{m['role']}: {m['content']}" for m in msgs)
        r = httpx.post(f"{host}/api/generate", json={
            "model": model, "prompt": prompt, "stream": False,
            "options": {"num_ctx": 8192, "temperature": 0.2, "stop": ["TOOL","USER:","SYSTEM:"]}
        }, timeout=60)
        r.raise_for_status()
        return r.json()["response"]
    return fn

def make_openai_compat(model="Qwen/Qwen2.5-0.5B-Instruct", base_url="http://localhost:8000/v1", api_key="x"):
    from openai import OpenAI
    client = OpenAI(base_url=base_url, api_key=api_key)
    def fn(msgs: List[Dict]) -> str:
        res = client.chat.completions.create(
            model=model, messages=msgs, temperature=0.2, max_tokens=400
        )
        return res.choices[0].message.content or ""
    return fn

def make_llamacpp(model_path: str):
    from llama_cpp import Llama
    llm = Llama(model_path=model_path, n_ctx=8192, verbose=False)
    def fn(msgs: List[Dict]) -> str:
        prompt = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in msgs) + "\nASSISTANT:"
        out = llm(prompt, max_tokens=350, temperature=0.2, stop=["\nUSER:","TOOL ","<|im_end|>"])
        return out["choices"][0]["text"].strip()
    return fn

def make_fake(steps=None):
    """for testing without model"""
    steps = steps or [
        '{"tool":"shell","args":{"cmd":"ls"}}',
        '{"tool":"read","args":{"path":"README.md"}}',
        '{"tool":"done","args":{"result":"ok fake run"}}',
    ]
    it = iter(steps)
    def fn(_: List[Dict]) -> str:
        try:
            return next(it)
        except StopIteration:
            return '{"tool":"done","args":{"result":"done"}}'
    return fn
