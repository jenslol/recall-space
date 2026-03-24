#!/usr/bin/env bash
# recall-worker-setup.sh — Setup the AI worker on CachyOS gaming PC
# Run this once to install everything, then use recall-worker-start.sh
set -euo pipefail

echo "=== Recall Space AI Worker Setup ==="
echo ""

# -------------------------------------------------------------------------
# 1. Check/install Ollama
# -------------------------------------------------------------------------
if command -v ollama &>/dev/null; then
    echo "[✓] Ollama already installed: $(ollama --version 2>&1 || echo 'unknown')"
else
    echo "[→] Installing Ollama..."
    curl -fsSL https://ollama.ai/install.sh | sh
    echo "[✓] Ollama installed"
fi

# Start Ollama if not running
if ! pgrep -x ollama &>/dev/null && ! systemctl is-active --quiet ollama 2>/dev/null; then
    echo "[→] Starting Ollama service..."
    ollama serve &>/dev/null &
    sleep 3
fi

# Pull the model
echo "[→] Pulling LLM model (llama3.1:8b-instruct-q4_K_M)..."
ollama pull llama3.1:8b-instruct-q4_K_M
echo "[✓] Ollama ready"
echo ""

# -------------------------------------------------------------------------
# 2. Python venv + dependencies
# -------------------------------------------------------------------------
WORKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$WORKER_DIR/.venv"

# Check Python version
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[→] Python version: $PY_VERSION"

if [ ! -d "$VENV_DIR" ]; then
    echo "[→] Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

echo "[→] Installing Python dependencies..."
pip install --upgrade pip -q

# -------------------------------------------------------------------------
# 3. Install PyTorch — try ROCm, fall back to CPU
# -------------------------------------------------------------------------
TORCH_INSTALLED=0

# Check if ROCm is available and try to install ROCm PyTorch
if command -v rocminfo &>/dev/null; then
    ROCM_VERSION=$(cat /opt/rocm/.info/version 2>/dev/null || echo "")

    if [ -n "$ROCM_VERSION" ]; then
        echo "[→] ROCm detected: $ROCM_VERSION"

        # Map ROCm version to PyTorch wheel index
        ROCM_MAJOR_MINOR=$(echo "$ROCM_VERSION" | grep -oP '^\d+\.\d+')

        echo "[→] Trying PyTorch for ROCm $ROCM_MAJOR_MINOR..."
        if pip install torch torchaudio --index-url "https://download.pytorch.org/whl/rocm${ROCM_MAJOR_MINOR}" -q 2>/dev/null; then
            TORCH_INSTALLED=1
            echo "[✓] PyTorch ROCm installed"
        else
            echo "[!] PyTorch ROCm wheel not found for rocm${ROCM_MAJOR_MINOR} + Python ${PY_VERSION}"
            echo "    Trying nightly build..."

            if pip install --pre torch torchaudio --index-url "https://download.pytorch.org/whl/nightly/rocm${ROCM_MAJOR_MINOR}" -q 2>/dev/null; then
                TORCH_INSTALLED=1
                echo "[✓] PyTorch ROCm (nightly) installed"
            else
                echo "[!] Nightly also failed. Falling back to CPU PyTorch."
            fi
        fi
    fi
fi

if [ "$TORCH_INSTALLED" -eq 0 ]; then
    echo "[→] Installing PyTorch (CPU only)..."
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu -q
    echo "[✓] PyTorch CPU installed"
    echo ""
    echo "    NOTE: Whisper will run on CPU. This is fine for background processing."
    echo "    A 30s voice note takes ~30s to transcribe on your Ryzen 5 5600X."
    echo ""
    echo "    To enable GPU later, install ROCm + PyTorch ROCm manually:"
    echo "    pip install torch torchaudio --index-url https://download.pytorch.org/whl/rocm6.4"
fi

# Install the rest of the dependencies
pip install -r "$WORKER_DIR/requirements.txt" -q

echo ""
echo "[✓] Python environment ready"
echo ""

# -------------------------------------------------------------------------
# 4. Test
# -------------------------------------------------------------------------
echo "[→] Quick health check..."
python3 -c "
from faster_whisper import WhisperModel
print('[✓] faster-whisper importable')
import torch
if hasattr(torch, 'cuda') and torch.cuda.is_available():
    print(f'[✓] GPU available: {torch.cuda.get_device_name(0)}')
    print(f'    Whisper will use GPU acceleration')
else:
    print('[i] GPU not available for Whisper — CPU mode (still works fine)')
print()
print('[i] Ollama handles its own GPU detection — your RX 7900 GRE')
print('    should be used automatically for LLM inference.')
"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Start the worker with:"
echo "  ./recall-worker-start.sh"
