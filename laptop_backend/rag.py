import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings
from .syllabus import SUPPORTED_SYLLABUS_SUFFIXES, read_syllabus_file


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "is", "it",
    "of", "on", "or", "that", "the", "this", "to", "was", "were", "will", "with", "you", "your",
}


@dataclass
class RagChunk:
    id: str
    source: str
    subject: str
    index: int
    text: str
    tokens: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "subject": self.subject,
            "index": self.index,
            "text": self.text,
            "tokens": self.tokens,
        }


class RagIndex:
    def __init__(self) -> None:
        self.index_path = settings.data_dir / "rag_index.json"

    def rebuild(self) -> dict[str, Any]:
        chunks: list[RagChunk] = []
        for path in sorted(Path(settings.syllabus_dir).glob("*")):
            if path.suffix.lower() not in SUPPORTED_SYLLABUS_SUFFIXES:
                continue
            text = read_syllabus_file(path)
            if not text.strip():
                continue
            subject = path.stem.replace("_", " ").replace("-", " ").strip() or "General"
            for index, chunk_text in enumerate(self._chunk_text(text)):
                chunk_id = f"{path.stem}-{index}"
                chunks.append(RagChunk(
                    id=chunk_id,
                    source=path.name,
                    subject=subject,
                    index=index,
                    text=chunk_text,
                    tokens=self._tokens(chunk_text),
                ))
        data = {
            "built_at": datetime.now().isoformat(timespec="seconds"),
            "chunk_count": len(chunks),
            "chunks": [chunk.as_dict() for chunk in chunks],
        }
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
        return {"ok": True, "built_at": data["built_at"], "chunk_count": len(chunks), "sources": sorted({c.source for c in chunks})}

    def status(self) -> dict[str, Any]:
        data = self._load()
        return {
            "built": bool(data),
            "built_at": data.get("built_at") if data else None,
            "chunk_count": data.get("chunk_count", 0) if data else 0,
            "sources": sorted({chunk.get("source", "") for chunk in data.get("chunks", [])}) if data else [],
        }

    def search(self, query: str, subject: str = "", limit: int = 5) -> list[dict[str, Any]]:
        data = self._load()
        if not data:
            self.rebuild()
            data = self._load()
        query_tokens = self._tokens(query + " " + subject)
        if not query_tokens or not data:
            return []
        query_counts = Counter(query_tokens)
        rows: list[dict[str, Any]] = []
        for chunk in data.get("chunks", []):
            if subject and subject.lower() not in (chunk.get("subject", "") + " " + chunk.get("source", "")).lower():
                pass
            score = self._score(query_counts, chunk.get("tokens", []))
            if subject and subject.lower() in (chunk.get("subject", "") + " " + chunk.get("source", "")).lower():
                score += 0.25
            if score <= 0:
                continue
            rows.append({
                "id": chunk.get("id"),
                "source": chunk.get("source"),
                "subject": chunk.get("subject"),
                "index": chunk.get("index"),
                "score": round(score, 4),
                "text": chunk.get("text", ""),
            })
        rows.sort(key=lambda row: row["score"], reverse=True)
        return rows[: max(1, min(limit, 12))]

    def context_for(self, query: str, subject: str = "", limit: int = 5) -> str:
        rows = self.search(query, subject, limit)
        if not rows:
            return "No relevant uploaded course material found."
        parts = []
        for row in rows:
            parts.append(f"[{row['source']} chunk {row['index']} score {row['score']}]\n{row['text']}")
        return "\n\n".join(parts)

    def quick_answer(self, query: str, subject: str = "") -> dict[str, Any]:
        rows = self.search(query, subject, limit=3)
        if not rows:
            return {
                "answered": False,
                "confidence": 0.0,
                "answer": "This is not covered in the uploaded course material.",
                "sources": [],
            }
        best = rows[0]
        if float(best.get("score") or 0) < 1.2:
            return {
                "answered": False,
                "confidence": round(float(best.get("score") or 0), 3),
                "answer": "This is not clearly covered in the uploaded course material.",
                "sources": [{"source": best.get("source"), "chunk": best.get("index"), "score": best.get("score")}],
            }
        sentences = self._sentences(best.get("text", ""))
        query_counts = Counter(self._tokens(query))
        ranked: list[tuple[float, str]] = []
        for sentence in sentences:
            score = self._score(query_counts, self._tokens(sentence))
            if score > 0:
                ranked.append((score, sentence))
        ranked.sort(key=lambda item: item[0], reverse=True)
        selected = [sentence for _, sentence in ranked[:2]]
        if not selected:
            selected = sentences[:2]
        answer = " ".join(selected).strip()
        if len(answer) > 360:
            answer = answer[:357].rsplit(" ", 1)[0].rstrip(",;:") + "..."
        return {
            "answered": True,
            "confidence": round(float(best.get("score") or 0), 3),
            "answer": answer,
            "sources": [{"source": row.get("source"), "chunk": row.get("index"), "score": row.get("score")} for row in rows],
        }

    def _load(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _chunk_text(self, text: str, target_chars: int = 1200, overlap_chars: int = 180) -> list[str]:
        paragraphs = [line.strip() for line in re.split(r"\n\s*\n|\r\n\s*\r\n", text) if line.strip()]
        if not paragraphs:
            paragraphs = [text]
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            if len(current) + len(paragraph) + 2 <= target_chars:
                current = (current + "\n\n" + paragraph).strip()
                continue
            if current:
                chunks.append(current)
            if len(paragraph) > target_chars:
                start = 0
                while start < len(paragraph):
                    chunks.append(paragraph[start:start + target_chars].strip())
                    start += max(1, target_chars - overlap_chars)
                current = ""
            else:
                current = paragraph
        if current:
            chunks.append(current)
        return chunks[:400]

    def _tokens(self, text: str) -> list[str]:
        return [
            token for token in re.findall(r"[a-zA-Z0-9]+", text.lower())
            if len(token) > 2 and token not in STOPWORDS
        ]

    def _sentences(self, text: str) -> list[str]:
        lines = [line.strip(" -") for line in text.splitlines() if 20 <= len(line.strip()) <= 260]
        compact = " ".join(text.split())
        parts = re.split(r"(?<=[.!?])\s+", compact)
        sentences = [part.strip(" -") for part in parts if 20 <= len(part.strip()) <= 260]
        seen: set[str] = set()
        ordered: list[str] = []
        for item in lines + sentences:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(item)
        return ordered[:16]

    def _score(self, query_counts: Counter, chunk_tokens: list[str]) -> float:
        chunk_counts = Counter(chunk_tokens)
        if not chunk_counts:
            return 0.0
        overlap = sum(min(count, chunk_counts.get(token, 0)) for token, count in query_counts.items())
        unique_overlap = sum(1 for token in query_counts if token in chunk_counts)
        length_penalty = math.log(max(len(chunk_tokens), 10), 10)
        return (overlap + unique_overlap * 1.5) / max(length_penalty, 1.0)
