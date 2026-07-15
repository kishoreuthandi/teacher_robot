import json
from datetime import datetime
from typing import Any

from .config import settings

try:
    import chromadb
except ImportError:  # pragma: no cover - optional local dependency
    chromadb = None


class MemoryStore:
    def __init__(self) -> None:
        self.path = settings.data_dir / "zoro_memory.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.collection = None
        if chromadb is not None:
            try:
                client = chromadb.PersistentClient(path=str(settings.data_dir / "chroma"))
                self.collection = client.get_or_create_collection("zoro_memory")
            except Exception as exc:
                print(f"Warning: ChromaDB memory disabled: {exc}")

    def add(self, kind: str, text: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        record = {
            "id": f"{datetime.now().timestamp():.6f}",
            "time": datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            "text": text,
            "metadata": metadata or {},
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=True) + "\n")
        if self.collection is not None:
            try:
                self.collection.add(
                    ids=[record["id"]],
                    documents=[text],
                    metadatas=[{"kind": kind, **record["metadata"]}],
                )
            except Exception as exc:
                print(f"Warning: Could not write vector memory: {exc}")
        return record

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if self.collection is not None:
            try:
                result = self.collection.query(query_texts=[query], n_results=limit)
                docs = result.get("documents", [[]])[0]
                metas = result.get("metadatas", [[]])[0]
                ids = result.get("ids", [[]])[0]
                return [
                    {"id": ids[index], "text": doc, "metadata": metas[index] if index < len(metas) else {}}
                    for index, doc in enumerate(docs)
                ]
            except Exception as exc:
                print(f"Warning: Vector memory search failed: {exc}")

        records = self.latest(200)
        scored = []
        terms = {term.lower() for term in query.split() if len(term) > 2}
        for record in records:
            haystack = record["text"].lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in scored[:limit]]

    def latest(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return list(reversed(records))

    def context_for(self, query: str) -> str:
        memories = self.search(query, limit=5)
        if not memories:
            return "No relevant long-term memories found."
        return "\n".join(f"- {item.get('time', '')} {item.get('text', '')}" for item in memories)
