import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings


DEFAULT_SELF_MODEL: dict[str, Any] = {
    "identity": {
        "name": "Zoro 2026",
        "purpose": "A warm classroom teaching robot that sees, hears, speaks, remembers, and moves carefully.",
        "body": {
            "camera": "eyes",
            "microphone": "ears",
            "speaker": "mouth",
            "motors": "legs",
            "display": "face",
            "pi_zero_2w": "body controller",
            "laptop": "brain",
        },
    },
    "physical_state": {
        "location": "classroom",
        "facing": "unknown",
        "battery": "external power or unknown",
        "last_motion": "still",
    },
    "session_state": {
        "active_since": None,
        "people_spoken_to_today": [],
        "topics_covered_today": [],
        "last_interaction_at": None,
    },
    "emotion": {
        "mood": "calm and attentive",
        "energy": 0.6,
        "notes": [],
    },
}


class SelfModel:
    def __init__(self) -> None:
        self.path = settings.data_dir / "self_model.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        if not self.state["session_state"].get("active_since"):
            self.state["session_state"]["active_since"] = datetime.now().isoformat(timespec="seconds")
            self.save()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return json.loads(json.dumps(DEFAULT_SELF_MODEL))
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return json.loads(json.dumps(DEFAULT_SELF_MODEL))
        return self._merge(DEFAULT_SELF_MODEL, loaded)

    def _merge(self, defaults: dict[str, Any], loaded: dict[str, Any]) -> dict[str, Any]:
        merged = dict(defaults)
        for key, value in loaded.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    def save(self) -> None:
        self.path.write_text(json.dumps(self.state, indent=2, ensure_ascii=True), encoding="utf-8")

    def context(self) -> str:
        return json.dumps(self.state, ensure_ascii=True, indent=2)

    def remember_interaction(self, student_name: str | None, topic: str | None) -> None:
        session = self.state["session_state"]
        session["last_interaction_at"] = datetime.now().isoformat(timespec="seconds")
        if student_name and student_name not in session["people_spoken_to_today"]:
            session["people_spoken_to_today"].append(student_name)
        if topic and topic not in session["topics_covered_today"]:
            session["topics_covered_today"].append(topic)
        self.save()

    def update_motion(self, direction: str) -> None:
        self.state["physical_state"]["last_motion"] = direction
        if direction in {"left", "right"}:
            self.state["physical_state"]["facing"] = f"turned {direction} from previous heading"
        self.save()

    def update_mood(self, mood: str, energy: float | None = None, note: str | None = None) -> None:
        self.state["emotion"]["mood"] = mood
        if energy is not None:
            self.state["emotion"]["energy"] = max(0.0, min(1.0, energy))
        if note:
            notes = self.state["emotion"].setdefault("notes", [])
            notes.append({"time": datetime.now().isoformat(timespec="seconds"), "note": note})
            self.state["emotion"]["notes"] = notes[-20:]
        self.save()
