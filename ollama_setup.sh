#!/bin/bash
# Setup Ollama for tiny models
set -e
if ! command -v ollama &> /dev/null; then
  echo "install ollama from https://ollama.com"
  curl -fsSL https://ollama.com/install.sh | sh
fi
echo "pulling tiny models..."
ollama pull qwen2.5:0.5b
ollama pull qwen2.5:1.5b
echo "testing..."
curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; print('\n'.join(m['name'] for m in json.load(sys.stdin)['models']))"
echo "ok - run: sprint 'ls and write hi.txt' --model qwen2.5:0.5b"
