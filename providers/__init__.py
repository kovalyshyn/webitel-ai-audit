from .base import (
    EvaluationProvider,
    EvaluationResult,
    QuestionAnswer,
    TranscriptPhrase,
    TranscriptResult,
    TranscriptionProvider,
)
from .elevenlabs import ElevenLabsProvider
from .gemini import GeminiProvider

__all__ = [
    "TranscriptionProvider", "EvaluationProvider",
    "TranscriptResult", "TranscriptPhrase",
    "EvaluationResult", "QuestionAnswer",
    "GeminiProvider", "ElevenLabsProvider",
    "get_transcription_provider", "get_evaluation_provider",
]


def get_transcription_provider(name: str, **kwargs) -> TranscriptionProvider:
    if name == "gemini":
        return GeminiProvider(**kwargs)
    if name == "elevenlabs":
        return ElevenLabsProvider(**kwargs)
    raise ValueError(f"Unknown transcription provider: {name!r}")


def get_evaluation_provider(name: str, **kwargs) -> EvaluationProvider:
    if name == "gemini":
        return GeminiProvider(**kwargs)
    raise ValueError(f"Unknown evaluation provider: {name!r}")
