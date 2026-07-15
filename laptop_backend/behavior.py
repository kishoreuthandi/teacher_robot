import csv
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings

BEHAVIOR_WEIGHTS = {
    "respectful": 2,
    "attentive": 1,
    "profanity": -12,
    "insult": -8,
    "unwanted_talk": -4,
    "mobile_phone": -6,
    "left_without_permission": -10,
    "missed_assessment": -8,
    "rule_violation": -6,
    "not_present": -8,
}


VISUAL_BEHAVIOR_RULES = {
    "writing": {"kind": "attentive", "note": "Observed writing or taking notes during class.", "severity": "info", "score_delta": 1},
    "write": {"kind": "attentive", "note": "Observed writing or taking notes during class.", "severity": "info", "score_delta": 1},
    "reading": {"kind": "attentive", "note": "Observed reading during class.", "severity": "info", "score_delta": 1},
    "read": {"kind": "attentive", "note": "Observed reading during class.", "severity": "info", "score_delta": 1},
    "listening": {"kind": "attentive", "note": "Observed listening during class.", "severity": "info", "score_delta": 1},
    "raising hand": {"kind": "attentive", "note": "Raised hand to participate.", "severity": "info", "score_delta": 2},
    "raise_hand": {"kind": "attentive", "note": "Raised hand to participate.", "severity": "info", "score_delta": 2},
    "hand-raising": {"kind": "attentive", "note": "Raised hand to participate.", "severity": "info", "score_delta": 2},
    "standing": {"kind": "rule_violation", "note": "Standing during class; check whether the teacher permitted it.", "severity": "warning", "score_delta": -3},
    "turning around": {"kind": "unwanted_talk", "note": "Turned around during class; possible distraction.", "severity": "warning", "score_delta": -3},
    "turn_head": {"kind": "unwanted_talk", "note": "Turned away during class; possible distraction.", "severity": "warning", "score_delta": -3},
    "discussing": {"kind": "unwanted_talk", "note": "Possible side discussion during teaching time.", "severity": "warning", "score_delta": -4},
    "discuss": {"kind": "unwanted_talk", "note": "Possible side discussion during teaching time.", "severity": "warning", "score_delta": -4},
    "sleeping": {"kind": "not_attentive", "note": "Possible sleeping or head-down posture during class.", "severity": "warning", "score_delta": -7},
    "sleep": {"kind": "not_attentive", "note": "Possible sleeping or head-down posture during class.", "severity": "warning", "score_delta": -7},
    "yawning": {"kind": "not_attentive", "note": "Possible tiredness detected; teacher may need to slow down or take a break.", "severity": "info", "score_delta": -2},
    "using mobile phone": {"kind": "mobile_phone", "note": "Possible mobile phone use detected during class.", "severity": "warning", "score_delta": -6},
    "mobile phone": {"kind": "mobile_phone", "note": "Possible mobile phone use detected during class.", "severity": "warning", "score_delta": -6},
    "phone": {"kind": "mobile_phone", "note": "Possible mobile phone use detected during class.", "severity": "warning", "score_delta": -6},
}


def visual_behavior_rule(label: str) -> dict[str, Any] | None:
    normalized = label.lower().replace("-", " ").replace("_", " ").strip()
    for key, rule in VISUAL_BEHAVIOR_RULES.items():
        if key.replace("_", " ") == normalized:
            return rule
    return None


@dataclass
class BehaviorEvent:
    id: str
    student_name: str
    kind: str
    note: str
    score_delta: int
    severity: str
    timestamp: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "student_name": self.student_name,
            "kind": self.kind,
            "note": self.note,
            "score_delta": self.score_delta,
            "severity": self.severity,
            "timestamp": self.timestamp,
            "evidence": self.evidence,
        }


class BehaviorStore:
    def __init__(self) -> None:
        self.path = settings.data_dir / "behavior_events.csv"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def detect_profanity(self, text: str) -> list[str]:
        lowered = text.lower()
        hits: list[str] = []
        for pattern in PROFANITY_PATTERNS:
            match = re.search(pattern, lowered)
            if match:
                hits.append(match.group(0))
        return sorted(set(hits))

    def add_event(
        self,
        student_name: str | None,
        kind: str,
        note: str,
        severity: str = "info",
        evidence: dict[str, Any] | None = None,
        score_delta: int | None = None,
    ) -> dict[str, Any]:
        event = BehaviorEvent(
            id=str(uuid.uuid4())[:8],
            student_name=(student_name or "Unknown student").strip() or "Unknown student",
            kind=kind,
            note=note,
            score_delta=score_delta if score_delta is not None else BEHAVIOR_WEIGHTS.get(kind, -1),
            severity=severity,
            timestamp=datetime.now().isoformat(timespec="seconds"),
            evidence=evidence or {},
        )
        new_file = not self.path.exists()
        with self.path.open("a", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=["id", "student_name", "kind", "note", "score_delta", "severity", "timestamp", "evidence"],
            )
            if new_file:
                writer.writeheader()
            row = event.as_dict()
            row["evidence"] = str(row["evidence"])
            writer.writerow(row)
        return event.as_dict()

    def events(self, limit: int = 200, student_name: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        if student_name:
            wanted = student_name.lower()
            rows = [row for row in rows if row.get("student_name", "").lower() == wanted]
        for row in rows:
            try:
                row["score_delta"] = int(row.get("score_delta") or 0)
            except ValueError:
                row["score_delta"] = 0
        return list(reversed(rows))[: max(1, min(limit, 1000))]

    def summary(self) -> dict[str, Any]:
        rows = list(reversed(self.events(limit=1000)))
        students: dict[str, dict[str, Any]] = {}
        for row in rows:
            name = row.get("student_name") or "Unknown student"
            item = students.setdefault(
                name,
                {
                    "student_name": name,
                    "score": 100,
                    "events": 0,
                    "warnings": 0,
                    "positive": 0,
                    "last_event": "",
                    "breakdown": {},
                },
            )
            delta = int(row.get("score_delta") or 0)
            item["score"] += delta
            item["score"] = max(0, min(120, item["score"]))
            item["events"] += 1
            item["last_event"] = row.get("timestamp") or item["last_event"]
            if delta < 0:
                item["warnings"] += 1
            else:
                item["positive"] += 1
            kind = row.get("kind") or "event"
            item["breakdown"][kind] = item["breakdown"].get(kind, 0) + 1
        ranked = sorted(students.values(), key=lambda item: (item["score"], -item["events"]))
        return {
            "students": ranked,
            "events": list(reversed(rows))[:50],
            "total_events": len(rows),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def report_path(self) -> Path:
        summary = self.summary()
        report = settings.data_dir / "behavior_report.csv"
        with report.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["Student", "Score", "Events", "Warnings", "Positive", "Breakdown", "Last Event"])
            for student in summary["students"]:
                writer.writerow([
                    student["student_name"],
                    student["score"],
                    student["events"],
                    student["warnings"],
                    student["positive"],
                    student["breakdown"],
                    student["last_event"],
                ])
            writer.writerow([])
            writer.writerow(["Event Time", "Student", "Kind", "Severity", "Score Delta", "Note"])
            for event in self.events(limit=1000):
                writer.writerow([
                    event.get("timestamp", ""),
                    event.get("student_name", ""),
                    event.get("kind", ""),
                    event.get("severity", ""),
                    event.get("score_delta", ""),
                    event.get("note", ""),
                ])
        return report
