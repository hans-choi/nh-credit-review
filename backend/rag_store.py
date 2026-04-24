"""Regulation corpus RAG store — in-memory cosine-similarity search over Upstage embeddings.

Persisted as JSON under DATA_DIR:
    regulations.json — { reg_id: { id, filename, uploaded_at, chunks: [{id, text, page, emb}] } }
"""

import json
import math
import os
import re
import uuid
from datetime import datetime
from typing import Optional

from config import DATA_DIR


# ─── Chunking config ──────────────────────────────────────────────
# Regulation documents have a hierarchical structure (장/조/항/호).
# Split on semantic boundaries; keep each chunk small enough for useful retrieval.
CHUNK_MAX_CHARS = 500
CHUNK_OVERLAP   = 60


def chunk_text(full_text: str) -> list[dict]:
    """Split text into overlapping chunks, preferring clause/section boundaries.

    Returns list of {text, page_estimate}. Page estimation is best-effort based on
    form-feed characters or page markers.
    """
    if not full_text:
        return []

    # Normalize whitespace but preserve line breaks
    text = full_text.replace("\r\n", "\n").replace("\r", "\n")

    # First split on section boundaries (제N장/제N조/제N항)
    section_re = re.compile(r"(?=제\s*\d+\s*[장조항호])")
    raw_sections = section_re.split(text)
    # Filter empty and too-tiny fragments
    sections = [s.strip() for s in raw_sections if s.strip()]

    chunks: list[dict] = []
    for sec in sections:
        if len(sec) <= CHUNK_MAX_CHARS:
            chunks.append({"text": sec, "page": _estimate_page(sec)})
            continue
        # Further split long sections on paragraphs/newlines
        cursor = 0
        while cursor < len(sec):
            end = min(cursor + CHUNK_MAX_CHARS, len(sec))
            # Try to break on newline near the boundary
            if end < len(sec):
                break_at = sec.rfind("\n", cursor, end)
                if break_at > cursor + 200:
                    end = break_at
            piece = sec[cursor:end].strip()
            if piece:
                chunks.append({"text": piece, "page": _estimate_page(piece)})
            cursor = end - CHUNK_OVERLAP if end < len(sec) else end
            if cursor < 0:
                cursor = end
    return chunks


def _estimate_page(chunk_text: str) -> int:
    """Try to extract a 'page N' hint; default to 1."""
    m = re.search(r"page\s*[:=]?\s*(\d+)", chunk_text, re.I)
    if m:
        return int(m.group(1))
    return 1


# ─── Cosine similarity ────────────────────────────────────────────
def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (na * nb)


# ─── Store ────────────────────────────────────────────────────────
class RegulationStore:
    """In-memory RAG store with JSON persistence."""

    def __init__(self):
        self.regulations: dict[str, dict] = {}
        self._path = os.path.join(DATA_DIR, "regulations.json")
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self.regulations = json.load(f)
            except Exception as e:
                print(f"[RAG] load failed: {e}")

    def _save(self):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self.regulations, f, ensure_ascii=False, indent=2)

    def list_regulations(self) -> list[dict]:
        out = []
        for reg in self.regulations.values():
            out.append({
                "id": reg["id"],
                "filename": reg["filename"],
                "uploaded_at": reg["uploaded_at"],
                "num_chunks": len(reg.get("chunks", [])),
                "preview": (reg.get("full_text") or "")[:200],
            })
        # Most recent first
        out.sort(key=lambda r: r["uploaded_at"], reverse=True)
        return out

    def get_regulation(self, reg_id: str) -> Optional[dict]:
        return self.regulations.get(reg_id)

    def delete_regulation(self, reg_id: str) -> bool:
        if reg_id not in self.regulations:
            return False
        del self.regulations[reg_id]
        self._save()
        return True

    async def add_regulation(self, filename: str, full_text: str, upstage_client) -> dict:
        """Chunk + embed + store a new regulation document.
        Accepts pre-parsed full_text (caller can use Document Parse or raw upload)."""
        reg_id = "reg-" + uuid.uuid4().hex[:10]
        chunks_raw = chunk_text(full_text)
        if not chunks_raw:
            chunks_raw = [{"text": full_text[:CHUNK_MAX_CHARS], "page": 1}]

        # Batch embed
        texts = [c["text"] for c in chunks_raw]
        # Upstage embedding-passage supports batch
        embeddings = await upstage_client.embed(texts, model="embedding-passage",
                                                detail=f"regulation: {filename}")
        chunks = []
        for i, (c, emb) in enumerate(zip(chunks_raw, embeddings)):
            chunks.append({
                "id": f"{reg_id}-c{i}",
                "text": c["text"],
                "page": c.get("page", 1),
                "emb": emb,
            })

        reg = {
            "id": reg_id,
            "filename": filename,
            "full_text": full_text,
            "uploaded_at": datetime.now().isoformat(),
            "chunks": chunks,
        }
        self.regulations[reg_id] = reg
        self._save()
        return {
            "id": reg_id,
            "filename": filename,
            "num_chunks": len(chunks),
        }

    async def search(self, query: str, top_k: int = 5, upstage_client=None) -> list[dict]:
        """Return top-k matching chunks across all regulations, ranked by cosine similarity."""
        if not self.regulations or not query.strip():
            return []
        q_emb = (await upstage_client.embed(query, model="embedding-query",
                                            detail=f"query: {query[:50]}"))[0]
        candidates = []
        for reg in self.regulations.values():
            for c in reg["chunks"]:
                s = cosine(q_emb, c["emb"])
                candidates.append({
                    "reg_id": reg["id"],
                    "reg_filename": reg["filename"],
                    "chunk_id": c["id"],
                    "page": c.get("page", 1),
                    "text": c["text"],
                    "score": s,
                })
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:top_k]


store_rag = RegulationStore()
