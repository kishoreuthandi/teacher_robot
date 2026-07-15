import asyncio
import contextlib
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .camera import CameraStreamer
from .motors import DriveBase


class MoveRequest(BaseModel):
    direction: str = Field(pattern="^(forward|backward|left|right|rotate|stop)$")
    speed: float = Field(default=0.65, ge=0.0, le=1.0)


app = FastAPI(title="Zoro 2026 Pi Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
drive = DriveBase()
speaker_lock = asyncio.Lock()
current_speaker_proc: asyncio.subprocess.Process | None = None
face_state = {"speaking": False, "updated_at": None}
SPEAKER_DEVICE = os.getenv("ZORO_SPEAKER_DEVICE", "plughw:ZoroSpeaker")
PCM_GAIN = float(os.getenv("ZORO_SPEAKER_GAIN", "0.92"))
SPEAKER_BUFFER_US = os.getenv("ZORO_SPEAKER_BUFFER_US", "140000")


def _set_face_speaking(value: bool) -> None:
    face_state["speaking"] = value
    face_state["updated_at"] = datetime.now(timezone.utc).isoformat()


def _mono_pcm16_to_stereo(data: bytes, pending: bytes = b"") -> tuple[bytes, bytes]:
    raw = pending + data
    if len(raw) < 2:
        return b"", raw
    if len(raw) % 2:
        pending = raw[-1:]
        raw = raw[:-1]
    else:
        pending = b""
    out = bytearray(len(raw) * 2)
    write_at = 0
    for read_at in range(0, len(raw), 2):
        value = int.from_bytes(raw[read_at:read_at + 2], byteorder="little", signed=True)
        value = max(-32768, min(32767, int(value * PCM_GAIN)))
        sample = int(value).to_bytes(2, byteorder="little", signed=True)
        out[write_at:write_at + 2] = sample
        out[write_at + 2:write_at + 4] = sample
        write_at += 4
    return bytes(out), pending


async def _stop_current_speaker() -> None:
    global current_speaker_proc
    proc = current_speaker_proc
    current_speaker_proc = None
    if proc and proc.returncode is None:
        proc.terminate()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        if proc.returncode is None:
            proc.kill()
    _set_face_speaking(False)


@app.get("/face/state")
async def get_face_state() -> dict:
    return dict(face_state)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "service": "pi_agent",
        "drive": drive.status(),
        "hardware": {
            "camera_present": any(Path(f"/dev/video{index}").exists() for index in range(10)),
            "mic_present": _alsa_device_present("arecord"),
            "speaker_present": _alsa_device_present("aplay"),
        },
    }


def _alsa_device_present(command: str) -> bool:
    try:
        result = subprocess.run(
            [command, "-l"],
            capture_output=True,
            text=True,
            timeout=0.8,
            check=False,
        )
    except Exception:
        return False
    output = f"{result.stdout}\n{result.stderr}".lower()
    return result.returncode == 0 and "card " in output and "no soundcards" not in output


@app.post("/move")
async def move(request: MoveRequest) -> dict:
    return drive.move(request.direction, request.speed)


@app.post("/stop")
async def stop() -> dict:
    return drive.stop()


@app.websocket("/ws/motor")
async def motor_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            command = await websocket.receive_json()
            direction = command.get("direction", "stop")
            speed = float(command.get("speed", 0.65))
            if direction == "stop":
                result = drive.stop()
            else:
                result = drive.move(direction, speed)
            await websocket.send_json(result)
    except WebSocketDisconnect:
        drive.stop()


@app.get("/video.mjpeg")
async def video() -> StreamingResponse:
    camera = CameraStreamer()

    def stream():
        try:
            yield from camera.frames()
        finally:
            camera.close()

    return StreamingResponse(stream(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/snapshot.jpg")
async def snapshot() -> Response:
    camera = CameraStreamer()
    try:
        content = camera.snapshot()
    finally:
        camera.close()
    return Response(content=content, media_type="image/jpeg")


@app.post("/speaker/play")
async def play_speaker(request: Request) -> dict:
    global current_speaker_proc
    async with speaker_lock:
        await _stop_current_speaker()
        content_type = request.headers.get("content-type", "")
        suffix = ".wav" if "wav" in content_type else ".mp3"
        data = await request.body()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
            temp.write(data)
            temp_path = temp.name
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffplay",
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "quiet",
                temp_path,
            )
            current_speaker_proc = proc
            _set_face_speaking(True)
            await proc.wait()
            return {"ok": proc.returncode == 0, "returncode": proc.returncode}
        finally:
            if current_speaker_proc is proc:
                current_speaker_proc = None
            _set_face_speaking(False)
            with contextlib.suppress(OSError):
                Path(temp_path).unlink()


@app.post("/speaker/stop")
async def stop_speaker() -> dict:
    async with speaker_lock:
        await _stop_current_speaker()
    return {"ok": True, "stopped": True}


async def _cleanup_playback(proc: asyncio.subprocess.Process, temp_path: str) -> None:
    global current_speaker_proc
    with contextlib.suppress(Exception):
        await proc.wait()
    if current_speaker_proc is proc:
        current_speaker_proc = None
    _set_face_speaking(False)
    with contextlib.suppress(OSError):
        Path(temp_path).unlink()


@app.post("/speaker/play-async")
async def play_speaker_async(request: Request) -> dict:
    global current_speaker_proc
    async with speaker_lock:
        await _stop_current_speaker()
        content_type = request.headers.get("content-type", "")
        suffix = ".wav" if "wav" in content_type else ".mp3"
        data = await request.body()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
            temp.write(data)
            temp_path = temp.name
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffplay",
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "quiet",
                temp_path,
            )
        except Exception:
            with contextlib.suppress(OSError):
                Path(temp_path).unlink()
            raise
        current_speaker_proc = proc
        _set_face_speaking(True)
        asyncio.create_task(_cleanup_playback(proc, temp_path))
        return {"ok": True, "started": True, "bytes": len(data), "pid": proc.pid}


