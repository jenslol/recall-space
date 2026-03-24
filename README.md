# Recall Space

Self-hosted, platform-agnostic alternative to Nothing's Essential Space.
Capture screenshots, voice notes, text, and URLs from any device.
AI processes everything in the background — transcription, summarization, action items, reminders.

## Architecture

```
┌──────────────────────────────┐      ┌──────────────────────────────┐
│  GMKtec (Hub) — always on    │      │  Gaming PC / Mac Mini (Brain)│
│                              │      │                              │
│  FastAPI + SQLite            │─────▶│  Ollama (LLM analysis)       │
│  Background job processor    │      │  Whisper (transcription)     │
│  PWA frontend                │◀─────│  Worker API (:8401)          │
│  ntfy (push notifications)   │      │                              │
│  :8400                       │      │  Can be offline — jobs queue  │
└──────────────────────────────┘      └──────────────────────────────┘
```

The hub captures and stores immediately. The brain processes when available.
If the gaming PC is off, memories queue up and process when it comes online.

## Quick Start

```bash
cp .env.example .env
nano .env                    # Set AI_WORKER_URL to your gaming PC IP

docker compose up -d --build
# Open http://your-server:8400
```

## What's Fixed in v0.2

- Processor runs inside the main app (no more SQLite locking)
- Text files (.txt, .md, .csv, etc.) are read and analyzed
- Proper `skipped` status in job queue
- Whisper timeout (180s) prevents queue hangs
- Worker status indicator (brain emoji) in the UI
- API key auth support
- Auto-refresh UI while jobs are processing
- Clean error handling throughout

## API Authentication

Set `API_KEY` in `.env` to require authentication. Generate a key:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Pass it via header: `X-API-Key: your-key-here`

Leave `API_KEY` empty for no auth (fine for LAN-only use).

## Project Phases

- [x] Phase 1 — Capture + storage + web UI
- [x] Phase 2 — AI worker (Whisper + Ollama) + job processing
- [x] v0.2 cleanup — Merged processor, auth, text files, bug fixes
- [ ] Phase 3 — ntfy push reminders, collections UI, desktop shortcuts
- [ ] Phase 4 — Design overhaul, PWA share target, mobile polish
