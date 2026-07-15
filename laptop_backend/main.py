from pathlib import Path
import asyncio
import base64
import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlencode

import httpx
import websockets
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .ai_teacher import TeacherAI
from .assessments import AssessmentStore
from .attendance import AttendanceService
from .behavior import BehaviorStore
from .brain import ZoroBrain
from .config import ensure_dirs, settings
from .conversation_store import ConversationStore
from .classroom_policy import ClassroomPolicy
from .lesson_planner import LessonPlanner
from .memory import MemoryStore
from .models import AskRequest, AskResponse, MoveRequest
from .notifications import NotificationStore
from .people_memory import PeopleMemory
from .perception import PerceptionEngine
from .rag import RagIndex
from .robot_client import RobotClient
from .self_model import SelfModel
from .voice import VoicePipeline
from .world_memory import WorldMemory
from fastapi.middleware.cors import CORSMiddleware

from .database import (
    init_db, save_attendance_record, get_attendance_sessions,
    get_attendance_records, save_syllabus, get_syllabus_list, delete_syllabus,
    save_speech, get_speeches, get_speech_by_id, update_speech_triggered,
    delete_speech, update_speech, save_transcript_session, get_transcript_sessions,
    get_transcript_messages,
)
from pydantic import BaseModel
from typing import Any, Optional, List
import uuid, datetime, json

ensure_dirs()
init_db()

app = FastAPI(title="Zoro 2026 Laptop Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

robot = RobotClient()
teacher = TeacherAI()
voice = VoicePipeline()
attendance = AttendanceService()
conversation_store = ConversationStore()
memory = MemoryStore()
self_model = SelfModel()
notifications = NotificationStore()
policy = ClassroomPolicy()
lessons = LessonPlanner()
behavior = BehaviorStore()
people_memory = PeopleMemory()
rag = RagIndex()
assessments = AssessmentStore()
world_memory = WorldMemory()
perception = PerceptionEngine(attendance, memory, notifications, behavior, world_memory)
brain = ZoroBrain(
    teacher, robot, attendance, voice, memory, self_model, perception,
    notifications, policy, lessons, behavior, people_memory, rag, world_memory,
)

_zoro_is_speaking = False
_voice_enabled = True
_deepgram_ws = None
_deepgram_lock = asyncio.Lock()
_cartesia_ws = None
_cartesia_ws_pool: dict[str, Any] = {}
_cartesia_lock = asyncio.Lock()
_last_voice_latency: dict = {}
_ignore_audio_until = 0.0
_tts_cache_dir = settings.data_dir / "tts_cache"
_tts_cache_locks: dict[str, asyncio.Lock] = {}
_speaking_token = 0
_speaker_lock = asyncio.Lock()
_voice_turn_start_peak = 2500
_last_obstacle_voice: dict[str, Any] = {"message": "", "at": 0.0}
_lesson_runner_task: asyncio.Task | None = None
_attendance_auto_scan: dict[str, Any] = {
    "enabled": settings.attendance_auto_scan_enabled,
    "interval_minutes": settings.attendance_auto_scan_interval_minutes,
    "last_run": None,
    "next_run": None,
    "last_result": None,
    "last_error": "",
    "last_session_key": None,
}
_voice_diagnostics: dict = {
    "sessions": 0,
    "audio_chunks_from_pi": 0,
    "audio_bytes_from_pi": 0,
    "audio_chunks_to_deepgram": 0,
    "deepgram_events": 0,
    "deepgram_results": 0,
    "last_partial": "",
    "last_final": "",
    "last_error": "",
    "updated_at": None,
}

PRONUNCIATION_OVERRIDES = {
    "Zoro": "ˈzoʊroʊ",
    "TETRAX": "ˈtɛtræks",
    "Raspberry Pi": "ˈræzˌbɛri paɪ",
    "Deepgram": "ˈdiːpɡræm",
}

SPOKEN_REPLACEMENTS = {
    "Kowsalya ma'am": "kowsalyaa ma'am",
    "Kowsalya": "kowsalyaa",
    "Haroon Bashi": "Haarooon Baashi",
    "Haroon": "Haarooon",
}

VOICE_OPTIONS = [
    {
        "model": "aura-2-thalia-en",
        "name": "Thalia",
        "gender": "female",
        "tone": "clear, confident, energetic",
    },
    {
        "model": "aura-2-andromeda-en",
        "name": "Andromeda",
        "gender": "female",
        "tone": "casual, expressive, comfortable",
    },
    {
        "model": "aura-2-helena-en",
        "name": "Helena",
        "gender": "female",
        "tone": "caring, natural, friendly",
    },
    {
        "model": "aura-2-apollo-en",
        "name": "Apollo",
        "gender": "male",
        "tone": "confident, comfortable, casual",
    },
    {
        "model": "aura-2-arcas-en",
        "name": "Arcas",
        "gender": "male",
        "tone": "natural, smooth, clear",
    },
    {
        "model": "aura-2-aries-en",
        "name": "Aries",
        "gender": "male",
        "tone": "warm, energetic, caring",
    },
]


class VoiceConfigUpdate(BaseModel):
    model: str
    speed: float | None = None


class VoiceBenchmarkRequest(BaseModel):
    text: str = "Okay."


app.mount("/media", StaticFiles(directory=str(settings.data_dir)), name="media")


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _mp3_duration_seconds(raw: bytes) -> float:
    """Estimate playback duration of MP3 bytes at 128 kbps, with 1.5s buffer."""
    return len(raw) / (128_000 / 8) + 1.5


def _pcm_silence_frame(duration_ms: int = 100) -> bytes:
    samples = int(settings.audio_sample_rate * (duration_ms / 1000))
    return b"\x00" * samples * 2 * max(settings.audio_channels, 1)


def _estimate_speech_seconds(text: str) -> float:
    words = max(1, len(text.split()))
    return max(1.5, words / 2.3)


def _suppress_audio_for(seconds: float) -> None:
    global _ignore_audio_until
    _ignore_audio_until = max(_ignore_audio_until, time.monotonic() + max(0.0, seconds))


def _audio_suppressed() -> bool:
    return time.monotonic() < _ignore_audio_until


def _pcm16_peak(chunk: bytes) -> int:
    if len(chunk) < 2:
        return 0
    if len(chunk) % 2:
        chunk = chunk[:-1]
    try:
        samples = memoryview(chunk).cast("h")
        return max((abs(sample) for sample in samples), default=0)
    except Exception:
        return 0


def _deepgram_listen_url() -> str:
    params = {
        "model": settings.deepgram_stt_model,
        "language": settings.deepgram_stt_language,
        "encoding": "linear16",
        "sample_rate": str(settings.audio_sample_rate),
        "channels": str(settings.audio_channels),
        "interim_results": "true",
        "endpointing": str(settings.deepgram_endpointing_ms),
        "vad_events": "true",
        "smart_format": "true",
        "punctuate": "true",
        "numerals": "true",
    }
    return f"wss://api.deepgram.com/v1/listen?{urlencode(params)}"


def _partial_response_delay_ms(transcript: str) -> int | None:
    text = transcript.lower().strip(" .?!")
    if not text:
        return None
    words = text.split()
    if len(words) < 2:
        return None
    incomplete_endings = {
        "what", "who", "when", "where", "why", "how",
        "is", "are", "was", "were", "do", "does", "did", "can", "could",
        "the", "a", "an", "of", "in", "on", "for", "to", "with", "and", "or",
        "about", "like", "because", "why",
        "create", "created", "make", "made",
        "this", "that", "these", "those", "their", "his", "her", "its", "our",
    }
    if words[-1] in incomplete_endings:
        return None
    if len(words) >= 2 and " ".join(words[-2:]) in {"tell me", "what is", "who is", "how did", "how does", "why did", "why does"}:
        return None
    if re.search(r"\b(and|also)\s+(why|what|who|where|when|how|they|it|he|she|do|does|did|is|are)\s*$", text):
        return None
    if re.search(r"\b(who|what|where|when|how)\b.+\band\s+(why|what|who|where|when|how)\b", text):
        return 650
    direct = any(name in text for name in ("zoro", "zara", "soro", "zorro", "joro", "robot", "buddy"))
    starts_question = words[0] in {"what", "who", "when", "where", "why", "how"} or text.startswith(("can you", "could you", "do you", "is it", "are you", "tell me", "explain"))
    has_question = starts_question or any(
        phrase in text
        for phrase in (" what ", " who ", " when ", " where ", " why ", " how ", "your name", "capital of", "tell me", "explain")
    )
    commandish = any(
        phrase in text
        for phrase in (
            "move", "forward", "backward", "left", "right", "rotate", "stop",
            "attendance", "mark everyone", "say hi", "greet",
        )
    )
    if commandish and len(words) >= 2:
        return 120
    if text.endswith("?") and has_question and len(words) >= 2:
        return 50
    if has_question and len(words) >= 2:
        return 70 if direct or starts_question else 120
    if direct and len(words) >= 3:
        return 120
    return None


def _normalize_stt_text(transcript: str) -> str:
    text = " ".join(transcript.strip().split())
    if not text:
        return text
    replacements = (
        (" wat ", " what "),
        (" wht ", " what "),
        (" whaat ", " what "),
        (" y ", " why "),
        (" r ", " are "),
        (" u ", " you "),
        (" ur ", " your "),
        (" frnt ", " front "),
        (" infront ", " in front "),
        (" artificil ", " artificial "),
        (" inteligence ", " intelligence "),
        (" dinousur ", " dinosaur "),
        (" dinosaur era", "dinosaur era"),
    )
    padded = f" {text.lower()} "
    for old, new in replacements:
        padded = padded.replace(old, new)
    normalized = " ".join(padded.split())
    wake_fixes = {
        "zara": "Zoro",
        "soro": "Zoro",
        "zorro": "Zoro",
        "joro": "Zoro",
    }
    words = normalized.split()
    if words:
        first = words[0].strip(",.?!")
        if first in wake_fixes:
            words[0] = wake_fixes[first] + words[0][len(first):]
            normalized = " ".join(words)
    if text and text[-1] in ".?!":
        normalized = normalized.rstrip(".?!") + text[-1]
    return normalized


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _prepare_spoken_text(text: str, pronunciation: bool = True) -> str:
    spoken = " ".join(text.replace("\n", " ").split()).strip()
    for phrase in (
        "Feel free to ask.",
        "Feel free to ask me anything.",
        "Let me know if you need help.",
        "How can I assist you today?",
        "How can I help you today?",
    ):
        spoken = spoken.replace(phrase, "").strip()
    if spoken and spoken[-1] not in ".!?":
        spoken += "."
    for source, replacement in SPOKEN_REPLACEMENTS.items():
        spoken = spoken.replace(source, replacement)
    spoken = _apply_speech_pacing(spoken)
    spoken = re.sub(r"\b(yes|yeah|okay|alright|so|well)\b,?", r"\1,", spoken, flags=re.IGNORECASE)
    spoken = re.sub(r"([.!?])\s+", r"\1  ", spoken)
    spoken = re.sub(r",\s+", ", ", spoken)
    if pronunciation:
        for word, ipa in PRONUNCIATION_OVERRIDES.items():
            spoken = spoken.replace(word, f'\\{{"word": "{word}", "pronounce": "{ipa}"\\}}')
    return spoken


def _apply_speech_pacing(text: str) -> str:
    paced = text
    paced = re.sub(r"\bPlease\s+", "Please, ", paced)
    paced = re.sub(r"\bAttendance marked for:\s*", "Attendance marked for. ", paced, flags=re.IGNORECASE)
    paced = re.sub(r"\bObstacle detected\.\s*", "Obstacle detected. ", paced, flags=re.IGNORECASE)
    paced = re.sub(r"\bI cannot move ([a-z]+) safely\.", r"I cannot move \1 safely.", paced, flags=re.IGNORECASE)
    paced = re.sub(r"\s*,?\s+and\s+I do not want to bump", ", and I do not want to bump", paced, flags=re.IGNORECASE)
    return paced


def _speech_speed_for_text(text: str) -> float:
    base = max(0.65, min(1.0, float(settings.cartesia_voice_speed)))
    lowered = text.lower()
    if any(marker in lowered for marker in ("attendance marked", "please step aside", "please, step aside", "please move aside", "obstacle detected")):
        return max(0.74, base - 0.03)
    return base


def _cached_tts_path(prefix: str, text: str) -> Path:
    safe_prefix = "".join(ch for ch in prefix if ch.isalnum() or ch in ("-", "_"))[:40] or "tts"
    return _tts_cache_dir / f"{safe_prefix}-{_cache_key(text)}.mp3"


def _cached_pcm_path(prefix: str, text: str, sample_rate: int = 24000) -> Path:
    safe_prefix = "".join(ch for ch in prefix if ch.isalnum() or ch in ("-", "_"))[:40] or "tts"
    return _tts_cache_dir / f"{safe_prefix}-{sample_rate}-{_cache_key(text)}.raw"


def _cartesia_usage_path() -> Path:
    return settings.data_dir / "cartesia_usage.json"


def _load_cartesia_usage() -> dict[str, Any]:
    path = _cartesia_usage_path()
    if not path.exists():
        return {"remaining": {}, "exhausted": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("remaining", {})
            data.setdefault("exhausted", {})
            return data
    except Exception:
        pass
    return {"remaining": {}, "exhausted": {}}


def _save_cartesia_usage(data: dict[str, Any]) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _cartesia_usage_path().write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")


def _cartesia_pool() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    raw = (settings.cartesia_api_pool or "").strip()
    if raw:
        for index, item in enumerate(part.strip() for part in raw.split(";") if part.strip()):
            parts = [part.strip() for part in item.split("|")]
            if len(parts) < 2:
                continue
            label = parts[0] or f"key-{index + 1}"
            key = parts[1]
            try:
                starting_remaining = int(parts[2]) if len(parts) >= 3 and parts[2] else settings.cartesia_credit_limit
            except ValueError:
                starting_remaining = settings.cartesia_credit_limit
            entries.append({"label": label, "key": key, "starting_remaining": starting_remaining})
    elif settings.cartesia_api_key:
        entries.append({"label": "primary", "key": settings.cartesia_api_key, "starting_remaining": settings.cartesia_credit_limit})

    usage = _load_cartesia_usage()
    remaining = usage.get("remaining", {})
    exhausted = usage.get("exhausted", {})
    for entry in entries:
        label = entry["label"]
        entry["remaining"] = int(remaining.get(label, entry["starting_remaining"]))
        entry["exhausted"] = bool(exhausted.get(label, False))
        key = entry["key"]
        entry["masked_key"] = f"{key[:10]}...{key[-4:]}" if len(key) > 16 else "***"
    return entries


def _select_cartesia_key(char_count: int, attempted: set[str] | None = None) -> tuple[dict[str, Any] | None, bool]:
    attempted = attempted or set()
    pool = [item for item in _cartesia_pool() if item["label"] not in attempted and not item.get("exhausted")]
    if not pool:
        return None, False
    needed = char_count + max(0, settings.cartesia_credit_reserve)
    for item in pool:
        if item.get("remaining", 0) >= needed:
            return item, False
    best = max(pool, key=lambda item: item.get("remaining", 0))
    return best, True


def _mark_cartesia_used(label: str, char_count: int) -> None:
    data = _load_cartesia_usage()
    pool_by_label = {item["label"]: item for item in _cartesia_pool()}
    current = int(data.get("remaining", {}).get(label, pool_by_label.get(label, {}).get("starting_remaining", settings.cartesia_credit_limit)))
    data.setdefault("remaining", {})[label] = max(0, current - max(0, char_count))
    if data["remaining"][label] <= settings.cartesia_credit_reserve:
        data.setdefault("exhausted", {})[label] = True
    _save_cartesia_usage(data)


def _mark_cartesia_exhausted(label: str) -> None:
    data = _load_cartesia_usage()
    data.setdefault("remaining", {})[label] = 0
    data.setdefault("exhausted", {})[label] = True
    _save_cartesia_usage(data)


def _cartesia_error_is_quota(error: str | None) -> bool:
    text = (error or "").lower()
    # Only permanent account/credit failures should exhaust a key locally.
    # Transient rate/network errors must not disable the whole key pool.
    return any(token in text for token in ("quota", "credit", "402", "insufficient"))


async def _ensure_cached_tts(prefix: str, text: str) -> tuple[Path, bool]:
    _tts_cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cached_tts_path(prefix, text)
    if path.exists() and path.stat().st_size > 0:
        return path, False
    lock = _tts_cache_locks.setdefault(str(path), asyncio.Lock())
    async with lock:
        if path.exists() and path.stat().st_size > 0:
            return path, False
        audio_path, audio_bytes = await voice.synthesize(_prepare_spoken_text(text))
        raw = audio_bytes
        if not raw and audio_path:
            source = Path(audio_path)
            if source.exists():
                raw = source.read_bytes()
        if not raw:
            raise RuntimeError("Deepgram returned no audio bytes.")
        path.write_bytes(raw)
        return path, True


async def _ensure_cached_pcm_tts(prefix: str, text: str, sample_rate: int = 24000) -> tuple[Path, bool]:
    if not settings.deepgram_api_key:
        raise RuntimeError("Deepgram API key is not configured.")
    _tts_cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cached_pcm_path(prefix, text, sample_rate)
    if path.exists() and path.stat().st_size > 0:
        return path, False
    lock = _tts_cache_locks.setdefault(str(path), asyncio.Lock())
    async with lock:
        if path.exists() and path.stat().st_size > 0:
            return path, False
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.deepgram.com/v1/speak",
                params={
                    "model": settings.deepgram_tts_model,
                    "encoding": "linear16",
                    "sample_rate": str(sample_rate),
                    "speed": str(settings.deepgram_tts_speed),
                },
                headers={
                    "Authorization": f"Token {settings.deepgram_api_key}",
                    "Content-Type": "application/json",
                },
                json={"text": _prepare_spoken_text(text)},
            )
            response.raise_for_status()
        path.write_bytes(response.content)
        return path, True


async def _release_speaking_after(raw: bytes, token: int) -> None:
    global _zoro_is_speaking
    await asyncio.sleep(min(_mp3_duration_seconds(raw), 12.0))
    if token == _speaking_token:
        _zoro_is_speaking = False


async def _release_speaking_after_seconds(seconds: float, token: int) -> None:
    global _zoro_is_speaking
    await asyncio.sleep(min(max(seconds, 0.2), 12.0))
    if token == _speaking_token:
        _zoro_is_speaking = False


async def _send_cached_audio_to_pi_async(path: Path) -> dict:
    global _zoro_is_speaking, _speaking_token
    raw = path.read_bytes()
    started = time.monotonic()
    try:
        _zoro_is_speaking = True
        _speaking_token += 1
        token = _speaking_token
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{settings.pi_base_url.rstrip('/')}/speaker/play-async",
                content=raw,
                headers={"Content-Type": "audio/mpeg"},
            )
            response.raise_for_status()
            result = response.json()
        asyncio.create_task(_release_speaking_after(raw, token))
        return {
            "ok": True,
            "bytes": len(raw),
            "request_ms": round((time.monotonic() - started) * 1000),
            "pi": result,
        }
    except Exception as exc:
        _zoro_is_speaking = False
        return {"ok": False, "bytes": len(raw), "error": str(exc)}


