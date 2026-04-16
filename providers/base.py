"""Abstract base classes for AI providers."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TranscriptPhrase:
    phrase: str
    start_sec: float
    end_sec: float
    channel: int = 0


@dataclass
class TranscriptResult:
    text: str
    phrases: list[TranscriptPhrase] = field(default_factory=list)


@dataclass
class QuestionAnswer:
    """Score for a single scorecard question."""
    score: float
    reasoning: str = ""


@dataclass
class EvaluationResult:
    answers: list[QuestionAnswer]
    comment: str = ""


class TranscriptionProvider(ABC):
    @abstractmethod
    def transcribe(self, audio_bytes: bytes, mime_type: str, language: str) -> TranscriptResult:
        """Transcribe audio and return transcript with optional phrase timestamps."""


class EvaluationProvider(ABC):
    @abstractmethod
    def evaluate(self, transcript: str, questions: list[dict]) -> EvaluationResult:
        """Evaluate a call transcript against scorecard questions."""
