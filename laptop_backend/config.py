from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8-sig", extra="ignore")

    openai_api_key: str = ""
    deepgram_api_key: str = ""
    pi_base_url: str = "http://zoro2026.local:8000"

    openai_model: str = "gpt-4o-mini"
    deepgram_stt_model: str = "nova-3"
    deepgram_stt_language: str = "en"
    deepgram_endpointing_ms: int = 120
    deepgram_tts_model: str = "aura-2-thalia-en"
    live_tts_mode: str = "deepgram_stream"
    deepgram_tts_speed: float = 0.95
    cartesia_api_key: str = ""
    cartesia_api_pool: str = ""
    cartesia_credit_limit: int = 20000
    cartesia_credit_reserve: int = 500
    cartesia_tts_model: str = "sonic-3"
    cartesia_voice_id: str = ""
    cartesia_version: str = "2026-03-01"
    cartesia_sample_rate: int = 48000
    cartesia_voice_speed: float = 0.82
    cartesia_voice_emotion: str = "content"
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    perception_max_fps: float = 0.25
    attendance_auto_scan_enabled: bool = True
    attendance_auto_scan_interval_minutes: int = 45
    attendance_auto_scan_initial_delay_seconds: int = 60
    classroom_behavior_model: Path = Path("data/models/classroom_behavior_best.pt")
    classroom_behavior_dataset_slug: str = "nonpat/detecting-student-classroom-behavior2"
    classroom_behavior_models_slug: str = "nonpat/test010"

    data_dir: Path = Path("data")
    syllabus_dir: Path = Path("data/syllabus")
    face_dir: Path = Path("data/faces")


settings = Settings()


def ensure_dirs() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.syllabus_dir.mkdir(parents=True, exist_ok=True)
    settings.face_dir.mkdir(parents=True, exist_ok=True)
