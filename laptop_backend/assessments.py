from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings


class AssessmentStore:
    def __init__(self) -> None:
        self.path = settings.data_dir / "assessments.json"

    def create(self, title: str, subject: str = "", instructions: str = "", due_at: str = "") -> dict[str, Any]:
        data = self._load()
        item = {
            "id": str(uuid.uuid4())[:8],
            "title": title.strip() or "Class assessment",
            "subject": subject.strip() or "General",
            "instructions": instructions.strip(),
            "due_at": due_at.strip(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "status": "open",
            "submissions": {},
        }
        data[item["id"]] = item
        self._save(data)
        return item

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._load().values(), key=lambda item: item.get("created_at", ""), reverse=True)

    def submit(self, assessment_id: str, student_name: str, note: str = "") -> dict[str, Any] | None:
        data = self._load()
        item = data.get(assessment_id)
        if not item:
            return None
        submissions = item.setdefault("submissions", {})
        submissions[student_name or "Unknown student"] = {
            "student_name": student_name or "Unknown student",
            "note": note,
            "submitted_at": datetime.now().isoformat(timespec="seconds"),
            "status": "completed",
        }
        data[assessment_id] = item
        self._save(data)
        return item

    def close(self, assessment_id: str, expected_students: list[str]) -> dict[str, Any] | None:
        data = self._load()
        item = data.get(assessment_id)
        if not item:
            return None
        item["status"] = "closed"
        item["closed_at"] = datetime.now().isoformat(timespec="seconds")
        submitted = set((item.get("submissions") or {}).keys())
        missing = [name for name in expected_students if name and name not in submitted]
        item["missing_students"] = missing
        data[assessment_id] = item
        self._save(data)
        return item

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
