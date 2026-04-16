"""Webitel AI Audit Service."""
import asyncio
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from config import settings
from providers import get_evaluation_provider, get_transcription_provider
from webitel import WebitelClient, WebitelError


class _CallUUIDFilter(logging.Filter):
    """Inject a default call_uuid into log records that don't have one."""
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "call_uuid"):
            record.call_uuid = "-"
        return True


_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s [%(call_uuid)s]: %(message)s"
))
_handler.addFilter(_CallUUIDFilter())

logging.root.setLevel(logging.INFO)
logging.root.addHandler(_handler)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

app = FastAPI(title="Webitel AI Audit", version="0.1.0")

webitel = WebitelClient(settings.webitel_url, settings.webitel_token)

transcriber = get_transcription_provider(
    settings.transcription_provider,
    api_key=settings.elevenlabs_api_key if settings.transcription_provider == "elevenlabs" else settings.gemini_api_key,
    **({"model": settings.elevenlabs_model} if settings.transcription_provider == "elevenlabs" else {"model": settings.gemini_model}),
)

evaluator = get_evaluation_provider(
    settings.evaluation_provider,
    api_key=settings.gemini_api_key,
    model=settings.gemini_model,
)

_in_progress: set[str] = set()
_in_progress_lock = asyncio.Lock()


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(level: str, call_uuid: str, msg: str, *args) -> None:
    getattr(logger, level)(msg, *args, extra={"call_uuid": call_uuid})


# ── Background pipeline ───────────────────────────────────────────────────────

async def run_pipeline(call_uuid: str, questionnaire_id: int) -> None:
    async with _in_progress_lock:
        _in_progress.add(call_uuid)
    try:
        await _pipeline(call_uuid, questionnaire_id)
    except Exception as e:
        log("error", call_uuid, "Pipeline failed: %s", e)
    finally:
        async with _in_progress_lock:
            _in_progress.discard(call_uuid)
        log("info", call_uuid, "Pipeline finished")


async def _pipeline(call_uuid: str, questionnaire_id: int) -> None:
    # ── Step 1: call details ──────────────────────────────────────────────────
    log("info", call_uuid, "Fetching call details")
    call = await webitel.get_call(call_uuid)

    files = call.get("files", [])
    if not files:
        log("warning", call_uuid, "Call has no recording files — skipping")
        return

    audio_file = next((f for f in files if "audio" in f.get("mime_type", "")), files[0])
    file_id = audio_file["id"]
    mime_type = audio_file.get("mime_type", "audio/mp3")
    duration = call.get("duration", 0)
    existing_transcripts = call.get("transcripts", [])

    log("info", call_uuid, "Recording file_id=%s mime=%s duration=%ss", file_id, mime_type, duration)

    # ── Step 2: transcription (skip if already exists) ────────────────────────
    if existing_transcripts:
        transcript_text = existing_transcripts[0].get("transcript", "")
        log("info", call_uuid, "Transcript already exists (id=%s) — skipping transcription",
            existing_transcripts[0].get("id"))
    else:
        log("info", call_uuid, "Downloading recording (%s bytes expected)", audio_file.get("size", "?"))
        audio_bytes = await webitel.download_recording(file_id)
        log("info", call_uuid, "Downloaded %d bytes", len(audio_bytes))

        log("info", call_uuid, "Transcribing with provider=%s", settings.transcription_provider)
        transcript = await asyncio.to_thread(
            transcriber.transcribe, audio_bytes, mime_type, settings.transcription_language
        )
        transcript_text = transcript.text
        log("info", call_uuid, "Transcription done: %d chars, %d phrases",
            len(transcript_text), len(transcript.phrases))

        # ── Step 3: save transcript ───────────────────────────────────────────
        phrases_payload = [
            {"phrase": p.phrase, "start_sec": p.start_sec, "end_sec": p.end_sec, "channel": p.channel}
            for p in transcript.phrases
        ] or [
            {"phrase": transcript_text, "start_sec": 0.0, "end_sec": float(duration), "channel": 0}
        ]

        log("info", call_uuid, "Saving transcript to Webitel (%d phrases)", len(phrases_payload))
        saved = await webitel.save_transcript(
            file_id=file_id,
            call_uuid=call_uuid,
            text=transcript_text,
            phrases=phrases_payload,
            locale=settings.transcription_language,
        )
        log("info", call_uuid, "Transcript saved: id=%s", saved.get("id"))

    if not transcript_text:
        log("warning", call_uuid, "Empty transcript — skipping evaluation")
        return

    # ── Step 4: fetch scorecard ───────────────────────────────────────────────
    log("info", call_uuid, "Fetching scorecard id=%s", questionnaire_id)
    scorecard = await webitel.get_scorecard(questionnaire_id)
    questions = scorecard.get("questions", [])
    if not questions:
        log("warning", call_uuid, "Scorecard has no questions — skipping evaluation")
        return

    log("info", call_uuid, "Scorecard '%s' — %d questions", scorecard.get("name"), len(questions))

    # ── Step 5: evaluate ──────────────────────────────────────────────────────
    log("info", call_uuid, "Evaluating with provider=%s", settings.evaluation_provider)
    evaluation = await asyncio.to_thread(evaluator.evaluate, transcript_text, questions)
    log("info", call_uuid, "Evaluation done — %s", evaluation.comment[:120])

    for i, (q, a) in enumerate(zip(questions, evaluation.answers), 1):
        log("info", call_uuid, "  Q%d score=%.1f  %s", i, a.score, a.reasoning[:80])

    # ── Step 6: save audit rate ───────────────────────────────────────────────
    answers_payload = [{"score": a.score} for a in evaluation.answers]
    log("info", call_uuid, "Saving audit rate to Webitel")
    rate = await webitel.save_audit_rate(
        call_id=call_uuid,
        form_id=questionnaire_id,
        form_name=scorecard.get("name", ""),
        answers=answers_payload,
        comment=evaluation.comment,
    )
    log("info", call_uuid, "Audit rate saved: id=%s score_required=%.1f score_optional=%.1f",
        rate.get("id"), rate.get("score_required", 0), rate.get("score_optional", 0))


# ── Endpoint ──────────────────────────────────────────────────────────────────

@app.get("/evaluate")
async def evaluate_call(
    background_tasks: BackgroundTasks,
    call_uuid: str = Query(..., description="UUID of the call to evaluate"),
    questionnaire_id: int = Query(..., description="ID of the audit scorecard form"),
):
    async with _in_progress_lock:
        if call_uuid in _in_progress:
            log("warning", call_uuid, "Already in progress — ignoring duplicate request")
            return JSONResponse({"status": "already_processing", "call_uuid": call_uuid})

    log("info", call_uuid, "Accepted — queuing pipeline (scorecard=%s)", questionnaire_id)
    background_tasks.add_task(run_pipeline, call_uuid, questionnaire_id)
    return JSONResponse({"status": "accepted", "call_uuid": call_uuid})


@app.get("/health")
async def health():
    return {"status": "ok", "in_progress": len(_in_progress)}
