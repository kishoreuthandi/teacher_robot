import asyncio
import os
import time

import websockets
from websockets.exceptions import ConnectionClosed

from .camera import CameraStreamer


LAPTOP_WS_BASE = os.getenv("LAPTOP_WS_BASE", "ws://localhost:8001")
MIC_DEVICE = os.getenv("ZORO_MIC_DEVICE", "plughw:ZoroPnP")
SAMPLE_RATE = int(os.getenv("ZORO_AUDIO_SAMPLE_RATE", "16000"))
AUDIO_CHUNK = int(os.getenv("ZORO_AUDIO_CHUNK", "3200"))
CAMERA_INTERVAL_SECONDS = float(os.getenv("ZORO_CAMERA_INTERVAL_SECONDS", "0.11"))
CAMERA_JPEG_QUALITY = int(os.getenv("ZORO_CAMERA_JPEG_QUALITY", "50"))
CAMERA_MAX_AGE_MS = int(os.getenv("ZORO_CAMERA_MAX_AGE_MS", "450"))


async def stream_camera() -> None:
    url = f"{LAPTOP_WS_BASE.rstrip('/')}/ws/pi/video"
    camera = CameraStreamer()
    while True:
        try:
            async with websockets.connect(
                url,
                ping_interval=10,
                ping_timeout=10,
                open_timeout=8,
                close_timeout=1,
                max_size=None,
            ) as ws:
                while True:
                    try:
                        frame = await asyncio.to_thread(
                            camera.snapshot,
                            CAMERA_JPEG_QUALITY,
                            CAMERA_MAX_AGE_MS,
                            False,
                        )
                    except TimeoutError:
                        await asyncio.sleep(0.08)
                        continue
                    await ws.send(frame)
                    await asyncio.sleep(CAMERA_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[BodyNode] camera reconnecting: {type(exc).__name__}: {exc}", flush=True)
            await asyncio.sleep(1)


async def stream_microphone() -> None:
    url = f"{LAPTOP_WS_BASE.rstrip('/')}/ws/pi/audio"
    while True:
        proc = None
        drain_task = None
        try:
            async with websockets.connect(
                url,
                ping_interval=10,
                ping_timeout=10,
                open_timeout=8,
                close_timeout=1,
                max_size=None,
            ) as ws:
                drain_task = asyncio.create_task(_drain_server_messages(ws))
                proc = await asyncio.create_subprocess_exec(
                    "arecord",
                    "-D",
                    MIC_DEVICE,
                    "-f",
                    "S16_LE",
                    "-r",
                    str(SAMPLE_RATE),
                    "-c",
                    "1",
                    "-",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                while True:
                    chunk = await asyncio.wait_for(proc.stdout.read(AUDIO_CHUNK), timeout=2.5)
                    if not chunk:
                        break
                    try:
                        await ws.send(chunk)
                    except ConnectionClosed:
                        break
                    if proc.returncode is not None:
                        break
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            print("[BodyNode] microphone timed out, restarting capture", flush=True)
            await asyncio.sleep(0.2)
        except Exception as exc:
            print(f"[BodyNode] microphone reconnecting: {type(exc).__name__}: {exc}", flush=True)
            await asyncio.sleep(1)
        finally:
            if drain_task is not None:
                drain_task.cancel()
                try:
                    await drain_task
                except BaseException:
                    pass
            if proc and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()


async def _drain_server_messages(ws) -> None:
    while True:
        try:
            await ws.recv()
        except asyncio.CancelledError:
            raise
        except Exception:
            return


async def supervise(name: str, runner) -> None:
    while True:
        try:
            await runner()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            print(f"[BodyNode] {name} worker crashed, restarting: {type(exc).__name__}: {exc}", flush=True)
            await asyncio.sleep(1)


async def main() -> None:
    print(f"[BodyNode] streaming sensors to {LAPTOP_WS_BASE}", flush=True)
    mic_task = asyncio.create_task(supervise("microphone", stream_microphone))
    await asyncio.sleep(0.5)
    camera_task = asyncio.create_task(supervise("camera", stream_camera))
    await asyncio.gather(mic_task, camera_task)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"[BodyNode] stopped at {time.strftime('%H:%M:%S')}")
