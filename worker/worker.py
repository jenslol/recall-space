"""
Recall Space — AI Worker
Runs on the gaming PC (CachyOS, RX 7900 GRE).
Exposes /transcribe and /analyze endpoints.
Requires: Ollama running locally, faster-whisper installed.
"""

import json
import os
import tempfile
import logging
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("recall-worker")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b-instruct-q4_K_M")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")  # auto, cuda, cpu
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "float16")  # float16, int8, float32

app = FastAPI(title="Recall Space Worker", version="0.1.0")

# Lazy-loaded whisper model
_whisper_model = None


def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        log.info(f"Loading Whisper model '{WHISPER_MODEL}' on device '{WHISPER_DEVICE}'...")
        _whisper_model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE,
        )
        log.info("Whisper model loaded.")
    return _whisper_model


# ---------------------------------------------------------------------------
# /transcribe — accepts audio, returns transcript
# ---------------------------------------------------------------------------
@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str | None = Form(None),
):
    """
    Transcribe an audio file using faster-whisper.
    Returns the full transcript and detected language.
    Timeout: 180 seconds max to prevent hangs on bad audio.
    """
    suffix = Path(file.filename).suffix if file.filename else ".webm"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        # Reject very small files (likely empty recordings)
        if len(content) < 1000:
            return {
                "transcript": "",
                "language": "unknown",
                "language_probability": 0,
                "duration": 0,
                "note": "Audio too short to transcribe",
            }

        import asyncio
        import concurrent.futures

        def _transcribe():
            model = get_whisper()
            segments, info = model.transcribe(
                tmp_path,
                language=language,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
            )
            parts = [seg.text.strip() for seg in segments]
            return " ".join(parts), info

        # Run in thread pool with timeout
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            try:
                transcript, info = await asyncio.wait_for(
                    loop.run_in_executor(pool, _transcribe),
                    timeout=180.0,
                )
            except asyncio.TimeoutError:
                log.warning("Transcription timed out after 180s")
                raise HTTPException(504, "Transcription timed out")

        return {
            "transcript": transcript,
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
            "duration": round(info.duration, 1),
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Transcription failed: {e}")
        raise HTTPException(500, f"Transcription failed: {str(e)}")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except:
                pass


# ---------------------------------------------------------------------------
# /analyze — accepts text, returns summary + actions + dates + tags
# ---------------------------------------------------------------------------
ANALYZE_SYSTEM_PROMPT = """You are a precise analysis assistant for a personal knowledge capture system.
Given captured content (text, OCR output, transcripts, URLs, notes), extract structured information.

You MUST respond with valid JSON only, no markdown, no explanation. Use this exact schema:

{
  "title": "short title, 3-7 words",
  "summary": "2-3 sentence summary of what this content is about",
  "actions": ["action item 1", "action item 2"],
  "dates": ["2025-03-22T14:00:00 - meeting with Gert", "2025-04-01 - deadline for report"],
  "tags": ["tag1", "tag2", "tag3"]
}
Rules:
- title: 3-7 word title capturing the core topic. No punctuation.
- summary: Brief, useful. What is this and why does it matter?
- actions: Extract any tasks, to-dos, follow-ups, things to remember to do. Empty array if none.
- dates: Extract any dates, deadlines, appointments, scheduled events. Include the context. Use ISO format where possible. Empty array if none.
- tags: 3-6 relevant tags for categorization. Lowercase, no hashtags.
- If the content is trivial or lacks actionable info, still provide a summary and tags.
- Respond ONLY with the JSON object. No markdown fences, no preamble.


@app.post("/analyze")
async def analyze(
    text: str = Form(...),
    user_note: str | None = Form(None),
    content_type: str | None = Form(None),
):
    """
    Analyze text content using Ollama LLM.
    Returns summary, action items, dates, and tags.
    """
    import httpx

    # Build the prompt
    parts = []
    if content_type:
        parts.append(f"[Content type: {content_type}]")
    if user_note:
        parts.append(f"[User's note about this: {user_note}]")
    parts.append(f"[Content]\n{text}")

    user_message = "\n\n".join(parts)

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 1024,
                    },
                },
            )
            response.raise_for_status()

        data = response.json()
        raw_content = data.get("message", {}).get("content", "")

        # Parse the JSON response, handling potential markdown fences
        cleaned = raw_content.strip()
        if cleaned.startswith("```"):
            # Strip markdown code fences
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        result = json.loads(cleaned)

        # Validate expected keys
        return {
            "summary": result.get("summary", ""),
            "actions": result.get("actions", []),
            "dates": result.get("dates", []),
            "tags": result.get("tags", []),
        }

    except json.JSONDecodeError as e:
        log.warning(f"LLM returned invalid JSON: {raw_content[:200]}")
        # Fallback: return the raw text as summary
        return {
            "summary": raw_content[:500] if raw_content else "Analysis failed to parse",
            "actions": [],
            "dates": [],
            "tags": [],
        }
    except httpx.HTTPError as e:
        log.error(f"Ollama request failed: {e}")
        raise HTTPException(502, f"Ollama unavailable: {str(e)}")
    except Exception as e:
        log.error(f"Analysis failed: {e}")
        raise HTTPException(500, f"Analysis failed: {str(e)}")


# ---------------------------------------------------------------------------
# Health / status
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    import httpx

    ollama_ok = False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            ollama_ok = r.status_code == 200
    except:
        pass

    return {
        "status": "ok",
        "ollama": "connected" if ollama_ok else "unavailable",
        "ollama_model": OLLAMA_MODEL,
        "whisper_model": WHISPER_MODEL,
        "whisper_device": WHISPER_DEVICE,
    }
