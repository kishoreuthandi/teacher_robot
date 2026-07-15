import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .config import settings

try:
    import face_recognition
except ImportError:  # pragma: no cover
    face_recognition = None


INTRO_PATTERN = re.compile(
    r"(?:this is|she is|he is|meet)\s+([a-zA-Z][a-zA-Z .'-]{1,40}?)(?:\s+(?:our|the)\s+([a-zA-Z][a-zA-Z .'-]{1,40}?))?(?=\s+(?:and|this is|she is|he is|meet)|[,.]|$)",
    re.IGNORECASE,
)


class PeopleMemory:
    def __init__(self) -> None:
        self.path = settings.data_dir / "people_profiles.json"
        settings.face_dir.mkdir(parents=True, exist_ok=True)

    def profiles(self) -> list[dict[str, Any]]:
        data = self._load()
        return sorted(data.values(), key=lambda item: item.get("name", ""))

    def parse_introductions(self, text: str) -> list[dict[str, str]]:
        people: list[dict[str, str]] = []
        for match in INTRO_PATTERN.finditer(text):
            name = self._clean_name(match.group(1))
            role = self._clean_role(match.group(2) or "")
            if not name:
                continue
            people.append({"name": name, "role": role})
            if len(people) >= 5:
                break
        return people

    def enroll_from_jpeg(self, jpeg: bytes | None, people: list[dict[str, str]]) -> dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        data = self._load()
        face_paths: list[str | None] = [None] * len(people)
        if jpeg:
            face_paths = self._save_face_crops(jpeg, people)

        enrolled = []
        for index, person in enumerate(people):
            key = self._key(person["name"])
            existing = data.get(key, {})
            profile = {
                **existing,
                "name": person["name"],
                "role": person.get("role") or existing.get("role", ""),
                "first_seen": existing.get("first_seen") or now,
                "last_seen": now,
                "times_seen": int(existing.get("times_seen") or 0) + 1,
                "face_file": face_paths[index] or existing.get("face_file", ""),
            }
            data[key] = profile
            enrolled.append(profile)
        self._save(data)
        return {"enrolled": enrolled, "count": len(enrolled), "faces_saved": sum(1 for path in face_paths if path)}

    def touch_seen(self, names: list[str]) -> None:
        if not names:
            return
        data = self._load()
        now = datetime.now().isoformat(timespec="seconds")
        changed = False
        for name in names:
            key = self._key(name)
            if key not in data:
                continue
            data[key]["last_seen"] = now
            data[key]["times_seen"] = int(data[key].get("times_seen") or 0) + 1
            changed = True
        if changed:
            self._save(data)

    def greeting_for(self, names: list[str]) -> str | None:
        data = self._load()
        known = [data[self._key(name)] for name in names if self._key(name) in data]
        if not known:
            return None
        profile = known[0]
        name = profile.get("name", names[0])
        role = profile.get("role", "")
        last_seen = profile.get("last_seen", "")
        gap = self._days_since(last_seen)
        if gap is not None and gap >= 14:
            weeks = max(2, round(gap / 7))
            return f"Hello {name}. It has been about {weeks} weeks since we last met. Is everything alright?"
        title = role if role else name
        return f"Hello {title}. Good to see you."

    def _save_face_crops(self, jpeg: bytes, people: list[dict[str, str]]) -> list[str | None]:
        array = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(array, cv2.IMREAD_COLOR)
        if frame is None:
            return [None] * len(people)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        boxes: list[tuple[int, int, int, int]] = []
        if face_recognition is not None:
            for top, right, bottom, left in face_recognition.face_locations(rgb):
                boxes.append((left, top, right, bottom))
        if not boxes:
            cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            for x, y, w, h in cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40)):
                boxes.append((int(x), int(y), int(x + w), int(y + h)))
        boxes = sorted(boxes, key=lambda item: item[0])[: len(people)]
        saved: list[str | None] = [None] * len(people)
        height, width = frame.shape[:2]
        for index, box in enumerate(boxes):
            left, top, right, bottom = box
            pad_x = int((right - left) * 0.25)
            pad_y = int((bottom - top) * 0.35)
            x1 = max(0, left - pad_x)
            y1 = max(0, top - pad_y)
            x2 = min(width, right + pad_x)
            y2 = min(height, bottom + pad_y)
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            filename = self._safe_filename(people[index]["name"]) + ".jpg"
            path = settings.face_dir / filename
            cv2.imwrite(str(path), crop)
            saved[index] = str(path)
        return saved

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")

    def _clean_name(self, value: str) -> str:
        words = [word for word in re.split(r"\s+", value.strip(" .,'\"")) if word.lower() not in {"our", "the"}]
        return " ".join(words[:4]).title()

    def _clean_role(self, value: str) -> str:
        role = value.strip(" .,'\"")
        role = re.sub(r"\b(and|he|she|this is|meet)$", "", role, flags=re.IGNORECASE).strip()
        return role.upper() if role.lower() in {"hod", "principal"} else role.title()

    def _key(self, name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

    def _safe_filename(self, name: str) -> str:
        return self._key(name) or "person"

    def _days_since(self, timestamp: str) -> int | None:
        try:
            then = datetime.fromisoformat(timestamp)
        except Exception:
            return None
        return (datetime.now() - then).days
