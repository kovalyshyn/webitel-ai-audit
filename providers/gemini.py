"""Gemini AI provider — transcription and evaluation (google-genai SDK)."""
import json
import logging
import os
import re
import tempfile

from google import genai
from google.genai import types

from .base import (
    EvaluationProvider,
    EvaluationResult,
    QuestionAnswer,
    TranscriptPhrase,
    TranscriptResult,
    TranscriptionProvider,
)

logger = logging.getLogger(__name__)


class GeminiProvider(TranscriptionProvider, EvaluationProvider):
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self._client = genai.Client(api_key=api_key)
        self.model_name = model

    # ── Transcription ─────────────────────────────────────────────────────────

    def transcribe(self, audio_bytes: bytes, mime_type: str, language: str) -> TranscriptResult:
        suffix = "." + mime_type.split("/")[-1].replace("mpeg", "mp3")
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            uploaded = self._client.files.upload(
                file=tmp_path,
                config=types.UploadFileConfig(mime_type=mime_type),
            )

            prompt = (
                f"Transcribe this call center audio recording in language '{language}'.\n"
                "Return ONLY a JSON object with this structure:\n"
                '{"text": "<full transcript>", "phrases": [{"phrase": "...", "start_sec": 0.0, "end_sec": 1.5, "channel": 0}]}\n'
                "Use channel=0 for the agent and channel=1 for the customer when distinguishable.\n"
                "If timestamps are unavailable, return phrases as empty array.\n"
                "Do NOT include any markdown or explanation — raw JSON only."
            )

            response = self._client.models.generate_content(
                model=self.model_name,
                contents=[
                    types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime_type),
                    prompt,
                ],
            )
            self._client.files.delete(name=uploaded.name)
        finally:
            os.unlink(tmp_path)

        raw = _extract_json(response.text)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Gemini returned non-JSON for transcription, using raw text")
            return TranscriptResult(text=response.text.strip())

        phrases = [
            TranscriptPhrase(
                phrase=p.get("phrase", ""),
                start_sec=float(p.get("start_sec", 0)),
                end_sec=float(p.get("end_sec", 0)),
                channel=int(p.get("channel", 0)),
            )
            for p in data.get("phrases", [])
        ]
        return TranscriptResult(text=data.get("text", ""), phrases=phrases)

    # ── Evaluation ────────────────────────────────────────────────────────────

    def evaluate(self, transcript: str, questions: list[dict]) -> EvaluationResult:
        questions_text = _format_questions(questions)
        prompt = (
            "You are a call center quality evaluator.\n\n"
            f"TRANSCRIPT:\n{transcript}\n\n"
            f"SCORECARD QUESTIONS:\n{questions_text}\n\n"
            "Evaluate the agent's performance based on the transcript.\n"
            "Return ONLY a JSON object with this structure:\n"
            '{"answers": [{"score": <number>, "reasoning": "<brief>"}], "comment": "<overall comment>"}\n'
            "Rules:\n"
            "- answers array MUST have exactly the same number of entries as questions, in order\n"
            "- For score questions: use a number within the specified [min, max] range\n"
            "- For option questions: use the score value of the chosen option\n"
            "- Do NOT include markdown or explanation — raw JSON only."
        )

        response = self._client.models.generate_content(
            model=self.model_name,
            contents=prompt,
        )
        raw = _extract_json(response.text)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError(f"Could not parse evaluation response: {response.text[:200]}")

        answers = [
            QuestionAnswer(score=float(a.get("score", 0)), reasoning=a.get("reasoning", ""))
            for a in data.get("answers", [])
        ]

        if len(answers) != len(questions):
            raise ValueError(
                f"Got {len(answers)} answers but expected {len(questions)}"
            )

        return EvaluationResult(answers=answers, comment=data.get("comment", ""))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    return match.group(1).strip() if match else text


def _format_questions(questions: list[dict]) -> str:
    lines = []
    for i, q in enumerate(questions, 1):
        q_type = q.get("type", "")
        question = q.get("question", "")
        if q_type == "question_score":
            mn, mx = q.get("min", 1), q.get("max", 10)
            lines.append(f"{i}. [SCORE {mn}-{mx}] {question}")
        elif q_type == "question_option":
            options = ", ".join(f"{o['name']}={o['score']}" for o in q.get("options", []))
            lines.append(f"{i}. [OPTION: {options}] {question}")
        else:
            lines.append(f"{i}. {question}")
    return "\n".join(lines)
