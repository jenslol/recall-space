# Recall Space — AI Worker

This runs on your **gaming PC** (CachyOS, AMD RX 7900 GRE).
It provides transcription (Whisper) and analysis (Ollama LLM) services
to the main Recall Space hub on the GMKtec.

## Setup

```bash
# 1. Copy this folder to your gaming PC
scp -r recall-space-worker/ jens@gaming-pc:~/

# 2. SSH in and run setup
ssh jens@gaming-pc
cd ~/recall-space-worker
chmod +x *.sh
./recall-worker-setup.sh
```

The setup script will:
- Install Ollama (if not present) and pull the LLM model
- Check for ROCm/GPU availability
- Create a Python venv with faster-whisper and PyTorch (ROCm)

## Run

```bash
./recall-worker-start.sh
```

The worker listens on port **8401** and exposes:
- `POST /transcribe` — audio file → transcript
- `POST /analyze` — text → summary + actions + dates + tags
- `GET /health` — status check

## Run as a systemd user service (auto-start on login)

```bash
mkdir -p ~/.config/systemd/user/
cp recall-worker.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable recall-worker
systemctl --user start recall-worker

# Check status
systemctl --user status recall-worker
journalctl --user -u recall-worker -f
```

## Test

```bash
# Health check
curl http://localhost:8401/health

# Test transcription
curl -X POST http://localhost:8401/transcribe \
  -F "file=@some-audio.webm"

# Test analysis
curl -X POST http://localhost:8401/analyze \
  -F "text=Meeting tomorrow at 2pm with Gert about Q3 budget" \
  -F "user_note=Important budget discussion"
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| OLLAMA_URL | http://localhost:11434 | Ollama API URL |
| OLLAMA_MODEL | llama3.1:8b-instruct-q4_K_M | LLM model for analysis |
| WHISPER_MODEL | medium | Whisper model size (tiny/small/medium/large) |
| WHISPER_DEVICE | auto | Device for Whisper (auto/cuda/cpu) |
| WHISPER_COMPUTE | float16 | Compute type (float16/int8/float32) |

## Swapping to Mac Mini later

When you get a Mac Mini, just:
1. Copy this folder there
2. Run `./recall-worker-setup.sh` (it'll detect Apple Silicon, skip ROCm)
3. Update `AI_WORKER_URL` in the GMKtec's `.env` to point to the Mac Mini
4. `docker compose restart processor` on the GMKtec
