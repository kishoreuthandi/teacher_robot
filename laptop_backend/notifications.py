import json
from datetime import datetime
from typing import Any

from .config import settings


class NotificationStore:
    def __init__(self) -> None:
        self.path = settings.data_dir / "notifications.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(
        self,
        kind: str,
        message: str,
        severity: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "id": f"{datetime.now().timestamp():.6f}",
            "time": datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            "severity": severity,
            "message": message,
            "metadata": metadata or {},
            "acknowledged": False,
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=True) + "\n")
        return record

    def latest(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return list(reversed(records))
