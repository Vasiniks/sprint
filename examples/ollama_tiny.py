"""Ollama example - 1.5B tiny agent doing real work"""
from sprint.llms import make_ollama
from sprint.harness import Sprint

# Use local Ollama - no API key needed
llm = make_ollama(
    model="hf.co/ElementMerc/qwen2.5-1.5b-abliterated-GGUF:Q4_K_M", # or qwen2.5:0.5b
    host="http://localhost:11434",
    use_tools=False,  # False for most tiny models (JSON mode)
)

bot = Sprint(llm, max_steps=15, verbose=True)
result = bot.run("""
1. Use shell to list python files in current dir
2. Read README.md
3. Create REPORT.md summarizing repo in 3 bullets
""")
print("\nDONE:", result)
