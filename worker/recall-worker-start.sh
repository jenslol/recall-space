#!/usr/bin/env bash
# recall-worker-start.sh — Start the AI worker service
set -euo pipefail

WORKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$WORKER_DIR"

# RX 7900 GRE is gfx1100 family — set this for ROCm compatibility
export HSA_OVERRIDE_GFX_VERSION="${HSA_OVERRIDE_GFX_VERSION:-11.0.0}"

# Optional: reduce SDMA issues on some AMD cards
export HSA_ENABLE_SDMA="${HSA_ENABLE_SDMA:-0}"

# Ollama config
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.1:8b-instruct-q4_K_M}"

# Whisper config
export WHISPER_MODEL="${WHISPER_MODEL:-medium}"
export WHISPER_DEVICE="${WHISPER_DEVICE:-cpu}"
export WHISPER_COMPUTE="${WHISPER_COMPUTE:-int8}"

# Ensure Ollama is running
if ! pgrep -x ollama &>/dev/null; then
    echo "[→] Starting Ollama..."
    ollama serve &>/dev/null &
    sleep 3
fi

# Activate venv
source "$WORKER_DIR/.venv/bin/activate"

echo "=== Recall Space AI Worker ==="
echo "Ollama:  $OLLAMA_URL ($OLLAMA_MODEL)"
echo "Whisper: $WHISPER_MODEL (device=$WHISPER_DEVICE, compute=$WHISPER_COMPUTE)"
echo "Listen:  http://0.0.0.0:8401"
echo ""

exec uvicorn worker:app --host 0.0.0.0 --port 8401