async def _send_wav_to_pi_async(path: Path) -> dict:
    global _zoro_is_speaking, _speaking_token
    raw = path.read_bytes()
    started = time.monotonic()
    try:
        _zoro_is_speaking = True
        _speaking_token += 1
        token = _speaking_token
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{settings.pi_base_url.rstrip('/')}/speaker/play-async",
                content=raw,
                headers={"Content-Type": "audio/wav"},
            )
            response.raise_for_status()
            result = response.json()
        asyncio.create_task(_release_speaking_after_seconds(2.5, token))
        return {
            "ok": True,
            "bytes": len(raw),
            "request_ms": round((time.monotonic() - started) * 1000),
            "pi": result,
        }
    except Exception as exc:
        _zoro_is_speaking = False
        return {"ok": False, "bytes": len(raw), "error": str(exc)}


async def _synthesize_sapi_wav(text: str) -> dict:
    _tts_cache_dir.mkdir(parents=True, exist_ok=True)
    path = _tts_cache_dir / f"sapi-{_cache_key(text)}.wav"
    if path.exists() and path.stat().st_size > 0:
        return {"ok": True, "path": path, "cached": True, "synth_ms": 0}
    command = (
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$synth.Rate = 2; "
        "$synth.Volume = 100; "
        "$synth.SetOutputToWaveFile($env:ZORO_SAPI_OUT); "
        "$synth.Speak($env:ZORO_SAPI_TEXT); "
        "$synth.Dispose();"
    )
    env = {**os.environ, "ZORO_SAPI_TEXT": text, "ZORO_SAPI_OUT": str(path)}
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        "powershell",
        "-NoProfile",
        "-Command",
        command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    _, stderr = await proc.communicate()
    synth_ms = round((time.monotonic() - started) * 1000)
    if proc.returncode != 0 or not path.exists() or path.stat().st_size == 0:
        error = stderr.decode("utf-8", errors="ignore").strip() or f"SAPI exited {proc.returncode}"
        return {"ok": False, "error": error, "synth_ms": synth_ms}
    return {"ok": True, "path": path, "cached": False, "synth_ms": synth_ms}


async def _speak_live_sentence(sentence: str) -> dict:
    _suppress_audio_for(min(_estimate_speech_seconds(sentence) + 0.6, 8.0))
    async with _speaker_lock:
        result = await _stream_cartesia_tts_to_pi(sentence)
        _suppress_audio_for(0.65)
        if not result.get("ok"):
            print(f"[TTS] Cartesia failed; no Deepgram fallback: {result.get('error') or result.get('producer_error')}")
        return result


async def _send_cached_pcm_to_pi_async(path: Path, sample_rate: int = 24000) -> dict:
    global _zoro_is_speaking, _speaking_token
    raw = path.read_bytes()
    started = time.monotonic()
    try:
        _zoro_is_speaking = True
        _speaking_token += 1
        token = _speaking_token
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{settings.pi_base_url.rstrip('/')}/speaker/pcm-play-async",
                params={"sample_rate": sample_rate},
                content=raw,
                headers={"Content-Type": "application/octet-stream"},
            )
            response.raise_for_status()
            result = response.json()
        duration_seconds = len(raw) / float(sample_rate * 2)
        asyncio.create_task(_release_speaking_after_seconds(duration_seconds + 0.2, token))
        return {
            "ok": True,
            "bytes": len(raw),
            "request_ms": round((time.monotonic() - started) * 1000),
            "pi": result,
        }
    except Exception as exc:
        _zoro_is_speaking = False
        return {"ok": False, "bytes": len(raw), "error": str(exc)}


async def _play_fast_ack() -> dict:
    started = time.monotonic()
    path, generated = await _ensure_cached_pcm_tts("fast-ack-helena-v2", "Okay.")
    result = await _send_cached_pcm_to_pi_async(path)
    return {
        **result,
        "generated": generated,
        "total_ms": round((time.monotonic() - started) * 1000),
    }


async def _play_cached_filler(text: str) -> dict:
    started = time.monotonic()
    label = "filler-" + _cache_key(text)
    path, generated = await _ensure_cached_pcm_tts(label, text)
    result = await _send_cached_pcm_to_pi_async(path)
    return {
        **result,
        "engine": "fast_cached_filler",
        "generated": generated,
        "total_ms": round((time.monotonic() - started) * 1000),
    }


async def _send_tts_to_pi(audio_path, text=None, audio_bytes=None):
    global _zoro_is_speaking, _speaking_token
    raw = audio_bytes
    if not raw and audio_path:
        path = Path(audio_path)
        if path.exists():
            raw = path.read_bytes()
    if not raw:
        return {"ok": False, "error": "No audio bytes to send."}

    print(f"[TTS] Sending {len(raw)} bytes to Pi speaker...")
    t0 = time.monotonic()
    try:
        _zoro_is_speaking = True
        _speaking_token += 1
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{settings.pi_base_url.rstrip('/')}/speaker/play",
                content=raw,
                headers={"Content-Type": "audio/mpeg"},
            )
            response.raise_for_status()
            elapsed = time.monotonic() - t0
            print(f"[TTS] Pi responded in {elapsed:.2f}s")
            return {"ok": True, "bytes": len(raw), "pi_playback_ms": round(elapsed * 1000)}

    except Exception as exc:
        print(f"Warning: could not send TTS audio to Pi speaker: {exc}")
        return {"ok": False, "bytes": len(raw), "error": str(exc)}
    finally:
        _zoro_is_speaking = False


async def _stream_tts_text_to_pi(text: str) -> dict:
    global _zoro_is_speaking, _speaking_token
    if not settings.deepgram_api_key:
        return {"ok": False, "error": "Deepgram API key is not configured."}

    deepgram_url = "https://api.deepgram.com/v1/speak"
    deepgram_headers = {
        "Authorization": f"Token {settings.deepgram_api_key}",
        "Content-Type": "application/json",
    }
    spoken_text = _prepare_spoken_text(text)
    deepgram_params = {"model": settings.deepgram_tts_model, "speed": str(settings.deepgram_tts_speed)}
    started = time.monotonic()
    first_byte_ms = None
    total_bytes = 0

    async def audio_chunks():
        nonlocal first_byte_ms, total_bytes
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                deepgram_url,
                params=deepgram_params,
                headers=deepgram_headers,
                json={"text": spoken_text},
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    if first_byte_ms is None:
                        first_byte_ms = round((time.monotonic() - started) * 1000)
                    total_bytes += len(chunk)
                    yield chunk

    try:
        _zoro_is_speaking = True
        _speaking_token += 1
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(
                f"{settings.pi_base_url.rstrip('/')}/speaker/stream",
                content=audio_chunks(),
                headers={"Content-Type": "audio/mpeg"},
            )
            response.raise_for_status()
            pi_result = response.json()
        return {
            "ok": True,
            "deepgram_first_byte_ms": first_byte_ms,
            "total_stream_ms": round((time.monotonic() - started) * 1000),
            "bytes": total_bytes,
            "pi": pi_result,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "deepgram_first_byte_ms": first_byte_ms, "bytes": total_bytes}
    finally:
        _zoro_is_speaking = False


