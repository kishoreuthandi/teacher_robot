from pathlib import Path
from uuid import uuid4

import httpx

from .config import settings


class VoicePipeline:
    def __init__(self) -> None:
        self.output_dir = settings.data_dir / "tts"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def transcribe(self, audio_bytes: bytes, content_type: str = "audio/wav") -> str:
        if not settings.deepgram_api_key:
            return ""
        url = "https://api.deepgram.com/v1/listen"
        params = {"model": settings.deepgram_stt_model, "smart_format": "true"}
        headers = {
            "Authorization": f"Token {settings.deepgram_api_key}",
            "Content-Type": content_type,
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(url, params=params, headers=headers, content=audio_bytes)
            response.raise_for_status()
            data = response.json()
        try:
            return data["results"]["channels"][0]["alternatives"][0]["transcript"]
        except (KeyError, IndexError):
            return ""

    async def synthesize(self, text: str) -> tuple[Path, bytes]:
        if not settings.deepgram_api_key:
            raise RuntimeError("Deepgram API key is not configured.")
        url = "https://api.deepgram.com/v1/speak"
        params = {"model": settings.deepgram_tts_model, "speed": str(settings.deepgram_tts_speed)}
        headers = {
            "Authorization": f"Token {settings.deepgram_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(url, params=params, headers=headers, json={"text": text})
            response.raise_for_status()
        output = self.output_dir / f"{uuid4().hex}.mp3"
        output.write_bytes(response.content)
        return output, response.content
