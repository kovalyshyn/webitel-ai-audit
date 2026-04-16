"""ElevenLabs transcription provider (Scribe v1)."""
import logging
import os
import subprocess
import tempfile

import httpx

from .base import TranscriptionProvider, TranscriptPhrase, TranscriptResult

logger = logging.getLogger(__name__)

_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
PAUSE_SPLIT_SEC = 1.0  # start new phrase after this silence gap


class ElevenLabsProvider(TranscriptionProvider):
    def __init__(self, api_key: str, model: str = "scribe_v1"):
        self._api_key = api_key
        self._model = model

    def transcribe(self, audio_bytes: bytes, mime_type: str, language: str) -> TranscriptResult:
        channels = _detect_channels(audio_bytes, mime_type)
        logger.info("Audio channels detected: %d", channels)

        if channels >= 2:
            return self._transcribe_stereo(audio_bytes, mime_type, language)
        else:
            return self._transcribe_mono(audio_bytes, mime_type, language, channel=0)

    def _transcribe_stereo(self, audio_bytes: bytes, mime_type: str, language: str) -> TranscriptResult:
        """Split stereo into 2 mono tracks, transcribe each, merge by timestamp."""
        left, right = _split_stereo(audio_bytes, mime_type)

        logger.info("Transcribing channel 0 (left / agent)...")
        result_left = self._transcribe_mono(left, "audio/wav", language, channel=0)

        logger.info("Transcribing channel 1 (right / customer)...")
        result_right = self._transcribe_mono(right, "audio/wav", language, channel=1)

        # Merge full texts
        text = f"{result_left.text}\n{result_right.text}".strip()

        # Merge phrases sorted by start time
        all_phrases = sorted(
            result_left.phrases + result_right.phrases,
            key=lambda p: p.start_sec,
        )

        logger.info(
            "Stereo transcription merged: ch0=%d phrases, ch1=%d phrases → %d total",
            len(result_left.phrases), len(result_right.phrases), len(all_phrases),
        )
        return TranscriptResult(text=text, phrases=all_phrases)

    def _transcribe_mono(
        self, audio_bytes: bytes, mime_type: str, language: str, channel: int
    ) -> TranscriptResult:
        files = {"file": ("audio", audio_bytes, mime_type)}
        data = {
            "model_id": self._model,
            "diarize": "false",              # no diarization — channel is already known
            "timestamps_granularity": "word",
        }
        if language:
            data["language_code"] = language

        r = httpx.post(
            _STT_URL,
            headers={"xi-api-key": self._api_key},
            files=files,
            data=data,
            timeout=120,
        )
        r.raise_for_status()
        result = r.json()

        text = result.get("text", "")
        words = result.get("words", [])
        phrases = _group_into_phrases(words, channel=channel)

        logger.info(
            "Channel %d: %d chars, %d words, %d phrases, lang=%s",
            channel, len(text), len(words), len(phrases), result.get("language_code"),
        )
        return TranscriptResult(text=text, phrases=phrases)


# ── Audio helpers ─────────────────────────────────────────────────────────────

def _detect_channels(audio_bytes: bytes, mime_type: str) -> int:
    """Return number of audio channels using ffprobe."""
    suffix = _suffix(mime_type)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        tmp = f.name
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=channels",
                "-of", "csv=p=0",
                tmp,
            ],
            capture_output=True, text=True, timeout=30,
        )
        return int(r.stdout.strip()) if r.stdout.strip() else 1
    except Exception as e:
        logger.warning("ffprobe failed, assuming mono: %s", e)
        return 1
    finally:
        os.unlink(tmp)


def _split_stereo(audio_bytes: bytes, mime_type: str) -> tuple[bytes, bytes]:
    """Split stereo audio into (left, right) mono WAV bytes."""
    suffix = _suffix(mime_type)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        inp = f.name

    left_path = inp + "_ch0.wav"
    right_path = inp + "_ch1.wav"

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", inp,
             "-filter_complex", "channelsplit=channel_layout=stereo[L][R]",
             "-map", "[L]", "-ar", "16000", left_path,
             "-map", "[R]", "-ar", "16000", right_path],
            capture_output=True, check=True, timeout=120,
        )
        return _read(left_path), _read(right_path)
    finally:
        for p in [inp, left_path, right_path]:
            if os.path.exists(p):
                os.unlink(p)


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _suffix(mime_type: str) -> str:
    return "." + mime_type.split("/")[-1].replace("mpeg", "mp3")


# ── Phrase grouping ───────────────────────────────────────────────────────────

def _group_into_phrases(words: list[dict], channel: int) -> list[TranscriptPhrase]:
    """Merge consecutive words into phrases, splitting on pause."""
    if not words:
        return []

    phrases: list[TranscriptPhrase] = []
    current_words: list[str] = []
    current_start = 0.0
    current_end = 0.0

    def flush():
        if current_words:
            phrases.append(TranscriptPhrase(
                phrase=" ".join(current_words),
                start_sec=current_start,
                end_sec=current_end,
                channel=channel,
            ))

    for w in words:
        if w.get("type") == "spacing":
            continue

        text = w.get("text", "")
        start = float(w.get("start", 0))
        end = float(w.get("end", 0))

        if current_words and (start - current_end) >= PAUSE_SPLIT_SEC:
            flush()
            current_words = [text]
            current_start = start
            current_end = end
        else:
            if not current_words:
                current_start = start
            current_words.append(text)
            current_end = end

    flush()
    return phrases
