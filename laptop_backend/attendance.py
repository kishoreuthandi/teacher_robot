import csv
from datetime import datetime
from pathlib import Path
import threading
from typing import Any

import cv2
import numpy as np

from .config import settings

try:
    import face_recognition
except ImportError:  # pragma: no cover - depends on local native install
    face_recognition = None


class AttendanceService:
    def __init__(self) -> None:
        self.attendance_dir = settings.data_dir / "attendance"
        self.attendance_dir.mkdir(parents=True, exist_ok=True)
        self.known_names: list[str] = []
        self.known_encodings: list[Any] = []
        self._lock = threading.RLock()
        self.reload_faces()

    def reload_faces(self) -> dict:
        with self._lock:
            self.known_names = []
            self.known_encodings = []
            if face_recognition is None:
                return {"available": False, "known_faces": 0}

            for image_path in sorted(Path(settings.face_dir).glob("*")):
                if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                    continue
                image = face_recognition.load_image_file(str(image_path))
                encodings = face_recognition.face_encodings(image)
                if not encodings:
                    continue
                self.known_names.append(image_path.stem)
                self.known_encodings.append(encodings[0])
            return {"available": True, "known_faces": len(self.known_names)}

    def today_csv_path(self) -> Path:
        return self.attendance_dir / f"{datetime.now().date().isoformat()}.csv"

    def mark(self, name: str) -> None:
        path = self.today_csv_path()
        seen = set()
        if path.exists():
            with path.open("r", newline="", encoding="utf-8") as file:
                for row in csv.DictReader(file):
                    seen.add(row.get("name", ""))
        if name in seen:
            return
        new_file = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=["name", "time"])
            if new_file:
                writer.writeheader()
            writer.writerow({"name": name, "time": datetime.now().isoformat(timespec="seconds")})

    def recognize_and_mark(self, jpeg_bytes: bytes) -> dict:
        with self._lock:
            if face_recognition is None:
                return {"available": False, "marked": [], "message": "Install face_recognition on the laptop."}
            if not self.known_encodings:
                return {"available": True, "marked": [], "message": "No known faces enrolled in data/faces."}

            array = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(array, cv2.IMREAD_COLOR)
            if frame is None:
                return {"available": True, "marked": [], "message": "Could not decode image."}

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            locations = face_recognition.face_locations(rgb)
            encodings = face_recognition.face_encodings(rgb, locations)
            marked: list[str] = []
            for encoding in encodings:
                matches = face_recognition.compare_faces(self.known_encodings, encoding, tolerance=0.6)
                if True not in matches:
                    continue
                name = self.known_names[matches.index(True)]
                self.mark(name)
                marked.append(name)
            return {"available": True, "marked": sorted(set(marked)), "faces_seen": len(encodings)}
