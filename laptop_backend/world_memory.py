import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings


SAFE_REJECT = ("weapon", "bomb", "poison", "attack", "harm", "kill", "steal", "theft")


class WorldMemory:
    def __init__(self) -> None:
        self.path = settings.data_dir / "world_memory.json"

    def teach(self, name: str, facts: str = "", source: str = "user", verified: bool = True) -> dict[str, Any]:
        name = self._clean_name(name)
        facts = " ".join(facts.split()).strip()
        if not name:
            raise ValueError("Object name is required.")
        if self._unsafe(name + " " + facts):
            raise ValueError("I will not store violent or unsafe object instructions.")
        data = self._load()
        key = self._key(name)
        existing = data.get(key, {})
        fact_list = existing.get("facts", [])
        if facts and facts not in fact_list:
            fact_list.append(facts)
        item = {
            **existing,
            "name": name,
            "facts": fact_list[:20],
            "hypotheses": existing.get("hypotheses", []),
            "source": source,
            "verified": bool(verified),
            "first_seen": existing.get("first_seen") or datetime.now().isoformat(timespec="seconds"),
            "last_seen": datetime.now().isoformat(timespec="seconds"),
            "times_seen": int(existing.get("times_seen") or 0) + 1,
        }
        data[key] = item
        self._save(data)
        return item

    def observe(self, labels: list[str]) -> list[dict[str, Any]]:
        updated = []
        for label in sorted(set(labels)):
            if not label or label == "person":
                continue
            try:
                item = self.teach(label, self._default_fact(label), "camera", verified=False)
                self.imagine_from_observation(label)
                updated.append(item)
            except ValueError:
                continue
        return updated

    def imagine_from_observation(self, label: str, context: str = "classroom") -> dict[str, Any] | None:
        label = self._clean_name(label)
        if not label or self._unsafe(label):
            return None
        data = self._load()
        key = self._key(label)
        existing = data.get(key, {})
        hypotheses = existing.get("hypotheses", [])
        guess = f"Possible use in this {context}: {label} may be part of classroom activity or room setup. Verify before teaching it as fact."
        if guess not in hypotheses:
            hypotheses.append(guess)
        item = {
            **existing,
            "name": label,
            "facts": existing.get("facts", []),
            "hypotheses": hypotheses[:20],
            "source": existing.get("source", "camera"),
            "verified": bool(existing.get("verified", False)),
            "first_seen": existing.get("first_seen") or datetime.now().isoformat(timespec="seconds"),
            "last_seen": datetime.now().isoformat(timespec="seconds"),
            "times_seen": int(existing.get("times_seen") or 0) + 1,
        }
        data[key] = item
        self._save(data)
        return item

    def observe_human_behavior(self, behavior: str, student_name: str = "", note: str = "") -> dict[str, Any]:
        data = self._load()
        patterns = data.setdefault("_human_behavior_patterns", {"items": []})
        item = {
            "behavior": behavior,
            "student_name": student_name or "Unknown student",
            "note": note,
            "time": datetime.now().isoformat(timespec="seconds"),
        }
        existing = patterns.get("items", [])
        existing.append(item)
        patterns["items"] = existing[-500:]
        self._save(data)
        return item

    def search(self, query: str = "", limit: int = 50) -> list[dict[str, Any]]:
        items = [item for key, item in self._load().items() if not key.startswith("_")]
        if query:
            terms = [term.lower() for term in re.findall(r"[a-zA-Z0-9]+", query) if len(term) > 2]
            scored = []
            for item in items:
                haystack = json.dumps(item, ensure_ascii=True).lower()
                score = sum(1 for term in terms if term in haystack)
                if score:
                    scored.append((score, item))
            scored.sort(key=lambda pair: (pair[0], pair[1].get("last_seen", "")), reverse=True)
            items = [item for _, item in scored]
        else:
            items.sort(key=lambda item: item.get("last_seen", ""), reverse=True)
        return items[: max(1, min(limit, 500))]

    def summary(self) -> dict[str, Any]:
        items = self.search(limit=500)
        patterns = self._load().get("_human_behavior_patterns", {}).get("items", [])
        return {
            "total": len(items),
            "recent": items[:12],
            "human_patterns": patterns[-20:],
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def context_for(self, query: str) -> str:
        items = self.search(query, limit=5)
        patterns = self._load().get("_human_behavior_patterns", {}).get("items", [])[-8:]
        lines: list[str] = []
        for item in items:
            facts = "; ".join(item.get("facts", [])[:3])
            hypotheses = "; ".join(item.get("hypotheses", [])[:2])
            if facts:
                lines.append(f"- Object memory {item.get('name')}: {facts}")
            if hypotheses:
                lines.append(f"- Hypothesis about {item.get('name')}: {hypotheses}")
        for pattern in patterns:
            lines.append(
                f"- Human behavior observation: {pattern.get('student_name')} {pattern.get('behavior')} "
                f"at {pattern.get('time')}. {pattern.get('note')}"
            )
        return "\n".join(lines) if lines else "No relevant world observations yet."

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

    def _clean_name(self, name: str) -> str:
        return " ".join(name.strip().split())[:80]

    def _key(self, name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

    def _unsafe(self, text: str) -> bool:
        lowered = text.lower()
        return any(word in lowered for word in SAFE_REJECT)

    def _default_fact(self, label: str) -> str:
        defaults = {
            "orange": "Orange is a fruit and is commonly known for vitamin C.",
            "apple": "Apple is a fruit and is commonly eaten as a healthy snack.",
            "banana": "Banana is a fruit and is commonly known for potassium.",
            "chair": "Chair is classroom furniture used for sitting.",
            "table": "Table is furniture used for writing, reading, or keeping materials.",
            "book": "Book is a learning material with written content.",
            "cell phone": "Mobile phone use is usually not allowed during class unless a teacher permits it.",
        }
        return defaults.get(label.lower(), f"{label} was observed in the classroom environment.")
