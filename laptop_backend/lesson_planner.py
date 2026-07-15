from pathlib import Path
import json
import re
from datetime import datetime
from typing import Any

from .config import settings
from .syllabus import SUPPORTED_SYLLABUS_SUFFIXES, read_syllabus_file


class LessonPlanner:
    def __init__(self) -> None:
        self.progress_path = settings.data_dir / "lesson_progress.json"

    def available_subjects(self) -> list[dict[str, Any]]:
        subjects: list[dict[str, Any]] = []
        for path in sorted(Path(settings.syllabus_dir).glob("*")):
            if path.suffix.lower() not in SUPPORTED_SYLLABUS_SUFFIXES:
                continue
            subjects.append({"filename": path.name, "size_kb": round(path.stat().st_size / 1024, 1)})
        return subjects

    def build_plan(self, subject: str = "", duration_minutes: int = 30, break_count: int = 2) -> dict[str, Any]:
        duration_minutes = max(10, min(duration_minutes, 180))
        break_count = max(0, min(break_count, 4))
        break_minutes = 5 if duration_minutes >= 25 else 2
        teaching_minutes = max(5, duration_minutes - break_count * break_minutes)

        docs = self._matching_docs(subject)
        content = "\n\n".join(read_syllabus_file(path)[:7000] for path in docs)
        topics = self._extract_topics(content, subject)
        if not topics:
            topics = self._fallback_topics(subject or self._subject_from_docs(docs))

        teaching_slots = break_count + 1
        base_slot_minutes = max(3, teaching_minutes // teaching_slots)
        extra_minutes = max(0, teaching_minutes - base_slot_minutes * teaching_slots)
        schedule: list[dict[str, Any]] = []
        topic_index = 0
        for slot in range(teaching_slots):
            minutes = base_slot_minutes + (1 if slot < extra_minutes else 0)
            slot_topics = topics[topic_index: topic_index + 3]
            if not slot_topics:
                slot_topics = topics[:3]
            topic_index += max(1, len(slot_topics))
            title = self._segment_title(slot, slot_topics)
            schedule.append({
                "type": "teaching",
                "minutes": minutes,
                "title": title,
                "topics": slot_topics,
                "objective": self._objective_for(slot, slot_topics),
                "activity": self._activity_for(slot, slot_topics),
                "teaching_points": self._teaching_points(slot_topics, content),
                "check_questions": self._check_questions(slot_topics),
                "spoken_script": self._spoken_script(slot, slot_topics, minutes),
            })
            if slot < break_count:
                schedule.append({
                    "type": "break",
                    "minutes": break_minutes,
                    "title": f"{break_minutes}-minute break",
                    "activity": "Let students rest quietly. Resume only when the break ends.",
                    "spoken_script": f"We will take a {break_minutes}-minute break now. Please stay nearby and come back quietly when the break is over.",
                })

        subject_name = subject or self._subject_from_docs(docs) or "Uploaded syllabus"
        return {
            "subject": subject_name,
            "duration_minutes": duration_minutes,
            "break_count": break_count,
            "break_minutes": break_minutes,
            "teaching_minutes": teaching_minutes,
            "source_files": [path.name for path in docs],
            "topics": topics[:12],
            "schedule": schedule,
            "greeting": (
                f"Hello students. Today's class is about {subject_name}. "
                f"We have {duration_minutes} minutes, with {break_count} break{'s' if break_count != 1 else ''}. "
                "Please listen carefully, keep your doubts ready, and I will explain step by step."
            ),
            "closing": (
                f"That completes today's {subject_name} lesson. "
                "Please revise the key points and ask your remaining doubts before the next class."
            ),
            "rules": [
                "Stay inside the uploaded subject material.",
                "Ask short understanding checks after each segment.",
                "If many students look tired or sad, slow down and encourage them.",
                "Do not answer unrelated questions during the lesson unless the teacher allows it.",
            ],
        }

    def start_or_resume(self, subject: str = "", duration_minutes: int = 30, break_count: int = 2) -> dict[str, Any]:
        plan = self.build_plan(subject, duration_minutes, break_count)
        progress = self._load_progress()
        key = self._key(plan["subject"])
        existing = progress.get(key, {})
        session = {
            "subject": plan["subject"],
            "source_files": plan["source_files"],
            "duration_minutes": plan["duration_minutes"],
            "break_count": plan["break_count"],
            "break_minutes": plan.get("break_minutes", 5),
            "teaching_minutes": plan.get("teaching_minutes", plan["duration_minutes"]),
            "schedule": plan["schedule"],
            "greeting": plan.get("greeting", ""),
            "closing": plan.get("closing", ""),
            "current_index": min(int(existing.get("current_index") or 0), max(len(plan["schedule"]) - 1, 0)),
            "completed_minutes": int(existing.get("completed_minutes") or 0),
            "started_at": existing.get("started_at") or datetime.now().isoformat(timespec="seconds"),
            "resumed_at": datetime.now().isoformat(timespec="seconds"),
            "status": "active",
        }
        progress[key] = session
        self._save_progress(progress)
        return {**plan, "progress": self._progress_view(session)}

    def advance(self, subject: str = "", minutes: int | None = None) -> dict[str, Any]:
        progress = self._load_progress()
        key = self._key(subject or "General")
        session = progress.get(key)
        if not session:
            return {"ok": False, "message": "No active lesson progress for this subject."}
        schedule = session.get("schedule") or []
        if not schedule:
            return {"ok": False, "message": "Lesson has no schedule."}
        current = min(int(session.get("current_index") or 0), len(schedule) - 1)
        current_item = schedule[current]
        session["completed_minutes"] = int(session.get("completed_minutes") or 0) + int(minutes or current_item.get("minutes") or 0)
        session["current_index"] = min(current + 1, len(schedule))
        session["status"] = "completed" if session["current_index"] >= len(schedule) else "active"
        session["updated_at"] = datetime.now().isoformat(timespec="seconds")
        progress[key] = session
        self._save_progress(progress)
        return {"ok": True, "progress": self._progress_view(session)}

    def stop(self, subject: str = "") -> dict[str, Any]:
        progress = self._load_progress()
        updated_at = datetime.now().isoformat(timespec="seconds")
        if not subject:
            stopped = []
            for key, session in progress.items():
                if session and session.get("status") == "active":
                    session["status"] = "paused"
                    session["updated_at"] = updated_at
                    progress[key] = session
                    stopped.append(self._progress_view(session))
            self._save_progress(progress)
            return {"ok": True, "progress": stopped}

        key = self._key(subject)
        session = progress.get(key)
        if session:
            session["status"] = "paused"
            session["updated_at"] = updated_at
            progress[key] = session
            self._save_progress(progress)
            return {"ok": True, "progress": self._progress_view(session)}
        return {"ok": True, "progress": None}

    def progress(self, subject: str = "") -> dict[str, Any]:
        progress = self._load_progress()
        if subject:
            session = progress.get(self._key(subject))
            return {"items": [self._progress_view(session)] if session else []}
        return {"items": [self._progress_view(item) for item in progress.values()]}

    def _matching_docs(self, subject: str) -> list[Path]:
        docs = [path for path in sorted(Path(settings.syllabus_dir).glob("*")) if path.suffix.lower() in SUPPORTED_SYLLABUS_SUFFIXES]
        if not subject:
            return docs
        subject_text = subject.lower()
        matches = [path for path in docs if subject_text in path.name.lower()]
        return matches or docs

    def _extract_topics(self, content: str, subject: str) -> list[str]:
        lowered_content = content.lower()
        if "syllabus" in lowered_content and ("learning outcome" in lowered_content or "course design" in lowered_content):
            return [
                "Course design and syllabus construction",
                "Student concerns at the beginning of a course",
                "Required syllabus components",
                "Course goals, objectives, and expectations",
                "Intended learning outcomes",
                "Assessment, grading, and assignments",
                "Classroom conduct and academic policies",
                "How students should prepare and participate",
            ]
        topics: list[str] = []
        candidates: list[str] = []
        for raw in content.splitlines():
            line = raw.strip(" -#\t")
            if not line:
                continue
            if len(line) > 90:
                heading_prefix = re.split(r"\bIn pairs\b|\bResearch indicates\b|\bStatement\b", line, maxsplit=1)[0].strip(" -#\t")
                if 4 <= len(heading_prefix) <= 90:
                    candidates.append(heading_prefix)
                candidates.extend(part.strip(" -#\t") for part in re.split(r"[.?!:;]", line) if part.strip())
            else:
                candidates.append(line)

        for line in candidates:
            if not line or len(line) < 4 or len(line) > 90:
                continue
            lowered = line.lower()
            if line[:1] in {",", ".", ")", "("}:
                continue
            if any(skip in lowered for skip in (
                "http", "www", ".edu", "/", "references", "joss", "adapted from", "san francisco",
                "fink", "davis", "wiggins", "tools for teaching", "understanding by design",
                "expanded 2nd", "alexandria", "dee (",
            )):
                continue
            if lowered.startswith(("what ", "why ", "when ", "where ", "will ", "is ", "are ", "can ", "do ", "does ")):
                continue
            has_topic_word = any(word in lowered for word in (
                "chapter", "unit", "lesson", "module", "topic", "course", "syllabus",
                "requirement", "objective", "expectation", "policy", "assessment",
                "grading", "assignment", "classroom", "student",
            ))
            looks_like_heading = line.istitle() or (line[:1].isupper() and len(line.split()) <= 9)
            if has_topic_word or looks_like_heading:
                if line not in topics:
                    topics.append(line)
            if len(topics) >= 16:
                break
        if not topics and subject:
            topics.append(subject)
        return topics

    def _subject_from_docs(self, docs: list[Path]) -> str:
        if not docs:
            return "Uploaded syllabus"
        stem = docs[0].stem.replace("_", " ").replace("-", " ").strip()
        return " ".join(stem.split()) or "Uploaded syllabus"

    def _fallback_topics(self, subject: str) -> list[str]:
        subject = subject or "the uploaded subject"
        return [
            f"Introduction to {subject}",
            f"Core ideas in {subject}",
            f"Important terms in {subject}",
            f"Worked examples from {subject}",
            f"Common mistakes in {subject}",
            f"Quick recap of {subject}",
        ]

    def _segment_title(self, slot: int, topics: list[str]) -> str:
        if slot == 0:
            return "Introduction and foundations"
        if len(topics) == 1:
            return topics[0]
        return "Teach " + ", ".join(topics[:2])

    def _objective_for(self, slot: int, topics: list[str]) -> str:
        if slot == 0:
            return "Set context, define the main idea, and connect it to what students already know."
        if len(topics) == 1:
            return f"Help students understand {topics[0]} clearly enough to answer basic questions."
        return f"Help students connect {topics[0]} with {topics[1]} and apply the idea."

    def _activity_for(self, slot: int, topics: list[str]) -> str:
        if slot == 0:
            return "Greet students, introduce the topic, explain why it matters, and ask one warm-up question."
        if slot % 2 == 0:
            return "Explain with a simple example, ask two check questions, and correct misconceptions."
        return "Teach the key points, invite one doubt, and summarize before moving to the next part."

    def _teaching_points(self, topics: list[str], content: str) -> list[str]:
        points: list[str] = []
        content_lines = [line.strip(" -#\t") for line in content.splitlines() if 20 <= len(line.strip()) <= 160]
        for topic in topics:
            match = next((line for line in content_lines if topic.lower()[:24] in line.lower()), "")
            points.append(match or f"Explain {topic} in simple classroom language with one example.")
        return points[:4]

    def _check_questions(self, topics: list[str]) -> list[str]:
        first = topics[0] if topics else "this topic"
        second = topics[1] if len(topics) > 1 else first
        return [
            f"What is the main idea of {first}?",
            f"Can someone give one example related to {second}?",
        ]

    def _spoken_script(self, slot: int, topics: list[str], minutes: int) -> str:
        if slot == 0:
            first_topic = topics[0] if topics else "today's topic"
            return (
                f"Let us begin with {first_topic}. "
                f"For the next {minutes} minutes, I will explain the foundation first, then we will check your understanding."
            )
        joined = ", ".join(topics[:2]) if topics else "the next part"
        return (
            f"Now we will continue with {joined}. "
            "Listen for the key idea, then I will ask a short question to confirm everyone understood."
        )

    def _progress_view(self, session: dict[str, Any] | None) -> dict[str, Any] | None:
        if not session:
            return None
        schedule = session.get("schedule") or []
        current_index = int(session.get("current_index") or 0)
        current_item = schedule[current_index] if current_index < len(schedule) else None
        remaining = schedule[current_index:] if current_index < len(schedule) else []
        return {
            "subject": session.get("subject", "General"),
            "status": session.get("status", "paused"),
            "current_index": current_index,
            "current_item": current_item,
            "completed_minutes": int(session.get("completed_minutes") or 0),
            "remaining_minutes": sum(int(item.get("minutes") or 0) for item in remaining),
            "started_at": session.get("started_at"),
            "resumed_at": session.get("resumed_at"),
            "source_files": session.get("source_files") or [],
            "schedule_count": len(schedule),
        }

    def _load_progress(self) -> dict[str, Any]:
        if not self.progress_path.exists():
            return {}
        try:
            data = json.loads(self.progress_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_progress(self, data: dict[str, Any]) -> None:
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        self.progress_path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")

    def _key(self, subject: str) -> str:
        safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in subject or "General")
        return "_".join(part for part in safe.split("_") if part) or "general"