async def _connect_cartesia_tts(credential: dict[str, Any] | None = None):
    global _cartesia_ws
    credential = credential or _select_cartesia_key(1)[0]
    if credential is None:
        raise RuntimeError("No Cartesia API key is configured.")
    label = credential["label"]
    async with _cartesia_lock:
        ws = _cartesia_ws_pool.get(label)
        if ws is not None:
            try:
                if not ws.closed:
                    return ws
            except AttributeError:
                return ws
            _cartesia_ws_pool.pop(label, None)
        headers = {
            "X-API-Key": credential["key"],
            "Cartesia-Version": settings.cartesia_version,
        }
        url = "wss://api.cartesia.ai/tts/websocket"
        try:
            ws = await websockets.connect(url, additional_headers=headers, ping_interval=20, open_timeout=8)
        except TypeError:
            ws = await websockets.connect(url, extra_headers=headers, ping_interval=20, open_timeout=8)
        _cartesia_ws_pool[label] = ws
        _cartesia_ws = ws
        return ws


@app.on_event("startup")
async def _warm_voice_services_on_startup() -> None:
    async def warm() -> None:
        await asyncio.sleep(0.5)
        if settings.live_tts_mode.lower() != "cartesia_stream":
            return
        credential, _ = _select_cartesia_key(1)
        if credential is None:
            return
        try:
            with contextlib.suppress(Exception):
                await _ensure_cached_pcm_tts("fast-ack-helena-v2", "Okay.")
                await _ensure_cached_pcm_tts("filler-" + _cache_key("Hmm."), "Hmm.")
            await _connect_cartesia_tts(credential)
            warmed_at = datetime.datetime.now().isoformat(timespec="seconds")
            _voice_diagnostics["cartesia_warmed_at"] = warmed_at
            _voice_diagnostics["updated_at"] = warmed_at
        except Exception as exc:
            _voice_diagnostics["last_error"] = f"cartesia warmup: {exc}"
            _voice_diagnostics["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")

    asyncio.create_task(warm())


@app.on_event("startup")
async def _start_period_attendance_scanner() -> None:
    asyncio.create_task(_period_attendance_loop())


async def _stream_cartesia_tts_to_pi_once(text: str, credential: dict[str, Any]) -> dict:
    global _zoro_is_speaking, _speaking_token, _cartesia_ws
    if not settings.cartesia_voice_id:
        return {"ok": False, "error": "Cartesia voice id is not configured."}

    sample_rate = int(settings.cartesia_sample_rate or 24000)
    context_id = str(uuid.uuid4())
    spoken_text = _prepare_spoken_text(text, pronunciation=False)
    payload = {
        "model_id": settings.cartesia_tts_model,
        "transcript": spoken_text,
        "voice": {"mode": "id", "id": settings.cartesia_voice_id},
        "language": "en",
        "context_id": context_id,
        "output_format": {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": sample_rate,
        },
        "generation_config": {
            "speed": _speech_speed_for_text(spoken_text),
            "emotion": settings.cartesia_voice_emotion,
        },
        "add_timestamps": False,
        "continue": False,
    }
    started = time.monotonic()
    first_byte_ms = None
    first_pi_write_ms = None
    total_bytes = 0
    producer_error = None
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=32)

    async def produce() -> None:
        nonlocal first_byte_ms, total_bytes, producer_error
        try:
            ws = await _connect_cartesia_tts(credential)
            await ws.send(json.dumps(payload))
            while True:
                message = await ws.recv()
                if isinstance(message, bytes):
                    if first_byte_ms is None:
                        first_byte_ms = round((time.monotonic() - started) * 1000)
                    total_bytes += len(message)
                    await queue.put(message)
                    continue
                try:
                    event = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if event.get("context_id") not in {None, context_id}:
                    continue
                if event.get("type") == "chunk" and event.get("data"):
                    raw = base64.b64decode(event["data"])
                    if first_byte_ms is None:
                        first_byte_ms = round((time.monotonic() - started) * 1000)
                    total_bytes += len(raw)
                    await queue.put(raw)
                elif event.get("type") == "error":
                    producer_error = event.get("message") or event.get("error_code") or "Cartesia TTS error"
                    break
                elif event.get("done") is True or event.get("type") == "done":
                    break
        except Exception as exc:
            producer_error = str(exc)
            _cartesia_ws_pool.pop(credential["label"], None)
            _cartesia_ws = None
        finally:
            await queue.put(None)

    async def audio_chunks():
        nonlocal first_pi_write_ms
        prebuffer = bytearray()
        # Cartesia can emit uneven websocket chunks. Start playback with a
        # small cushion so ALSA does not starve mid-sentence on Wi-Fi jitter.
        target_prebuffer_bytes = int(sample_rate * 2 * 0.32)
        prebuffer_deadline = time.monotonic() + 0.55
        while True:
            chunk = await queue.get()
            if chunk is None:
                if prebuffer:
                    if first_pi_write_ms is None:
                        first_pi_write_ms = round((time.monotonic() - started) * 1000)
                    yield bytes(prebuffer)
                break
            if first_pi_write_ms is None:
                prebuffer.extend(chunk)
                if len(prebuffer) < target_prebuffer_bytes and time.monotonic() < prebuffer_deadline:
                    continue
                first_pi_write_ms = round((time.monotonic() - started) * 1000)
                yield bytes(prebuffer)
                prebuffer.clear()
                continue
            if first_pi_write_ms is None:
                first_pi_write_ms = round((time.monotonic() - started) * 1000)
            yield chunk

    try:
        _zoro_is_speaking = True
        _speaking_token += 1
        producer_task = asyncio.create_task(produce())
        timeout = httpx.Timeout(connect=8.0, read=None, write=None, pool=8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.pi_base_url.rstrip('/')}/speaker/pcm-stream",
                params={"sample_rate": sample_rate},
                content=audio_chunks(),
                headers={"Content-Type": "application/octet-stream"},
            )
            response.raise_for_status()
            pi_result = response.json()
        await producer_task
        if producer_error:
            raise RuntimeError(producer_error)
        if total_bytes <= 0:
            raise RuntimeError("Cartesia produced no PCM audio.")
        return {
            "ok": producer_error is None and pi_result.get("ok", False),
            "engine": "cartesia_stream",
            "cartesia_key_label": credential["label"],
            "cartesia_key_remaining": credential.get("remaining"),
            "cartesia_first_byte_ms": first_byte_ms,
            "first_pi_write_ms": first_pi_write_ms,
            "total_stream_ms": round((time.monotonic() - started) * 1000),
            "bytes": total_bytes,
            "encoding": "pcm_s16le",
            "sample_rate": sample_rate,
            "pi": pi_result,
            "error": producer_error,
        }
    except Exception as exc:
        with contextlib.suppress(Exception):
            if "producer_task" in locals() and not producer_task.done():
                producer_task.cancel()
                await producer_task
        return {
            "ok": False,
            "engine": "cartesia_stream",
            "cartesia_key_label": credential["label"],
            "error": str(exc),
            "producer_error": producer_error,
            "cartesia_first_byte_ms": first_byte_ms,
            "first_pi_write_ms": first_pi_write_ms,
            "bytes": total_bytes,
        }
    finally:
        _zoro_is_speaking = False


async def _stream_cartesia_tts_to_pi(text: str) -> dict:
    spoken = _prepare_spoken_text(text, pronunciation=False)
    char_count = len(spoken)
    attempted: set[str] = set()
    last_result: dict[str, Any] = {"ok": False, "error": "No Cartesia key attempted."}
    switched = False

    for _ in range(max(1, len(_cartesia_pool()))):
        credential, low_credit = _select_cartesia_key(char_count, attempted)
        if credential is None:
            return {
                "ok": False,
                "engine": "cartesia_stream",
                "error": "All Cartesia API keys are exhausted or unavailable.",
                "attempted_keys": sorted(attempted),
                "last_result": last_result,
            }
        attempted.add(credential["label"])
        speak_text = text
        if switched or low_credit:
            speak_text = "My voice credits are low, switching voice channel. " + text
        result = await _stream_cartesia_tts_to_pi_once(speak_text, credential)
        result["attempted_keys"] = sorted(attempted)
        if result.get("ok"):
            _mark_cartesia_used(credential["label"], len(_prepare_spoken_text(speak_text, pronunciation=False)))
            return result
        last_result = result
        if _cartesia_error_is_quota(result.get("error") or result.get("producer_error")) or result.get("bytes", 0) == 0:
            if _cartesia_error_is_quota(result.get("error") or result.get("producer_error")):
                _mark_cartesia_exhausted(credential["label"])
            switched = True
            continue
        return result

    return {
        "ok": False,
        "engine": "cartesia_stream",
        "error": "Cartesia failover could not complete.",
        "attempted_keys": sorted(attempted),
        "last_result": last_result,
    }


async def _stream_tts_text_to_pi_ws(text: str) -> dict:
    global _zoro_is_speaking, _speaking_token
    if not settings.deepgram_api_key:
        return {"ok": False, "error": "Deepgram API key is not configured."}

    sample_rate = 24000
    params = {
        "model": settings.deepgram_tts_model,
        "encoding": "linear16",
        "sample_rate": str(sample_rate),
    }
    url = f"wss://api.deepgram.com/v1/speak?{urlencode(params)}"
    headers = {"Authorization": f"Token {settings.deepgram_api_key}"}
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=20)
    started = time.monotonic()
    first_byte_ms = None
    total_bytes = 0
    producer_error = None

    async def produce() -> None:
        nonlocal first_byte_ms, total_bytes, producer_error
        try:
            try:
                ws = await websockets.connect(url, additional_headers=headers, ping_interval=None, open_timeout=15)
            except TypeError:
                ws = await websockets.connect(url, extra_headers=headers, ping_interval=None, open_timeout=15)
            async with ws:
                await ws.send(json.dumps({"type": "Speak", "text": text}))
                await ws.send(json.dumps({"type": "Flush"}))
                async for message in ws:
                    if isinstance(message, bytes):
                        if first_byte_ms is None:
                            first_byte_ms = round((time.monotonic() - started) * 1000)
                        total_bytes += len(message)
                        await queue.put(message)
                        continue
                    try:
                        event = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "Flushed":
                        break
                with contextlib.suppress(Exception):
                    await ws.send(json.dumps({"type": "Close"}))
        except Exception as exc:
            producer_error = str(exc)
        finally:
            await queue.put(None)

    async def audio_chunks():
        while True:
            chunk = await queue.get()
            if chunk is None:
                return
            yield chunk

    try:
        _zoro_is_speaking = True
        _speaking_token += 1
        producer_task = asyncio.create_task(produce())
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(
                f"{settings.pi_base_url.rstrip('/')}/speaker/pcm-stream?sample_rate={sample_rate}",
                content=audio_chunks(),
                headers={"Content-Type": "application/octet-stream"},
            )
            response.raise_for_status()
            pi_result = response.json()
        await producer_task
        return {
            "ok": producer_error is None and pi_result.get("ok", False),
            "deepgram_first_byte_ms": first_byte_ms,
            "total_stream_ms": round((time.monotonic() - started) * 1000),
            "bytes": total_bytes,
            "encoding": "linear16",
            "sample_rate": sample_rate,
            "pi": pi_result,
            "error": producer_error,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "producer_error": producer_error,
            "deepgram_first_byte_ms": first_byte_ms,
            "bytes": total_bytes,
        }
    finally:
        _zoro_is_speaking = False


def _recognized_student_name() -> str | None:
    if perception.state.faces:
        return perception.state.faces[0].get("name")
    return None


