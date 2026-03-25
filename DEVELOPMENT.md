# Recall Space — Development Guide

Reference document for ongoing development. Covers architecture, deployment, quirks, and roadmap.

## Architecture

```
┌──────────────────────────────┐      ┌──────────────────────────────┐
│  Servern (Hub) — always on   │      │  Stationen (Brain) — on-demand│
│  Intel N150, 12GB RAM        │      │  Ryzen 5 5600X, 32GB RAM     │
│  Ubuntu 25.10, Docker (snap) │      │  RX 7900 GRE 16GB, CachyOS   │
│  IP: 192.168.86.35           │      │  IP: 192.168.86.28            │
│                              │      │                              │
│  FastAPI + SQLite            │─────▶│  Ollama (llama3.1:8b Q4)     │
│  Background job processor    │      │  faster-whisper (medium/CPU)  │
│  ntfy.sh (push reminders)    │◀─────│  Worker API (:8401)          │
│  :8400 / recall.tarnished.dk │      │                              │
└──────────────────────────────┘      └──────────────────────────────┘
```

Hub captures and stores immediately. Brain processes when available.
If Stationen is off, memories queue up and process when it comes online.
Processor runs as a background asyncio task inside the main FastAPI app (not a separate container — this avoids SQLite locking).

## Machines

### Servern (GMKtec N150 — Hub)
- OS: Ubuntu 25.10
- Shell: bash
- Docker: snap
- Project: `~/recall-space/`
- Compose: `docker compose up -d --build`
- Containers: `recall-space` (app), `recall-ntfy` (ntfy, currently unused if using ntfy.sh)
- Network: `shared-proxy` (external Docker network, shared with NPM and all other services)
- External: `recall.tarnished.dk` via Cloudflare tunnel → NPM → `recall-space:8000`

### Stationen (Gaming PC — Brain)
- OS: CachyOS (Arch-based), KDE Plasma
- Shell: fish
- Python: 3.14 system, **3.12 via pyenv** (required for PyTorch)
- ROCm: 7.2 (PyTorch uses rocm6.4 wheels — close enough, works)
- Project: `~/recall-space/` (cloned from GitHub)
- Worker: `~/recall-space/worker/`
- Venv: `~/recall-space-worker/.venv` symlinked to `~/recall-space/worker/.venv`
- Start: `cd ~/recall-space/worker && ./recall-worker-start.sh`
- Ollama: uses GPU (RX 7900 GRE) automatically
- Whisper: runs on CPU (int8, medium model) — faster-whisper CTranslate2 doesn't work reliably with ROCm
- UFW: port 8401 open for LAN (`192.168.86.0/24`)

## Key Files

### Hub (Servern)
- `app/main.py` — FastAPI app, all routes, lifespan starts processor + reminder checker
- `app/processor.py` — Background job processor (OCR, transcribe, analyze, extract_text)
- `app/reminders.py` — Background reminder checker, sends ntfy.sh notifications
- `app/database.py` — SQLite schema, init, helpers
- `static/js/app.js` — All frontend logic (timeline, search, collections, capture, voice)
- `templates/index.html` — Main page
- `templates/detail.html` — Memory detail page
- `.env` — Config (API key, worker URL, ntfy, base URL) — NOT in git
- `docker-compose.yml` — App + ntfy containers

### Worker (Stationen)
- `worker/worker.py` — FastAPI with /transcribe and /analyze endpoints
- `worker/recall-worker-start.sh` — Start script (sets ROCm env vars, activates venv)
- `worker/recall-worker-setup.sh` — First-time setup (Ollama, pyenv, PyTorch, whisper)
- `worker/recall-worker.service` — Systemd user service (optional auto-start)

## Environment Variables (.env on Servern)

```
AI_WORKER_URL=http://192.168.86.28:8401
API_KEY=<your-secret-key>
DATABASE_PATH=/app/data/recall.db
UPLOADS_PATH=/app/uploads
POLL_INTERVAL=10
NTFY_URL=https://ntfy.sh
NTFY_TOPIC=<your-unique-topic>
BASE_URL=https://recall.tarnished.dk
```

## Deployment

### Updating the hub (Servern)
```bash
cd ~/recall-space
docker compose down
# Apply code changes (git pull, or extract tar)
rm -f data/recall.db*  # Only if schema changed
docker compose up -d --build
```

### Updating the worker (Stationen)
```fish
cd ~/recall-space
git pull
cd worker
# Kill running worker (Ctrl+C or pkill -f "uvicorn worker:app")
./recall-worker-start.sh
```

### Git workflow
```bash
# From whichever machine has the changes
git add .
git commit -m "description"
git push
# On the other machine
git pull
```

## Known Quirks

1. **Fresh DB on schema changes** — SQLite `CREATE TABLE IF NOT EXISTS` doesn't update constraints on existing tables. Delete `data/recall.db*` when the schema changes.

2. **API key in browser** — Must be set manually via browser console: `localStorage.setItem('recall_api_key', 'your-key')`. Needs to be done per browser/device.

3. **Voice recording requires HTTPS** — Browsers block microphone on plain HTTP. Either use `recall.tarnished.dk` (HTTPS) or set Chrome flag `chrome://flags/#unsafely-treat-insecure-origin-as-secure` for the LAN IP.

4. **Whisper on CPU** — faster-whisper's CTranslate2 backend doesn't work reliably with ROCm/AMD GPU. CPU mode (int8) is fine for background processing. A 30s clip takes ~30s to transcribe.

5. **PyTorch + CachyOS** — System Python 3.14 is too new for PyTorch wheels. Must use pyenv Python 3.12. The venv is at `~/recall-space-worker/.venv`.

6. **ntfy emoji crash** — The brain emoji (🧠) in notification titles causes an ASCII encoding error in httpx headers. Stripped in current code. Don't add non-ASCII to ntfy header fields.

7. **Large files** — The LLM has a token limit. Very large .md/.txt files get truncated at 100KB by the text extractor, but the LLM summary will be incomplete. Future: add chunking.

8. **Worker offline handling** — When Stationen is off, transcribe and analyze jobs stay pending (attempts not incremented). OCR and extract_text run locally on Servern regardless.

## Roadmap

### Done
- [x] Phase 1 — Capture + storage + web UI
- [x] Phase 2 — AI worker (Whisper + Ollama) + job processing
- [x] v0.2 — Clean rewrite (merged processor, auth, text files, bug fixes)
- [x] v0.3 — Collections UI, search highlights, ntfy reminders, tags display

### Next
- [ ] Fix ntfy.sh config (verify .env has ntfy.sh not local container)
- [ ] Desktop capture shortcuts (KDE hotkeys on Stationen) — low priority
- [ ] Design overhaul (tarnished.dk brand identity) — Phase 4
- [ ] Mobile PWA polish (share target, install prompt)

### Future
- [ ] Chunking for large documents
- [ ] Semantic search with embeddings (nomic-embed-text via Ollama)
- [ ] Editable reminders (change date, dismiss, snooze)
- [ ] Export as Markdown/PDF
- [ ] MCP server for Recall Space
- [ ] Swap Stationen for Mac Mini (just change AI_WORKER_URL)
- [ ] API key input in the web UI (settings page instead of console)
- [ ] On-device Whisper (phone-side transcription)
