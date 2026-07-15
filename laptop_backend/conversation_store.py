import json
from datetime import datetime

from .config import settings


class ConversationStore:
    def __init__(self) -> None:
        self.path = settings.data_dir / "conversations.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, question: str, answer: str, student_name: str | None = None) -> dict:
        record = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "student_name": student_name,
            "question": question,
            "answer": answer,
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=True) + "\n")
        return record

    def latest(self, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return list(reversed(records))