def _attendance_period_key(now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    minutes = now.hour * 60 + now.minute
    period = (minutes // max(1, settings.attendance_auto_scan_interval_minutes)) + 1
    return f"{now.date().isoformat()}_period_{period:02d}"


def _attendance_next_run_after(delay_seconds: float | None = None) -> str:
    delay = settings.attendance_auto_scan_interval_minutes * 60 if delay_seconds is None else delay_seconds
    return (datetime.datetime.now() + datetime.timedelta(seconds=delay)).isoformat(timespec="seconds")


def _attendance_spoken_result(result: dict) -> str:
    marked = [str(name) for name in result.get("marked") or [] if str(name).strip()]
    if marked:
        return "Attendance marked for: " + ", ".join(marked) + "."
    return "Attendance scan completed, but I could not confidently recognize any enrolled students."


async def _mark_attendance_from_robot_frame(session_key: str | None = None) -> dict:
    if perception.latest_jpeg:
        image_bytes = perception.latest_jpeg
    else:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{settings.pi_base_url.rstrip('/')}/snapshot.jpg")
            response.raise_for_status()
        image_bytes = response.content
        await perception.process_jpeg(response.content)
    result = attendance.recognize_and_mark(image_bytes)
    for name in result.get("marked", []):
        save_attendance_record(name, status="present", date=session_key)
    return {
        **result,
        "session_key": session_key or datetime.date.today().isoformat(),
        "periodic": bool(session_key),
    }


async def _mark_attendance_scan_window(session_key: str | None = None, duration_seconds: float = 3.0) -> dict:
    deadline = time.monotonic() + max(0.5, min(float(duration_seconds), 10.0))
    merged_marked: list[str] = []
    best_result: dict[str, Any] = {"available": False, "marked": [], "faces_seen": 0}
    while time.monotonic() < deadline:
        result = await _mark_attendance_from_robot_frame(session_key)
        best_result = result if int(result.get("faces_seen") or 0) >= int(best_result.get("faces_seen") or 0) else best_result
        for name in result.get("marked", []) or []:
            if name not in merged_marked:
                merged_marked.append(name)
        await asyncio.sleep(0.35)
    return {
        **best_result,
        "marked": merged_marked,
        "scan_seconds": round(max(0.5, min(float(duration_seconds), 10.0)), 1),
        "session_key": session_key or datetime.date.today().isoformat(),
        "periodic": bool(session_key),
    }


async def _announce_and_mark_attendance(session_key: str | None = None) -> dict:
    await _speak_live_sentence("I am going to take attendance now. Please keep your face up and stay still.")
    result = await _mark_attendance_scan_window(session_key, 3.0)
    await _speak_live_sentence(_attendance_spoken_result(result))
    return result


async def _period_attendance_loop() -> None:
    await asyncio.sleep(max(1, settings.attendance_auto_scan_initial_delay_seconds))
    while True:
        interval_seconds = max(
            3,
            int(_attendance_auto_scan.get("interval_seconds") or settings.attendance_auto_scan_interval_minutes * 60),
        )
        if not _attendance_auto_scan.get("enabled"):
            _attendance_auto_scan["next_run"] = None
            await asyncio.sleep(10)
            continue
        _attendance_auto_scan["next_run"] = _attendance_next_run_after(0)
        try:
            session_key = _attendance_period_key()
            if _attendance_auto_scan.get("last_session_key") == session_key:
                _attendance_auto_scan["next_run"] = _attendance_next_run_after(interval_seconds)
                await asyncio.sleep(interval_seconds)
                continue
            result = await _announce_and_mark_attendance(session_key)
            now = datetime.datetime.now().isoformat(timespec="seconds")
            _attendance_auto_scan.update({
                "last_run": now,
                "last_result": result,
                "last_error": "",
                "next_run": _attendance_next_run_after(interval_seconds),
                "last_session_key": session_key,
            })
            marked = result.get("marked") or []
            notifications.add(
                "period_attendance",
                f"Period attendance scan completed. Marked {len(marked)} student(s).",
                "info",
                {"session_key": session_key, "marked": marked},
            )
        except Exception as exc:
            _attendance_auto_scan.update({
                "last_error": f"{type(exc).__name__}: {exc}",
                "next_run": _attendance_next_run_after(interval_seconds),
            })
        await asyncio.sleep(interval_seconds)


# ═══════════════════════════════════════════════════════════════════════════════
# EXISTING ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health() -> dict:
    pi = None
    try:
        pi = await robot.health()
    except Exception as exc:
        pi = {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "service": "laptop_backend",
        "pi_base_url": settings.pi_base_url,
        "pi": pi,
        "attendance": {"available": True, "known_faces": len(attendance.known_names)},
        "brain": {
            "model": settings.openai_model,
            "openai_configured": bool(settings.openai_api_key),
            "architecture": "laptop_brain_pi_body",
            "perception": perception.snapshot(),
        },
    }


@app.get("/metrics")
async def metrics() -> dict:
    today_path = attendance.today_csv_path()
    present = 0
    if today_path.exists():
        present = max(0, len(today_path.read_text(encoding="utf-8").splitlines()) - 1)
    return {
        "robot_name": "zoro2026",
        "present_today": present,
        "syllabus_files": len(list(Path(settings.syllabus_dir).glob("*"))),
        "known_faces": len(attendance.known_names),
        "conversation_count": len(conversation_store.latest(5000)),
        "memory_count": len(memory.latest(5000)),
    }


def _obstacle_voice_message(direction: str) -> str | None:
    blocker = perception.blockage_for_direction(direction)
    if not blocker:
        return None
    label = str(blocker.get("label") or "obstacle").lower()
    if label == "person":
        return "Please step aside for a moment. I need to go forward, and I do not want to bump into you."
    if direction == "forward":
        return "Obstacle detected. I cannot move forward safely. I am checking left and right for another path."
    article = "an" if label[:1] in {"a", "e", "i", "o", "u"} else "a"
    return f"Obstacle detected. There is {article} {label} in my way, so I cannot move {direction} safely."


def _obstacle_response_message(direction: str, fallback: str) -> str:
    return _obstacle_voice_message(direction) or fallback


async def _scan_around_for_path(direction: str) -> None:
    blocker = perception.blockage_for_direction(direction)
    if direction != "forward" or not blocker or str(blocker.get("label") or "").lower() == "person":
        return
    clear_options = [item for item in perception.clear_directions() if item in {"left", "right"}]
    scan_direction = clear_options[0] if clear_options else "left"
    try:
        await robot.move(scan_direction, 0.35)
        perception.update_motion_estimate(scan_direction, 0.35)
        await asyncio.sleep(0.35)
        await robot.stop()
        perception.update_motion_estimate("stop", 0.0)
    except Exception as exc:
        print(f"Warning: obstacle scan turn failed: {exc}")


async def _speak_obstacle_warning(direction: str) -> None:
    message = _obstacle_voice_message(direction)
    if not message:
        return
    now = time.monotonic()
    if message == _last_obstacle_voice.get("message") and now - float(_last_obstacle_voice.get("at") or 0) < 8:
        return
    _last_obstacle_voice.update({"message": message, "at": now})
    await _speak_live_sentence(message)
    await _scan_around_for_path(direction)


@app.post("/robot/move")
async def move_robot(request: MoveRequest) -> dict:
    if request.direction == "stop":
        self_model.update_motion("stop")
        perception.update_motion_estimate("stop", 0.0)
        return await robot.stop()
    clear, reason = perception.movement_clear(request.direction)
    if not clear:
        await robot.stop()
        perception.update_motion_estimate("stop", 0.0)
        asyncio.create_task(_speak_obstacle_warning(request.direction))
        return {"ok": False, "blocked": True, "message": _obstacle_response_message(request.direction, reason)}
    self_model.update_motion(request.direction)
    result = await robot.move(request.direction, request.speed)
    if result.get("ok", True):
        perception.update_motion_estimate(request.direction, request.speed)
    result["safety"] = {"blocked": False, "message": reason}
    return result


@app.post("/robot/stop")
async def stop_robot() -> dict:
    self_model.update_motion("stop")
    perception.update_motion_estimate("stop", 0.0)
    return await robot.stop()


@app.get("/robot/video.mjpeg")
async def video_proxy() -> StreamingResponse:
    async def stream():
        try:
            if perception.state.video_connected:
                last_frame = None
                while True:
                    if perception.latest_jpeg and perception.latest_jpeg is not last_frame:
                        last_frame = perception.latest_jpeg
                        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + perception.latest_jpeg + b"\r\n"
                    await asyncio.sleep(0.04)
            else:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("GET", f"{settings.pi_base_url.rstrip('/')}/video.mjpeg") as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes():
                            yield chunk
        except (asyncio.CancelledError, GeneratorExit):
            return
        except httpx.HTTPError:
            return
    return StreamingResponse(stream(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/robot/snapshot.jpg")
async def snapshot_proxy() -> FileResponse:
    path = settings.data_dir / "latest_snapshot.jpg"
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not perception.latest_jpeg:
        await asyncio.sleep(0.1)
    if perception.latest_jpeg:
        path.write_bytes(perception.latest_jpeg)
    else:
        path.write_bytes(_fallback_camera_jpeg())
    return FileResponse(path, media_type="image/jpeg", filename="snapshot.jpg")


def _fallback_camera_jpeg() -> bytes:
    import cv2
    import numpy as np

    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.putText(
        frame,
        "Camera stream warming up",
        (95, 180),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (255, 255, 255),
        2,
    )
    ok, buffer = cv2.imencode(".jpg", frame)
    return buffer.tobytes() if ok else b""


def _camera_frame_is_fresh(max_age_seconds: float = 8.0) -> bool:
    if not perception.latest_jpeg or not perception.state.last_frame_at:
        return False
    try:
        frame_at = datetime.datetime.fromisoformat(perception.state.last_frame_at)
    except ValueError:
        return False
    return (datetime.datetime.now() - frame_at).total_seconds() <= max_age_seconds


@app.websocket("/ws/robot-control")
async def robot_control_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    key_map = {"w": "forward", "s": "backward", "a": "left", "d": "right", " ": "stop"}
    try:
        while True:
            raw_message = await websocket.receive_text()
            speed = 0.65
            try:
                payload = json.loads(raw_message)
            except json.JSONDecodeError:
                payload = raw_message
            if isinstance(payload, dict):
                message = str(payload.get("direction", "stop")).lower()
                try:
                    speed = max(0.0, min(float(payload.get("speed", speed)), 1.0))
                except (TypeError, ValueError):
                    speed = 0.65
            else:
                message = str(payload).lower()
            direction = key_map.get(message, message)
            if direction not in {"forward", "backward", "left", "right", "rotate", "stop"}:
                await websocket.send_json({"ok": False, "error": "Unknown command."})
                continue
            if direction == "stop":
                self_model.update_motion("stop")
                perception.update_motion_estimate("stop", 0.0)
                result = await robot.stop()
            else:
                clear, reason = perception.movement_clear(direction)
                if not clear:
                    await robot.stop()
                    perception.update_motion_estimate("stop", 0.0)
                    asyncio.create_task(_speak_obstacle_warning(direction))
                    result = {"ok": False, "blocked": True, "message": _obstacle_response_message(direction, reason)}
                else:
                    self_model.update_motion(direction)
                    result = await robot.move(direction, speed)
                    if result.get("ok", True):
                        perception.update_motion_estimate(direction, speed)
                    result["safety"] = {"blocked": False, "message": reason}
            await websocket.send_json(result)
    except WebSocketDisconnect:
        await robot.stop()


@app.post("/attendance/reload-faces")
async def reload_faces() -> dict:
    return attendance.reload_faces()


@app.post("/attendance/mark-from-frame")
async def mark_from_frame(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    result = attendance.recognize_and_mark(data)
    for name in result.get("marked", []):
        save_attendance_record(name, status="present")
    return result


@app.post("/attendance/mark-from-robot")
async def mark_from_robot() -> dict:
    return await _announce_and_mark_attendance()


@app.get("/attendance/auto-scan")
async def attendance_auto_scan_status() -> dict:
    return dict(_attendance_auto_scan)


@app.post("/attendance/auto-scan")
async def attendance_auto_scan_update(data: dict) -> dict:
    if "enabled" in data:
        _attendance_auto_scan["enabled"] = bool(data["enabled"])
    if "interval_minutes" in data:
        interval = max(1, min(240, int(data["interval_minutes"])))
        _attendance_auto_scan["interval_minutes"] = interval
        settings.attendance_auto_scan_interval_minutes = interval
    if "interval_seconds" in data:
        interval_seconds = max(3, min(14400, int(data["interval_seconds"])))
        interval_minutes = max(1, round(interval_seconds / 60))
        _attendance_auto_scan["interval_seconds"] = interval_seconds
        _attendance_auto_scan["interval_minutes"] = interval_minutes
        settings.attendance_auto_scan_interval_minutes = interval_minutes
    if _attendance_auto_scan.get("enabled"):
        _attendance_auto_scan["next_run"] = _attendance_next_run_after(
            int(_attendance_auto_scan.get("interval_seconds") or settings.attendance_auto_scan_interval_minutes * 60)
        )
    else:
        _attendance_auto_scan["next_run"] = None
    return dict(_attendance_auto_scan)


@app.post("/attendance/auto-scan/run-now")
async def attendance_auto_scan_run_now(duration_seconds: float = 3.0) -> dict:
    session_key = _attendance_period_key()
    await _speak_live_sentence("I am going to take attendance now. Please keep your face up and stay still for three seconds.")
    result = await _mark_attendance_scan_window(session_key, duration_seconds)
    await _speak_live_sentence(_attendance_spoken_result(result))
    _attendance_auto_scan.update({
        "last_run": datetime.datetime.now().isoformat(timespec="seconds"),
        "last_result": result,
        "last_error": "",
        "next_run": _attendance_next_run_after(settings.attendance_auto_scan_interval_minutes * 60),
        "last_session_key": session_key,
    })
    return result


@app.get("/attendance/today.csv")
async def today_attendance() -> FileResponse:
    path = attendance.today_csv_path()
    if not path.exists():
        path.write_text("name,time\n", encoding="utf-8")
    return FileResponse(path, filename=path.name, media_type="text/csv")


@app.post("/conversation/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    student_name = request.student_name or _recognized_student_name()
    result = await brain.handle_transcript(request.question, student_name)
    answer = result["answer"]
    conversation_store.add(request.question, answer, student_name)
    save_transcript_session(
        messages=[
            {"role": "user", "content": request.question},
            {"role": "assistant", "content": answer},
        ],
        topics=[student_name or "Voice"],
        student_name=student_name or "Unknown student",
    )
    return AskResponse(answer=answer)


@app.get("/conversation/history")
async def conversation_history(limit: int = 50) -> dict:
    return {"items": conversation_store.latest(max(1, min(limit, 500)))}


@app.post("/voice/ask-audio")
async def ask_audio(file: UploadFile = File(...)) -> dict:
    audio = await file.read()
    transcript = await voice.transcribe(audio, file.content_type or "audio/wav")
    if not transcript:
        raise HTTPException(status_code=400, detail="No transcript returned from Deepgram.")
    student_name = _recognized_student_name()
    result = await brain.handle_transcript(transcript, student_name)
    if result.get("ignored"):
        return result
    answer = result["answer"]
    conversation_store.add(transcript, answer, student_name)
    save_transcript_session(
        messages=[
            {"role": "user", "content": transcript},
            {"role": "assistant", "content": answer},
        ],
        topics=[student_name or "Voice"],
        student_name=student_name or "Unknown student",
    )
    await _speak_live_sentence(answer)
    return {
        "transcript": transcript,
        "answer": answer,
        "intent": result["intent"],
        "audio_url": result.get("audio_url"),
    }


@app.post("/voice/handle-transcript")
async def handle_transcript(data: dict) -> dict:
    transcript = (data.get("transcript") or "").strip()
    if not transcript:
        raise HTTPException(status_code=400, detail="transcript is required")
    student_name = data.get("student_name") or _recognized_student_name()
    result = await brain.handle_transcript(transcript, student_name)
    if result.get("ignored"):
        return result
    conversation_store.add(transcript, result["answer"], student_name)
    save_transcript_session(
        messages=[
            {"role": "user", "content": transcript},
            {"role": "assistant", "content": result["answer"]},
        ],
        topics=[student_name or "Voice"],
        student_name=student_name or "Unknown student",
    )
    await _speak_live_sentence(result["answer"])
    return {key: value for key, value in result.items() if key != "audio_bytes"}


@app.websocket("/ws/pi/video")
async def pi_video_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    perception.state.video_connected = True
    last_processed = 0.0
    processing_task: asyncio.Task | None = None
    try:
        while True:
            message = await websocket.receive()
            if "bytes" in message and message["bytes"]:
                perception.accept_frame(message["bytes"])
                now = time.monotonic()
                if (
                    now - last_processed >= 1.0 / max(settings.perception_max_fps, 0.1)
                    and (processing_task is None or processing_task.done())
                ):
                    last_processed = now
                    processing_task = asyncio.create_task(perception.process_jpeg(message["bytes"]))
            elif "text" in message and message["text"] == "snapshot":
                await websocket.send_json(perception.snapshot())
    except (WebSocketDisconnect, RuntimeError):
        perception.state.video_connected = False
        if processing_task is not None and not processing_task.done():
            processing_task.cancel()


async def _ensure_deepgram_connected() -> None:
    """Pre-warm the Deepgram connection in background."""
    try:
        await _connect_deepgram_audio()
    except Exception as exc:
        print(f"[Deepgram] Pre-warm failed: {exc}")


@app.websocket("/ws/pi/audio")
async def pi_audio_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    perception.state.audio_connected = True
    with contextlib.suppress(Exception):
        await websocket.send_json({"ok": True, "status": "ready"})
    if settings.deepgram_api_key:
        await _deepgram_audio_session_resilient(websocket)
        perception.state.audio_connected = False
        return
    perception.state.audio_connected = False

    audio_path = settings.data_dir / "latest_audio.raw"
    try:
        with audio_path.open("ab") as audio_file:
            while True:
                message = await websocket.receive()
                if "bytes" in message and message["bytes"]:
                    audio_file.write(message["bytes"])
                    await websocket.send_json({"ok": True, "bytes": len(message["bytes"])})
                elif "text" in message and message["text"]:
                    result = await brain.handle_transcript(message["text"])
                    if not result.get("ignored"):
                        await _speak_live_sentence(result["answer"])
                    await websocket.send_json(result)
    except WebSocketDisconnect:
        perception.state.audio_connected = False


async def _deepgram_keepalive() -> None:
    global _deepgram_ws
    silent_frame = _pcm_silence_frame()
    while True:
        await asyncio.sleep(8)
        if _deepgram_ws is None:
            return
        try:
            if _deepgram_ws.closed:
                _deepgram_ws = None
                return
            await _deepgram_ws.send(silent_frame)
        except Exception as e:
            print(f"[Deepgram] Keepalive died: {e}")
            _deepgram_ws = None
            return

async def _connect_deepgram_audio():
    global _deepgram_ws
    async with _deepgram_lock:
        if _deepgram_ws is not None:
            try:
                if _deepgram_ws.closed:
                    raise Exception("closed")
                return _deepgram_ws
            except Exception:
                _deepgram_ws = None

        url = _deepgram_listen_url()
        headers = {"Authorization": f"Token {settings.deepgram_api_key}"}
        try:
            _deepgram_ws = await websockets.connect(
                url, additional_headers=headers, ping_interval=None, open_timeout=15
            )
        except TypeError:
            _deepgram_ws = await websockets.connect(
                url, extra_headers=headers, ping_interval=None, open_timeout=15
            )
        print("[Deepgram] Connected")
        asyncio.create_task(_deepgram_keepalive())
        return _deepgram_ws


async def _deepgram_audio_session_resilient(websocket: WebSocket) -> None:
    url = _deepgram_listen_url()
    headers = {"Authorization": f"Token {settings.deepgram_api_key}"}
    audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=40)
    pi_closed = asyncio.Event()
    _voice_diagnostics["sessions"] = int(_voice_diagnostics.get("sessions") or 0) + 1
    _voice_diagnostics["last_error"] = ""
    _voice_diagnostics["current_turn_audio_started_at"] = None
    _voice_diagnostics["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    session_audio_started_at: float | None = None
    session_voice_audio_at: float | None = None
    last_voice_chunk_at: float | None = None

    async def receive_from_pi() -> None:
        nonlocal session_audio_started_at, session_voice_audio_at, last_voice_chunk_at
        try:
            while True:
                message = await websocket.receive()
                if "bytes" in message and message["bytes"]:
                    if not _voice_enabled or _audio_suppressed():
                        continue
                    now = time.monotonic()
                    peak = _pcm16_peak(message["bytes"])
                    if peak >= 500:
                        if last_voice_chunk_at is None or now - last_voice_chunk_at > 1.2:
                            session_audio_started_at = now
                            _voice_diagnostics["current_turn_audio_started_at"] = now
                        session_voice_audio_at = now
                        last_voice_chunk_at = now
                        _voice_diagnostics["last_voice_peak"] = peak
                    _voice_diagnostics["audio_chunks_from_pi"] = int(_voice_diagnostics.get("audio_chunks_from_pi") or 0) + 1
                    _voice_diagnostics["audio_bytes_from_pi"] = int(_voice_diagnostics.get("audio_bytes_from_pi") or 0) + len(message["bytes"])
                    _voice_diagnostics["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                    if audio_queue.full():
                        with contextlib.suppress(asyncio.QueueEmpty):
                            audio_queue.get_nowait()
                    await audio_queue.put(message["bytes"])
                elif "text" in message and message["text"]:
                    if not _voice_enabled:
                        with contextlib.suppress(Exception):
                            await websocket.send_json({"ok": True, "ignored": True, "voice_enabled": False})
                        continue
                    result = await brain.handle_transcript(message["text"])
                    if not result.get("ignored"):
                        await _speak_live_sentence(result["answer"])
                    with contextlib.suppress(Exception):
                        await websocket.send_json(result)
        except (WebSocketDisconnect, RuntimeError):
            pi_closed.set()
        except Exception as exc:
            _voice_diagnostics["last_error"] = f"receive_from_pi: {type(exc).__name__}: {exc}"
            _voice_diagnostics["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            pi_closed.set()

    async def connect_deepgram():
        try:
            return await websockets.connect(url, additional_headers=headers, ping_interval=None, open_timeout=15)
        except TypeError:
            return await websockets.connect(url, extra_headers=headers, ping_interval=None, open_timeout=15)

    async def handle_transcript(full: str, source: str) -> dict[str, Any]:
        nonlocal session_audio_started_at, session_voice_audio_at, last_voice_chunk_at
        global _last_voice_latency, _zoro_is_speaking
        turn_started = time.monotonic()
        first_audio_received_at = session_audio_started_at
        first_tts_ready_ms = None
        first_speaker_done_ms = None
        if not _voice_enabled or _audio_suppressed() or _zoro_is_speaking:
            return {"ignored": True}
        print(f"[STT] Got transcript ({source}): {full}")

        async def tts_callback(sentence: str, is_first: bool) -> None:
            nonlocal first_tts_ready_ms, first_speaker_done_ms
            global _last_voice_latency, _zoro_is_speaking
            _zoro_is_speaking = True
            _suppress_audio_for(min(_estimate_speech_seconds(sentence) + 0.6, 8.0))
            if is_first:
                first_tts_ready_ms = round((time.monotonic() - turn_started) * 1000)
            speaker_result = await _speak_live_sentence(sentence)
            if is_first:
                first_speaker_done_ms = round((time.monotonic() - turn_started) * 1000)
                first_audio_byte_ms = speaker_result.get("cartesia_first_byte_ms")
                speech_start_from_transcript_ms = (
                    first_tts_ready_ms + first_audio_byte_ms
                    if first_tts_ready_ms is not None and first_audio_byte_ms is not None
                    else first_audio_byte_ms
                )
                speech_start_from_audio_ms = None
                if first_audio_received_at and speech_start_from_transcript_ms is not None:
                    speech_start_from_audio_ms = round((turn_started - float(first_audio_received_at)) * 1000 + speech_start_from_transcript_ms)
                _last_voice_latency = {
                    "transcript": full,
                    "transcript_source": source,
                    "stt_final_from_first_audio_ms": round((turn_started - float(first_audio_received_at)) * 1000) if first_audio_received_at else None,
                    "first_tts_ready_ms": first_tts_ready_ms,
                    "first_speaker_done_ms": first_speaker_done_ms,
                    "tts_engine": speaker_result.get("engine"),
                    "starts_speaking_after_transcript_ms": speech_start_from_transcript_ms,
                    "starts_speaking_after_first_audio_ms": speech_start_from_audio_ms,
                    "tts_first_audio_byte_ms": first_audio_byte_ms,
                    "cartesia_first_audio_byte_ms": speaker_result.get("cartesia_first_byte_ms"),
                    "deepgram_first_audio_byte_ms": None,
                    "first_speaker_result": speaker_result,
                    "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                }

        try:
            student_name = _recognized_student_name()
            result = await brain.handle_transcript_streaming(full, student_name, tts_callback)
            if not result.get("ignored"):
                conversation_store.add(full, result["answer"], student_name)
                save_transcript_session(
                    messages=[
                        {"role": "user", "content": full},
                        {"role": "assistant", "content": result["answer"]},
                    ],
                    topics=[student_name or "Voice"],
                    student_name=student_name or "Unknown student",
                )
            total_turn_ms = round((time.monotonic() - turn_started) * 1000)
            _last_voice_latency = {
                **_last_voice_latency,
                "transcript": full,
                "transcript_source": source,
                "total_turn_ms": total_turn_ms,
                "answer_chars": len(result.get("answer", "")),
                "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            with contextlib.suppress(Exception):
                await websocket.send_json(result)
            return result
        finally:
            _voice_diagnostics["current_turn_audio_started_at"] = None
            session_audio_started_at = None
            session_voice_audio_at = None
            last_voice_chunk_at = None
            _zoro_is_speaking = False

    async def run_deepgram_once(dg) -> None:
        responding = False
        pending_partial_task: asyncio.Task | None = None
        latest_partial = ""

        async def keepalive() -> None:
            silent_frame = _pcm_silence_frame()
            while not pi_closed.is_set():
                try:
                    if getattr(dg, "closed", False):
                        return
                    await dg.send(silent_frame)
                except Exception:
                    return
                await asyncio.sleep(1)

        async def send_audio() -> None:
            while not pi_closed.is_set():
                try:
                    chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                await dg.send(chunk)
                _voice_diagnostics["audio_chunks_to_deepgram"] = int(_voice_diagnostics.get("audio_chunks_to_deepgram") or 0) + 1

        async def respond_to_partial_after_delay(transcript: str, delay_ms: int) -> None:
            nonlocal responding, latest_partial, session_voice_audio_at
            await asyncio.sleep(max(0, delay_ms) / 1000)
            while session_voice_audio_at and time.monotonic() - session_voice_audio_at < 0.08:
                await asyncio.sleep(0.08 - (time.monotonic() - session_voice_audio_at))
            if not responding and transcript == latest_partial:
                responding = True
                try:
                    await handle_transcript(transcript, "stable_partial")
                finally:
                    responding = False

        async def receive_results() -> None:
            nonlocal responding, pending_partial_task, latest_partial
            async for raw in dg:
                event = json.loads(raw)
                _voice_diagnostics["deepgram_events"] = int(_voice_diagnostics.get("deepgram_events") or 0) + 1
                _voice_diagnostics["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                if event.get("type") != "Results":
                    continue
                _voice_diagnostics["deepgram_results"] = int(_voice_diagnostics.get("deepgram_results") or 0) + 1
                transcript = (
                    event.get("channel", {})
                    .get("alternatives", [{}])[0]
                    .get("transcript", "")
                ).strip()
                transcript = _normalize_stt_text(transcript)
                if not transcript:
                    continue
                partial_changed = False
                if event.get("is_final"):
                    _voice_diagnostics["last_final"] = transcript
                else:
                    partial_changed = transcript != latest_partial
                    latest_partial = transcript
                    _voice_diagnostics["last_partial"] = transcript
                with contextlib.suppress(Exception):
                    await websocket.send_json({
                        "type": "transcript_final" if event.get("is_final") else "transcript_partial",
                        "text": transcript,
                    })
                if event.get("is_final") and not responding:
                    if pending_partial_task is not None and not pending_partial_task.done():
                        pending_partial_task.cancel()
                    responding = True
                    try:
                        await handle_transcript(transcript, "final")
                    finally:
                        responding = False
                elif not event.get("is_final") and not responding:
                    if not partial_changed and pending_partial_task is not None and not pending_partial_task.done():
                        continue
                    if pending_partial_task is not None and not pending_partial_task.done():
                        pending_partial_task.cancel()
                    delay_ms = _partial_response_delay_ms(transcript)
                    if delay_ms is not None:
                        pending_partial_task = asyncio.create_task(respond_to_partial_after_delay(transcript, delay_ms))

        tasks = [
            asyncio.create_task(keepalive()),
            asyncio.create_task(send_audio()),
            asyncio.create_task(receive_results()),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if pending_partial_task is not None and not pending_partial_task.done():
            pending_partial_task.cancel()
        for task in pending:
            task.cancel()
        for task in done:
            with contextlib.suppress(BaseException):
                task.result()

    pi_task = asyncio.create_task(receive_from_pi())
    try:
        while not pi_closed.is_set():
            dg = None
            try:
                dg = await connect_deepgram()
                print("[Deepgram] Session connected")
                await run_deepgram_once(dg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _voice_diagnostics["last_error"] = f"deepgram_session: {type(exc).__name__}: {exc!r}"
                _voice_diagnostics["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                print(f"[Deepgram] Reconnecting after error: {type(exc).__name__}: {exc!r}")
                await asyncio.sleep(0.35)
            finally:
                if dg is not None:
                    with contextlib.suppress(Exception):
                        await dg.close()
    finally:
        pi_closed.set()
        pi_task.cancel()
        with contextlib.suppress(BaseException):
            await pi_task
        perception.state.audio_connected = False


async def _deepgram_audio_session(websocket: WebSocket) -> None:
    url = _deepgram_listen_url()
    headers = {"Authorization": f"Token {settings.deepgram_api_key}"}
    _voice_diagnostics["sessions"] = int(_voice_diagnostics.get("sessions") or 0) + 1
    _voice_diagnostics["last_error"] = ""
    _voice_diagnostics["current_turn_audio_started_at"] = None
    session_audio_started_at: float | None = None
    session_voice_audio_at: float | None = None
    _voice_diagnostics["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")

    try:
        try:
            dg = await websockets.connect(
                url, additional_headers=headers, ping_interval=None, open_timeout=15
            )
        except TypeError:
            dg = await websockets.connect(
                url, extra_headers=headers, ping_interval=None, open_timeout=15
            )
        print("[Deepgram] Session connected")
    except Exception as exc:
        print(f"[Audio] Deepgram connect failed: {exc}")
        return

    async def keepalive() -> None:
        silent_frame = _pcm_silence_frame()
        while True:
            try:
                if getattr(dg, "closed", False):
                    return
                await dg.send(silent_frame)
            except Exception:
                return
            await asyncio.sleep(1)

    asyncio.create_task(keepalive())

    # Queue for passing audio bytes from Pi to Deepgram
    audio_queue: asyncio.Queue = asyncio.Queue()

    async def receive_from_pi() -> None:
        """Read from Pi WebSocket, put audio on queue."""
        nonlocal session_audio_started_at, session_voice_audio_at
        try:
            while True:
                message = await websocket.receive()
                if "bytes" in message and message["bytes"]:
                    if _voice_enabled and not _audio_suppressed():
                        now = time.monotonic()
                        if session_audio_started_at is None or now - session_audio_started_at > 10:
                            session_audio_started_at = now
                            _voice_diagnostics["current_turn_audio_started_at"] = now
                        peak = _pcm16_peak(message["bytes"])
                        if peak >= 500:
                            session_voice_audio_at = now
                            _voice_diagnostics["last_voice_peak"] = peak
                        _voice_diagnostics["audio_chunks_from_pi"] = int(_voice_diagnostics.get("audio_chunks_from_pi") or 0) + 1
                        _voice_diagnostics["audio_bytes_from_pi"] = int(_voice_diagnostics.get("audio_bytes_from_pi") or 0) + len(message["bytes"])
                        _voice_diagnostics["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                        await audio_queue.put(message["bytes"])
                elif "text" in message and message["text"]:
                    if not _voice_enabled:
                        with contextlib.suppress(Exception):
                            await websocket.send_json({"ok": True, "ignored": True, "voice_enabled": False})
                        continue
                    result = await brain.handle_transcript(message["text"])
                    await _speak_live_sentence(result["answer"])
                    with contextlib.suppress(Exception):
                        await websocket.send_json(result)
        except (WebSocketDisconnect, RuntimeError):
            await audio_queue.put(None)  # sentinel to unblock sender
        except Exception as exc:
            _voice_diagnostics["last_error"] = f"receive_from_pi: {exc}"
            _voice_diagnostics["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            await audio_queue.put(None)

    async def send_to_deepgram() -> None:
        """Take audio from queue, forward to Deepgram."""
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                return  # Pi disconnected
            try:
                await dg.send(chunk)
                _voice_diagnostics["audio_chunks_to_deepgram"] = int(_voice_diagnostics.get("audio_chunks_to_deepgram") or 0) + 1
            except Exception as exc:
                _voice_diagnostics["last_error"] = f"send_to_deepgram: {exc}"
                _voice_diagnostics["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                print(f"[Deepgram] Audio send failed: {exc}")
                return

    async def receive_from_deepgram() -> None:
        """Read transcripts from Deepgram, trigger brain."""
        responding = False
        pending_partial_task: asyncio.Task | None = None
        latest_partial = ""

        async def respond_to_transcript(full: str, source: str = "final") -> None:
            nonlocal responding, session_audio_started_at, session_voice_audio_at
            global _last_voice_latency, _zoro_is_speaking
            if responding:
                return
            turn_started = time.monotonic()
            first_audio_received_at = session_audio_started_at
            first_tts_ready_ms = None
            first_speaker_done_ms = None
            if not _voice_enabled:
                print(f"[STT] Ignoring (voice disabled): {full}")
                return
            if _audio_suppressed():
                print(f"[STT] Ignoring (speaker cooldown): {full}")
                return
            if _zoro_is_speaking:
                print(f"[STT] Ignoring (Zoro speaking): {full}")
                return
            responding = True
            print(f"[STT] Got transcript ({source}): {full}")
            classification = brain.intent.classify(full)

            async def tts_callback(sentence: str, is_first: bool) -> None:
                nonlocal first_tts_ready_ms, first_speaker_done_ms
                global _last_voice_latency, _zoro_is_speaking
                _zoro_is_speaking = True
                _suppress_audio_for(min(_estimate_speech_seconds(sentence) + 0.6, 8.0))
                label = "first" if is_first else "next"
                print(f"[Streaming TTS] {label} sentence -> {len(sentence)} chars")
                if is_first:
                    first_tts_ready_ms = round((time.monotonic() - turn_started) * 1000)
                speaker_result = await _speak_live_sentence(sentence)
                if is_first:
                    first_speaker_done_ms = round((time.monotonic() - turn_started) * 1000)
                    first_audio_byte_ms = speaker_result.get("cartesia_first_byte_ms")
                    speech_start_from_transcript_ms = (
                        first_tts_ready_ms + first_audio_byte_ms
                        if first_tts_ready_ms is not None and first_audio_byte_ms is not None
                        else first_audio_byte_ms
                    )
                    speech_start_from_audio_ms = None
                    if first_audio_received_at and speech_start_from_transcript_ms is not None:
                        speech_start_from_audio_ms = round((turn_started - float(first_audio_received_at)) * 1000 + speech_start_from_transcript_ms)
                    _last_voice_latency = {
                        "transcript": full,
                        "transcript_source": source,
                        "stt_final_from_first_audio_ms": round((turn_started - float(first_audio_received_at)) * 1000) if first_audio_received_at else None,
                        "first_tts_ready_ms": first_tts_ready_ms,
                        "first_speaker_done_ms": first_speaker_done_ms,
                        "tts_engine": speaker_result.get("engine"),
                        "starts_speaking_after_transcript_ms": speech_start_from_transcript_ms,
                        "starts_speaking_after_first_audio_ms": speech_start_from_audio_ms,
                        "tts_first_audio_byte_ms": first_audio_byte_ms,
                        "cartesia_first_audio_byte_ms": speaker_result.get("cartesia_first_byte_ms"),
                        "deepgram_first_audio_byte_ms": None,
                        "first_speaker_result": speaker_result,
                        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    }

            student_name = _recognized_student_name()
            result = await brain.handle_transcript_streaming(full, student_name, tts_callback)
            _voice_diagnostics["current_turn_audio_started_at"] = None
            session_audio_started_at = None
            session_voice_audio_at = None
            total_turn_ms = round((time.monotonic() - turn_started) * 1000)
            _last_voice_latency = {
                **_last_voice_latency,
                "transcript": full,
                "transcript_source": source,
                "total_turn_ms": total_turn_ms,
                "answer_chars": len(result.get("answer", "")),
                "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            _zoro_is_speaking = False
            if not result.get("ignored"):
                conversation_store.add(full, result["answer"], student_name)
                save_transcript_session(
                    messages=[
                        {"role": "user", "content": full},
                        {"role": "assistant", "content": result["answer"]},
                    ],
                    topics=[student_name or "Voice"],
                    student_name=student_name or "Unknown student",
                )
            with contextlib.suppress(Exception):
                await websocket.send_json(result)
            responding = False

        async def respond_to_partial_after_delay(transcript: str, delay_ms: int) -> None:
            nonlocal latest_partial, responding, session_voice_audio_at
            await asyncio.sleep(max(0, delay_ms) / 1000)
            while session_voice_audio_at and time.monotonic() - session_voice_audio_at < 0.08:
                await asyncio.sleep(0.08 - (time.monotonic() - session_voice_audio_at))
            if not responding and transcript == latest_partial:
                await respond_to_transcript(transcript, "stable_partial")

        try:
            async for raw in dg:
                event = json.loads(raw)
                _voice_diagnostics["deepgram_events"] = int(_voice_diagnostics.get("deepgram_events") or 0) + 1
                _voice_diagnostics["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                if event.get("type") != "Results":
                    continue
                _voice_diagnostics["deepgram_results"] = int(_voice_diagnostics.get("deepgram_results") or 0) + 1
                transcript = (
                    event.get("channel", {})
                    .get("alternatives", [{}])[0]
                    .get("transcript", "")
                ).strip()
                transcript = _normalize_stt_text(transcript)
                partial_changed = False
                if transcript:
                    if event.get("is_final"):
                        _voice_diagnostics["last_final"] = transcript
                    else:
                        partial_changed = transcript != latest_partial
                        latest_partial = transcript
                        _voice_diagnostics["last_partial"] = transcript
                    with contextlib.suppress(Exception):
                        await websocket.send_json({
                            "type": "transcript_final" if event.get("is_final") else "transcript_partial",
                            "text": transcript,
                        })
                if transcript and event.get("is_final") and not responding:
                    if pending_partial_task is not None and not pending_partial_task.done():
                        pending_partial_task.cancel()
                    await respond_to_transcript(transcript, "final")
                elif transcript and not event.get("is_final") and not responding:
                    if not partial_changed and pending_partial_task is not None and not pending_partial_task.done():
                        continue
                    if pending_partial_task is not None and not pending_partial_task.done():
                        pending_partial_task.cancel()
                    delay_ms = _partial_response_delay_ms(transcript)
                    if delay_ms is not None:
                        pending_partial_task = asyncio.create_task(respond_to_partial_after_delay(transcript, delay_ms))
        except BaseException as exc:
            _voice_diagnostics["last_error"] = f"receive_from_deepgram: {type(exc).__name__}: {exc!r}"
            _voice_diagnostics["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            print(f"[Deepgram] Receive stopped: {type(exc).__name__}: {exc!r}")
            return

    pi_task = asyncio.create_task(receive_from_pi())
    dg_send_task = asyncio.create_task(send_to_deepgram())
    dg_recv_task = asyncio.create_task(receive_from_deepgram())

    done, pending = await asyncio.wait(
        {pi_task, dg_send_task, dg_recv_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if pi_task not in done:
        for task in done:
            with contextlib.suppress(BaseException):
                task.result()
        print("[Audio] Deepgram side ended; closing Pi audio socket for clean reconnect.")
        for task in pending:
            task.cancel()
        with contextlib.suppress(BaseException):
            await pi_task
        with contextlib.suppress(BaseException):
            await dg_send_task
        with contextlib.suppress(BaseException):
            await dg_recv_task
        with contextlib.suppress(Exception):
            await dg.close()
        with contextlib.suppress(Exception):
            await websocket.close()
        return
    for task in done:
        with contextlib.suppress(BaseException):
            task.result()

    # Clean up
    for task in pending:
        task.cancel()
    with contextlib.suppress(Exception):
        await websocket.close()
    with contextlib.suppress(BaseException):
        await pi_task
    with contextlib.suppress(BaseException):
        await dg_send_task
    with contextlib.suppress(BaseException):
        await dg_recv_task
    with contextlib.suppress(Exception):
        await dg.close()

# ═══════════════════════════════════════════════════════════════════════════════
# BRAIN / PERCEPTION
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/brain/state")
async def brain_state() -> dict:
    return {
        "self_model": self_model.state,
        "perception": perception.snapshot(),
        "latest_memories": memory.latest(10),
    }


@app.get("/brain/observe")
async def brain_observe() -> dict:
    return {"description": perception.describe_room(), "perception": perception.snapshot()}


@app.get("/environment/map")
async def environment_map() -> dict:
    return perception.environment_map.snapshot()


@app.get("/navigation/risk")
async def navigation_risk() -> dict:
    snapshot = perception.snapshot()
    return {
        "risk": snapshot.get("navigation_risk"),
        "tracks": snapshot.get("tracks", []),
        "environment_map": snapshot.get("environment_map"),
    }


@app.post("/environment/map/reset")
async def environment_map_reset() -> dict:
    perception.environment_map = perception.environment_map.__class__()
    return {"ok": True, "map": perception.environment_map.snapshot()}


@app.get("/voice/latency")
async def voice_latency() -> dict:
    return {"latest_live_turn": _last_voice_latency}


@app.get("/voice/diagnostics")
async def voice_diagnostics() -> dict:
    return {
        **_voice_diagnostics,
        "voice_enabled": _voice_enabled,
        "zoro_is_speaking": _zoro_is_speaking,
        "speaker_cooldown_seconds": max(0.0, round(_ignore_audio_until - time.monotonic(), 1)),
        "audio_connected": perception.state.audio_connected,
    }


@app.get("/voice/config")
async def voice_config() -> dict:
    return {
        "current_model": settings.deepgram_tts_model,
        "speed": settings.deepgram_tts_speed,
        "live_tts_mode": settings.live_tts_mode,
        "active_tts": {
            "engine": "cartesia_stream" if settings.live_tts_mode.lower() == "cartesia_stream" else "deepgram_stream",
            "model": settings.cartesia_tts_model if settings.live_tts_mode.lower() == "cartesia_stream" else settings.deepgram_tts_model,
            "voice_id": settings.cartesia_voice_id if settings.live_tts_mode.lower() == "cartesia_stream" else settings.deepgram_tts_model,
        },
        "cartesia": {
            "model": settings.cartesia_tts_model,
            "voice_id": settings.cartesia_voice_id,
            "sample_rate": settings.cartesia_sample_rate,
            "configured": bool(settings.cartesia_api_key or settings.cartesia_api_pool),
            "active_keys": [
                {
                    "label": item["label"],
                    "remaining": item.get("remaining"),
                    "exhausted": item.get("exhausted"),
                    "masked_key": item.get("masked_key"),
                }
                for item in _cartesia_pool()
            ],
        },
        "voices": VOICE_OPTIONS,
    }


@app.post("/voice/cartesia/warm")
async def voice_cartesia_warm() -> dict:
    if not (settings.cartesia_api_key or settings.cartesia_api_pool):
        raise HTTPException(status_code=400, detail="Cartesia API key is not configured")
    started = time.monotonic()
    try:
        credential, _ = _select_cartesia_key(1)
        if credential is None:
            raise RuntimeError("All Cartesia API keys are exhausted.")
        await _connect_cartesia_tts(credential)
        return {"ok": True, "connect_ms": round((time.monotonic() - started) * 1000)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.put("/voice/config")
async def voice_config_update(data: VoiceConfigUpdate) -> dict:
    allowed = {voice["model"] for voice in VOICE_OPTIONS}
    if data.model not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported voice model")
    settings.deepgram_tts_model = data.model
    if data.speed is not None:
        settings.deepgram_tts_speed = max(0.7, min(1.2, data.speed))
    return await voice_config()


@app.post("/voice/latency/benchmark")
async def voice_latency_benchmark(data: VoiceBenchmarkRequest | None = None) -> dict:
    text = (data.text if data else "Okay.").strip() or "Okay."
    started = time.monotonic()
    live_tts_started = time.monotonic()
    speaker = await _speak_live_sentence(text)
    live_tts_ms = round((time.monotonic() - live_tts_started) * 1000)
    total_ms = round((time.monotonic() - started) * 1000)
    first_audio_byte_ms = speaker.get("cartesia_first_byte_ms")
    return {
        "ok": True,
        "text": text,
        "live_tts_request_ms": live_tts_ms,
        "live_tts_first_audio_byte_ms": first_audio_byte_ms,
        "streaming_tts_to_speaker": speaker,
        "total_benchmark_ms": total_ms,
        "under_1s_to_first_audio": bool(first_audio_byte_ms is not None and first_audio_byte_ms < 1000),
        "models": {
            "stt": settings.deepgram_stt_model,
            "live_tts_mode": "cartesia_stream",
            "tts": settings.cartesia_tts_model,
            "sample_rate": settings.cartesia_sample_rate,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# NEW ENDPOINTS — for new frontend (zoro_dashboard_integrated)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/notifications")
async def notification_list(limit: int = 50) -> dict:
    return {"items": notifications.latest(max(1, min(limit, 200)))}


@app.get("/classroom/state")
async def classroom_state() -> dict:
    return {
        "mode": "teaching" if policy.teaching_active else "general",
        "teaching_active": policy.teaching_active,
        "strict_mode": policy.strict_mode,
        "permitted_exits": policy.permitted_exits[-20:],
        "rules": policy.classroom_rules_context().splitlines(),
        "perception": perception.snapshot(),
        "notifications": notifications.latest(20),
        "behavior": behavior.summary(),
        "people": people_memory.profiles(),
        "rag": rag.status(),
        "voice": {
            "last_partial": _voice_diagnostics.get("last_partial") or "",
            "last_final": _voice_diagnostics.get("last_final") or "",
            "transcript_source": _last_voice_latency.get("transcript_source") if isinstance(_last_voice_latency, dict) else "",
            "starts_speaking_after_transcript_ms": _last_voice_latency.get("starts_speaking_after_transcript_ms") if isinstance(_last_voice_latency, dict) else None,
            "starts_speaking_after_first_audio_ms": _last_voice_latency.get("starts_speaking_after_first_audio_ms") if isinstance(_last_voice_latency, dict) else None,
        },
        "assessments": assessments.list()[:5],
        "world_memory": world_memory.summary(),
    }


class ClassroomModeRequest(BaseModel):
    teaching: bool


@app.get("/classroom/mode")
async def classroom_mode() -> dict:
    return {
        "mode": "teaching" if policy.teaching_active else "general",
        "teaching_active": policy.teaching_active,
        "rag_enabled": policy.teaching_active,
        "message": "Teaching mode uses uploaded syllabus/RAG." if policy.teaching_active else "General mode answers broadly without syllabus grounding.",
    }


@app.put("/classroom/mode")
async def classroom_mode_update(data: ClassroomModeRequest) -> dict:
    policy.teaching_active = bool(data.teaching)
    mode = "teaching" if policy.teaching_active else "general"
    notifications.add(
        "classroom_mode",
        f"{mode.title()} mode enabled.",
        "info",
        {"mode": mode, "rag_enabled": policy.teaching_active},
    )
    return await classroom_mode()


@app.get("/behavior/summary")
async def behavior_summary() -> dict:
    return behavior.summary()


@app.get("/behavior/events")
async def behavior_events(limit: int = 200, student_name: str = "") -> dict:
    return {"items": behavior.events(limit=limit, student_name=student_name or None)}


@app.get("/behavior/report.csv")
async def behavior_report() -> FileResponse:
    path = behavior.report_path()
    return FileResponse(path, filename=path.name, media_type="text/csv")


def _behavior_model_status() -> dict:
    model_path = settings.classroom_behavior_model
    return {
        "model_path": str(model_path),
        "model_exists": model_path.exists(),
        "model_size_mb": round(model_path.stat().st_size / (1024 * 1024), 2) if model_path.exists() else 0,
        "loaded": bool(getattr(perception, "_behavior_yolo", None)),
        "dataset_slug": settings.classroom_behavior_dataset_slug,
        "models_slug": settings.classroom_behavior_models_slug,
        "kaggle_configured": bool(os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"))
            or (Path.home() / ".kaggle" / "kaggle.json").exists(),
        "kaggle_cli": bool(shutil.which("kaggle")),
    }


@app.get("/behavior/model/status")
async def behavior_model_status() -> dict:
    return _behavior_model_status()


@app.post("/behavior/model/reload")
async def behavior_model_reload() -> dict:
    return {**_behavior_model_status(), "reload": perception.load_behavior_model()}


@app.post("/behavior/model/upload")
async def behavior_model_upload(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix != ".pt":
        raise HTTPException(status_code=400, detail="Upload a YOLO .pt model file.")
    settings.classroom_behavior_model.parent.mkdir(parents=True, exist_ok=True)
    settings.classroom_behavior_model.write_bytes(await file.read())
    reload_result = perception.load_behavior_model()
    return {**_behavior_model_status(), "reload": reload_result}


def _install_kaggle_cli_if_needed() -> None:
    if shutil.which("kaggle"):
        return
    subprocess.run([sys.executable, "-m", "pip", "install", "kaggle"], check=True, timeout=180)


def _download_behavior_model_from_kaggle() -> dict:
    _install_kaggle_cli_if_needed()
    if not shutil.which("kaggle"):
        return {"ok": False, "error": "Kaggle CLI is not available after installation."}
    if not _behavior_model_status()["kaggle_configured"]:
        return {
            "ok": False,
            "error": "Kaggle credentials are not configured. Add ~/.kaggle/kaggle.json or KAGGLE_USERNAME/KAGGLE_KEY.",
        }
    download_dir = settings.data_dir / "models" / "kaggle_behavior_models"
    download_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "kaggle", "datasets", "download",
        "-d", settings.classroom_behavior_models_slug,
        "-p", str(download_dir),
        "--unzip",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or result.stdout.strip() or "Kaggle download failed."}
    candidates = sorted(download_dir.rglob("best.pt"), key=lambda path: path.stat().st_size, reverse=True)
    if not candidates:
        candidates = sorted(download_dir.rglob("*.pt"), key=lambda path: path.stat().st_size, reverse=True)
    if not candidates:
        return {"ok": False, "error": "Downloaded files did not contain a .pt model."}
    settings.classroom_behavior_model.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidates[0], settings.classroom_behavior_model)
    return {"ok": True, "source_model": str(candidates[0]), "installed_model": str(settings.classroom_behavior_model)}


@app.post("/behavior/model/download-kaggle")
async def behavior_model_download_kaggle() -> dict:
    result = await asyncio.to_thread(_download_behavior_model_from_kaggle)
    reload_result = perception.load_behavior_model() if result.get("ok") else {"loaded": False}
    return {**_behavior_model_status(), "download": result, "reload": reload_result}


@app.get("/people/profiles")
async def people_profiles() -> dict:
    return {"items": people_memory.profiles()}


@app.post("/people/introduce")
async def people_introduce(data: dict) -> dict:
    transcript = (data.get("transcript") or "").strip()
    people = data.get("people")
    if not people:
        people = people_memory.parse_introductions(transcript)
    if not people:
        raise HTTPException(status_code=400, detail="No people found. Say: this is Kowsalya ma'am, our HOD.")
    result = people_memory.enroll_from_jpeg(perception.latest_jpeg, people[:5])
    attendance.reload_faces()
    return result


class LessonRequest(BaseModel):
    subject: str = ""
    duration_minutes: int = 30
    break_count: int = 2


def _lesson_item_speech(item: dict[str, Any]) -> str:
    if item.get("type") == "break":
        return str(item.get("spoken_script") or item.get("title") or "We will take a short break now.")
    points = [str(point) for point in item.get("teaching_points") or [] if str(point).strip()]
    questions = [str(question) for question in item.get("check_questions") or [] if str(question).strip()]
    parts = [
        str(item.get("spoken_script") or item.get("title") or "Let us continue the lesson."),
        "Key points: " + " ".join(points[:3]) if points else "",
        "Quick check: " + questions[0] if questions else "",
    ]
    return " ".join(part for part in parts if part).strip()


async def _run_lesson_plan(plan: dict[str, Any]) -> None:
    subject = str(plan.get("subject") or "General")
    try:
        greeting = str(plan.get("greeting") or f"Hello students. Today's class is about {subject}.")
        await _speak_live_sentence(greeting)
        progress = plan.get("progress") or {}
        current_index = int(progress.get("current_index") or 0)
        schedule = list(plan.get("schedule") or [])
        for index, item in enumerate(schedule[current_index:], start=current_index):
            if not policy.teaching_active:
                return
            await _speak_live_sentence(_lesson_item_speech(item))
            minutes = max(1, int(item.get("minutes") or 1))
            await asyncio.sleep(minutes * 60)
            lessons.advance(subject, minutes)
            notifications.add(
                "lesson_progress",
                f"Completed lesson part {index + 1}: {item.get('title') or item.get('type')}.",
                "info",
                {"subject": subject, "index": index, "minutes": minutes},
            )
        if policy.teaching_active:
            await _speak_live_sentence(str(plan.get("closing") or f"That completes today's {subject} lesson."))
            policy.teaching_active = False
    except asyncio.CancelledError:
        return
    except Exception as exc:
        notifications.add("lesson_error", f"Lesson runner stopped: {type(exc).__name__}: {exc}", "warning")


def _start_lesson_runner(plan: dict[str, Any]) -> None:
    global _lesson_runner_task
    if _lesson_runner_task and not _lesson_runner_task.done():
        _lesson_runner_task.cancel()
    _lesson_runner_task = asyncio.create_task(_run_lesson_plan(plan))


@app.get("/lesson/subjects")
async def lesson_subjects() -> dict:
    return {"items": lessons.available_subjects()}


@app.post("/lesson/plan")
async def lesson_plan(data: LessonRequest) -> dict:
    return lessons.build_plan(data.subject, data.duration_minutes, data.break_count)


@app.post("/lesson/start")
async def lesson_start(data: LessonRequest) -> dict:
    plan = lessons.start_or_resume(data.subject, data.duration_minutes, data.break_count)
    policy.teaching_active = True
    _start_lesson_runner(plan)
    notifications.add(
        "lesson_started",
        f"Lesson started: {plan['subject']} for {plan['duration_minutes']} minutes.",
        "info",
        {"subject": plan["subject"], "duration_minutes": plan["duration_minutes"]},
    )
    return plan


@app.post("/lesson/stop")
async def lesson_stop() -> dict:
    global _lesson_runner_task
    policy.teaching_active = False
    if _lesson_runner_task and not _lesson_runner_task.done():
        _lesson_runner_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await _lesson_runner_task
    _lesson_runner_task = None
    lessons.stop()
    notifications.add("lesson_stopped", "Lesson mode stopped.", "info")
    return {"ok": True, "teaching_active": False}


@app.get("/lesson/progress")
async def lesson_progress(subject: str = "") -> dict:
    return lessons.progress(subject)


@app.post("/lesson/advance")
async def lesson_advance(data: dict) -> dict:
    subject = data.get("subject") or "General"
    result = lessons.advance(subject, data.get("minutes"))
    if result.get("ok"):
        notifications.add("lesson_progress", f"Lesson progress advanced for {subject}.", "info", result.get("progress"))
    return result


@app.get("/rag/status")
async def rag_status() -> dict:
    return rag.status()


@app.post("/rag/reindex")
async def rag_reindex() -> dict:
    return await asyncio.to_thread(rag.rebuild)


@app.get("/rag/search")
async def rag_search(q: str, subject: str = "", limit: int = 5) -> dict:
    return {"items": rag.search(q, subject, limit)}


@app.get("/rag/answer")
async def rag_answer(q: str, subject: str = "") -> dict:
    return rag.quick_answer(q, subject)


@app.get("/assessments")
async def assessment_list() -> dict:
    return {"items": assessments.list()}


@app.post("/assessments")
async def assessment_create(data: dict) -> dict:
    item = assessments.create(
        data.get("title") or "Class assessment",
        data.get("subject") or "General",
        data.get("instructions") or "",
        data.get("due_at") or "",
    )
    notifications.add("assessment_created", f"Assessment created: {item['title']}", "info", item)
    return item


@app.post("/assessments/{assessment_id}/submit")
async def assessment_submit(assessment_id: str, data: dict) -> dict:
    student_name = data.get("student_name") or _recognized_student_name() or "Unknown student"
    item = assessments.submit(assessment_id, student_name, data.get("note") or "")
    if not item:
        raise HTTPException(status_code=404, detail="Assessment not found")
    behavior.add_event(student_name, "attentive", f"Completed assessment: {item['title']}", "info", {"assessment_id": assessment_id}, 2)
    return item


@app.post("/assessments/{assessment_id}/close")
async def assessment_close(assessment_id: str, data: dict) -> dict:
    expected = data.get("expected_students") or attendance.known_names
    item = assessments.close(assessment_id, expected)
    if not item:
        raise HTTPException(status_code=404, detail="Assessment not found")
    for name in item.get("missing_students", []):
        behavior.add_event(
            name,
            "missed_assessment",
            f"Did not complete assessment: {item['title']}",
            "warning",
            {"assessment_id": assessment_id},
        )
    notifications.add("assessment_closed", f"Assessment closed: {item['title']}", "info", item)
    return item


@app.get("/world/memory")
async def world_memory_list(q: str = "") -> dict:
    return {"items": world_memory.search(q)}


@app.get("/world/summary")
async def world_memory_summary() -> dict:
    return world_memory.summary()


@app.post("/world/teach")
async def world_memory_teach(data: dict) -> dict:
    try:
        item = world_memory.teach(data.get("name") or "", data.get("facts") or "", "dashboard")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return item


@app.get("/status")
async def get_status() -> dict:
    try:
        pi = await robot.health()
        pi_hardware = pi.get("hardware") or {}
        voice_has_audio = int(_voice_diagnostics.get("audio_chunks_from_pi") or 0) > 0
        return {
            "online": True,
            "voice_active": _voice_enabled
            and bool(pi_hardware.get("mic_present", True))
            and perception.state.audio_connected
            and voice_has_audio,
            "voice_enabled": _voice_enabled,
            "camera_active": bool(pi_hardware.get("camera_present", perception.state.video_connected))
            and perception.state.video_connected
            and _camera_frame_is_fresh(),
            "hardware": pi_hardware,
            "mode": "teaching" if policy.teaching_active else "general",
            "teaching_active": policy.teaching_active,
            "ip": settings.pi_base_url,
            "students_present": len(attendance.known_names),
            "attendance_auto_scan": dict(_attendance_auto_scan),
        }
    except Exception:
        return {
            "online": False,
            "voice_active": False,
            "camera_active": False,
            "mode": "teaching" if policy.teaching_active else "general",
            "teaching_active": policy.teaching_active,
            "ip": "-",
            "attendance_auto_scan": dict(_attendance_auto_scan),
        }


# ── Attendance DB ─────────────────────────────────────────────────────────────

@app.get("/attendance/logs")
async def attendance_logs() -> list:
    return get_attendance_sessions()


@app.get("/attendance/logs/{session_id}/records")
async def attendance_session_records(session_id: str) -> list:
    records = get_attendance_records(session_id)
    if records is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return records


# ── Syllabus DB ───────────────────────────────────────────────────────────────

@app.get("/syllabus/list")
async def syllabus_list() -> list:
    return get_syllabus_list()


@app.delete("/syllabus/{file_id}")
async def syllabus_delete(file_id: str) -> dict:
    delete_syllabus(file_id)
    return {"deleted": file_id}


# ── Speeches ──────────────────────────────────────────────────────────────────

class SpeechCreate(BaseModel):
    name: str
    trigger_phrase: str = ""
    content: str
    voice: str = "aura-2-thalia-en"


@app.get("/speech/list")
async def speech_list() -> list:
    return get_speeches()


@app.post("/speech/create")
async def speech_create(data: SpeechCreate) -> dict:
    speech = save_speech(data.name, data.content, data.trigger_phrase, data.voice)
    if settings.live_tts_mode.lower() != "cartesia_stream":
        asyncio.create_task(_ensure_cached_tts(f"speech-{speech['id']}", data.content))
    return speech


@app.put("/speech/{speech_id}")
async def speech_update(speech_id: str, data: SpeechCreate) -> dict:
    speech = update_speech(speech_id, data.name, data.content, data.trigger_phrase, data.voice)
    if not speech:
        raise HTTPException(status_code=404, detail="Speech not found")
    if settings.live_tts_mode.lower() != "cartesia_stream":
        asyncio.create_task(_ensure_cached_tts(f"speech-{speech_id}", data.content))
    return speech


@app.post("/speech/trigger/{speech_id}")
async def speech_trigger(speech_id: str) -> dict:
    speech = get_speech_by_id(speech_id)
    if not speech:
        raise HTTPException(status_code=404, detail="Speech not found")
    update_speech_triggered(speech_id)
    try:
        _suppress_audio_for(min(_estimate_speech_seconds(speech["content"]) + 6.0, 45.0))
        if settings.live_tts_mode.lower() == "cartesia_stream":
            generated = True
            playback = await _speak_live_sentence(speech["content"])
        else:
            path, generated = await _ensure_cached_tts(f"speech-{speech_id}", speech["content"])
            playback = await _send_cached_audio_to_pi_async(path)
        if not playback.get("ok"):
            raise RuntimeError(playback.get("error") or "Pi speaker did not accept playback.")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Speech playback failed: {e}")
    return {"triggered": speech_id, "name": speech["name"], "cached": not generated, "playback": playback}


@app.post("/speech/prewarm")
async def speech_prewarm() -> dict:
    if settings.live_tts_mode.lower() == "cartesia_stream":
        return {"ok": True, "engine": "cartesia_stream", "speeches": []}
    warmed = []
    for speech in get_speeches():
        try:
            path, generated = await _ensure_cached_tts(f"speech-{speech['id']}", speech["content"])
            warmed.append({
                "id": speech["id"],
                "name": speech["name"],
                "generated": generated,
                "bytes": path.stat().st_size,
            })
        except Exception as exc:
            warmed.append({"id": speech.get("id"), "name": speech.get("name"), "error": str(exc)})
    return {"ok": True, "speeches": warmed}


@app.delete("/speech/{speech_id}")
async def speech_delete(speech_id: str) -> dict:
    delete_speech(speech_id)
    return {"deleted": speech_id}


# ── Transcripts DB ────────────────────────────────────────────────────────────

@app.get("/transcripts/list")
async def transcript_list() -> list:
    return get_transcript_sessions()


@app.get("/transcripts/session/{session_id}")
async def transcript_session(session_id: str) -> list:
    messages = get_transcript_messages(session_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return messages


@app.post("/transcripts/save")
async def save_transcript(data: dict) -> dict:
    session_id = save_transcript_session(
        messages=data.get("messages", []),
        topics=data.get("topics", []),
        student_name=data.get("student_name", ""),
    )
    return {"session_id": session_id}


# ── Voice control ─────────────────────────────────────────────────────────────

@app.post("/voice/start")
async def voice_start() -> dict:
    global _voice_enabled
    _voice_enabled = True
    return {
        "ok": True,
        "voice_enabled": True,
        "message": "Laptop brain is ready. Start pi_agent.voice_agent/body_node on the Pi to stream mic and camera.",
        "audio_ws": "/ws/pi/audio",
        "video_ws": "/ws/pi/video",
    }


@app.post("/voice/stop")
async def voice_stop() -> dict:
    global _voice_enabled
    _voice_enabled = False
    return {"ok": True, "voice_enabled": False, "message": "Voice responses muted. Mic stream may stay connected but Zoro will ignore speech."}


# ── Face upload ───────────────────────────────────────────────────────────────

@app.post("/attendance/upload")
async def upload_student_faces(files: list[UploadFile] = File(...)) -> dict:
    uploaded = 0
    failed = []
    known_faces_dir = Path(settings.data_dir) / "faces"
    known_faces_dir.mkdir(exist_ok=True)
    for file in files:
        try:
            safe_name = Path(file.filename or "student.jpg").name
            output = known_faces_dir / safe_name
            output.write_bytes(await file.read())
            uploaded += 1
        except Exception as e:
            failed.append(str(e))
    await asyncio.sleep(0)
    attendance.reload_faces()
    return {"ok": True, "uploaded": uploaded, "failed": failed}


@app.get("/attendance/download/{filename}")
async def download_attendance(filename: str) -> FileResponse:
    attend_dir = Path(settings.data_dir) / "attendance"
    path = attend_dir / filename
    if not path.exists():
        stripped = filename.replace("attendance_", "")
        path = attend_dir / stripped
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=filename, media_type="text/csv")


@app.post("/syllabus/upload")
async def upload_syllabus_files(files: List[UploadFile] = File(...), subject: str = Form("")) -> dict:
    uploaded = 0
    failed = []
    for file in files:
        try:
            safe_name = Path(file.filename or "syllabus.txt").name
            if Path(safe_name).suffix.lower() not in {".txt", ".md", ".csv", ".pdf", ".docx", ".pptx", ".zip", ".scorm", ".html", ".htm", ".xml"}:
                failed.append(f"{safe_name}: unsupported format")
                continue
            content = await file.read()
            output = settings.syllabus_dir / safe_name
            output.write_bytes(content)
            size_kb = round(len(content) / 1024, 1)
            save_syllabus(safe_name, subject or "General", size_kb)
            uploaded += 1
        except Exception as e:
            failed.append(str(e))
    rag_result = await asyncio.to_thread(rag.rebuild)
    return {"ok": True, "uploaded": uploaded, "failed": failed, "rag": rag_result}

