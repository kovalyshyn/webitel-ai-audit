from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):
    # Webitel
    webitel_url: str
    webitel_token: str

    # Transcription provider
    transcription_provider: Literal["gemini", "elevenlabs"] = "elevenlabs"
    transcription_language: str = "uk"  # ISO 639-1

    # ElevenLabs
    elevenlabs_api_key: str = ""
    elevenlabs_model: str = "scribe_v1"

    # Evaluation provider
    evaluation_provider: Literal["gemini"] = "gemini"

    # Gemini (used for evaluation, and optionally for transcription)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    class Config:
        env_file = ".env"


settings = Settings()