@app.post("/speaker/pcm-play-async")
async def play_pcm_speaker_async(request: Request, sample_rate: int = 24000) -> dict:
    global current_speaker_proc
    async with speaker_lock:
        await _stop_current_speaker()
        data = await request.body()
        data, pending = _mono_pcm16_to_stereo(data)
        if pending:
            padded, _ = _mono_pcm16_to_stereo(b"\x00", pending)
            data += padded
        with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as temp:
            temp.write(data)
            temp_path = temp.name
        try:
            proc = await asyncio.create_subprocess_exec(
                "aplay",
                "-q",
                "-D",
                SPEAKER_DEVICE,
                "-B",
                SPEAKER_BUFFER_US,
                "-f",
                "S16_LE",
                "-r",
                str(sample_rate),
                "-c",
                "2",
                temp_path,
            )
        except Exception:
            with contextlib.suppress(OSError):
                Path(temp_path).unlink()
            raise
        current_speaker_proc = proc
        _set_face_speaking(True)
        asyncio.create_task(_cleanup_playback(proc, temp_path))
        return {"ok": True, "started": True, "bytes": len(data), "pid": proc.pid, "sample_rate": sample_rate, "channels": 2}


@app.post("/speaker/stream")
async def stream_speaker(request: Request) -> dict:
    global current_speaker_proc
    async with speaker_lock:
        await _stop_current_speaker()
        proc = await asyncio.create_subprocess_exec(
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-loglevel",
            "quiet",
            "-i",
            "pipe:0",
            stdin=asyncio.subprocess.PIPE,
        )
        current_speaker_proc = proc
        _set_face_speaking(True)
        bytes_written = 0
        try:
            async for chunk in request.stream():
                if not chunk:
                    continue
                bytes_written += len(chunk)
                if proc.stdin is None:
                    break
                proc.stdin.write(chunk)
                await proc.stdin.drain()
            if proc.stdin is not None:
                proc.stdin.close()
            await proc.wait()
            return {"ok": proc.returncode == 0, "returncode": proc.returncode, "bytes": bytes_written}
        except Exception:
            proc.kill()
            raise
        finally:
            if current_speaker_proc is proc:
                current_speaker_proc = None
            _set_face_speaking(False)


@app.post("/speaker/pcm-stream")
async def stream_pcm_speaker(request: Request, sample_rate: int = 24000) -> dict:
    global current_speaker_proc
    async with speaker_lock:
        await _stop_current_speaker()
        proc = await asyncio.create_subprocess_exec(
            "aplay",
            "-q",
            "-D",
            SPEAKER_DEVICE,
            "-B",
            SPEAKER_BUFFER_US,
            "-f",
            "S16_LE",
            "-r",
            str(sample_rate),
            "-c",
            "2",
            stdin=asyncio.subprocess.PIPE,
        )
        current_speaker_proc = proc
        bytes_written = 0
        pending = b""
        _set_face_speaking(True)
        try:
            async for chunk in request.stream():
                if not chunk:
                    continue
                chunk, pending = _mono_pcm16_to_stereo(chunk, pending)
                if not chunk:
                    continue
                bytes_written += len(chunk)
                if proc.stdin is None:
                    break
                proc.stdin.write(chunk)
                await proc.stdin.drain()
            if pending and proc.stdin is not None:
                chunk, _ = _mono_pcm16_to_stereo(b"\x00", pending)
                bytes_written += len(chunk)
                proc.stdin.write(chunk)
                await proc.stdin.drain()
            if proc.stdin is not None:
                proc.stdin.close()
            await proc.wait()
            return {"ok": proc.returncode == 0, "returncode": proc.returncode, "bytes": bytes_written, "channels": 2}
        except Exception:
            proc.kill()
            raise
        finally:
            if current_speaker_proc is proc:
                current_speaker_proc = None
            _set_face_speaking(False)


@app.websocket("/ws/tts")
async def tts_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            audio = await websocket.receive_bytes()
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp:
                temp.write(audio)
                temp_path = temp.name
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ffplay",
                    "-nodisp",
                    "-autoexit",
                    "-loglevel",
                    "quiet",
                    temp_path,
                )
                _set_face_speaking(True)
                await proc.wait()
                await websocket.send_json({"ok": proc.returncode == 0, "returncode": proc.returncode})
            finally:
                _set_face_speaking(False)
                try:
                    Path(temp_path).unlink()
                except OSError:
                    pass
    except WebSocketDisconnect:
        return
