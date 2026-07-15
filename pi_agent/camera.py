import time
import threading
from collections.abc import Iterator
from pathlib import Path

import cv2

from .config import settings


class CameraStreamer:
    def __init__(self) -> None:
        self.camera_index = settings.camera_index
        self._cap = None
        self._lock = threading.Lock()
        self._new_frame = threading.Event()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._latest_frame = None
        self._latest_timestamp = 0.0
        self._last_open_attempt = 0.0
        self._selected_index = self.camera_index

    def _candidate_indices(self) -> list[int]:
        # The FINGERS 720 UVC camera exposes two low video nodes. On this Pi,
        # /dev/video0 can accept open() but then block in V4L2 select(); the
        # real MJPEG capture node is usually /dev/video1. Prefer the configured
        # index, then video1, then video0 so one flaky node does not keep the
        # dashboard stuck on the fallback frame after reboot.
        candidates = []
        preferred = [1]
        configured = int(self.camera_index)
        if configured not in preferred:
            preferred.append(configured)
        if 0 not in preferred:
            preferred.append(0)
        for index in preferred:
            if index not in candidates:
                candidates.append(index)
        for path in sorted(Path("/dev").glob("video*")):
            try:
                index = int(path.name.replace("video", ""))
            except ValueError:
                continue
            # Raspberry Pi codec/ISP devices usually live at high indices and
            # can block reads for seconds; USB UVC cameras normally use 0..9.
            if 0 <= index <= 9 and index not in candidates:
                candidates.append(index)
        for index in range(0, 4):
            if index not in candidates:
                candidates.append(index)
        return candidates

    def _open(self) -> bool:
        if self._cap is None or not self._cap.isOpened():
            now = time.perf_counter()
            if now - self._last_open_attempt < 2.0:
                return False
            self._last_open_attempt = now
            for index in self._candidate_indices():
                cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
                if not cap or not cap.isOpened():
                    if cap:
                        cap.release()
                    continue
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                cap.set(cv2.CAP_PROP_FPS, 30)
                self._cap = cap
                self._selected_index = index
                break
        return bool(self._cap and self._cap.isOpened())

    def _ensure_reader(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        if not self._open():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader_loop, name="zoro-camera-reader", daemon=True)
        self._thread.start()
        return True

    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            if not self._open():
                time.sleep(0.25)
                continue
            ok, frame = self._cap.read()
            if not ok:
                time.sleep(0.03)
                continue
            with self._lock:
                self._latest_frame = frame
                self._latest_timestamp = time.perf_counter()
            self._new_frame.set()

    def read_latest(self, max_age_ms: int = 500):
        if not self._ensure_reader():
            raise TimeoutError("Camera is not available.")
        with self._lock:
            frame = None if self._latest_frame is None else self._latest_frame.copy()
            timestamp = self._latest_timestamp
        if frame is None:
            raise TimeoutError("No camera frame has arrived yet.")
        age_ms = (time.perf_counter() - timestamp) * 1000
        if age_ms > max_age_ms:
            self._restart_capture()
            raise TimeoutError(f"Latest camera frame is stale: {age_ms:.1f} ms.")
        return frame, timestamp

    def async_read(self, timeout_ms: int = 200):
        if not self._ensure_reader():
            raise TimeoutError("Camera is not available.")
        if not self._new_frame.wait(timeout=timeout_ms / 1000):
            raise TimeoutError("Timed out waiting for camera frame.")
        self._new_frame.clear()
        return self.read_latest(max_age_ms=max(timeout_ms * 2, 500))

    def frames(self) -> Iterator[bytes]:
        last_timestamp = 0.0
        try:
            while True:
                if not self._ensure_reader():
                    yield self._multipart(self._fallback_jpeg())
                    time.sleep(0.5)
                    continue
                try:
                    frame, timestamp = self.read_latest(max_age_ms=1000)
                except TimeoutError:
                    yield self._multipart(self._fallback_jpeg())
                    time.sleep(0.25)
                    continue
                if timestamp == last_timestamp:
                    time.sleep(0.01)
                    continue
                ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
                if ok:
                    last_timestamp = timestamp
                    yield self._multipart(buffer.tobytes())
                time.sleep(0.015)
        finally:
            self.close()

    def snapshot(self, quality: int = 85, max_age_ms: int = 1000, fallback: bool = True) -> bytes:
        try:
            frame, _ = self.read_latest(max_age_ms=max_age_ms)
        except TimeoutError:
            if not fallback:
                raise
            return self._fallback_jpeg()
        ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            raise RuntimeError("Could not encode snapshot.")
        return buffer.tobytes()

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        if self._cap:
            self._cap.release()
        self._cap = None
        self._thread = None

    def _restart_capture(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.2)
        if self._cap:
            self._cap.release()
        self._cap = None
        self._thread = None
        self._latest_frame = None
        self._latest_timestamp = 0.0
        self._last_open_attempt = 0.0
        self._stop.clear()

    @staticmethod
    def _multipart(jpeg: bytes) -> bytes:
        return b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"

    @staticmethod
    def _fallback_jpeg() -> bytes:
        import numpy as np

        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(
            frame,
            "Camera not available",
            (110, 180),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (255, 255, 255),
            2,
        )
        ok, buffer = cv2.imencode(".jpg", frame)
        if not ok:
            return b""
        return buffer.tobytes()
