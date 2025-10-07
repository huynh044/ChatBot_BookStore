# app/services/rag.py
from __future__ import annotations

from typing import List, Optional, Dict
import os, httpx
import chromadb
from chromadb import PersistentClient
from chromadb.api.types import EmbeddingFunction
from chromadb.config import Settings
from sqlalchemy import text, bindparam

from ..config import settings
from ..db import (
    db_conn,
    fetch_books_fulltext,
    fetch_books_keywords,
    fetch_books_by_category,
)
from .llm import parse_catalog_query

# rapidfuzz để rerank theo từ khóa; nếu chưa cài vẫn chạy được
try:
    from rapidfuzz import fuzz
except Exception:  # fallback mềm
    class _F:
        @staticmethod
        def token_set_ratio(a, b): return 0.0
    fuzz = _F()


# ========= Helpers lấy config linh hoạt =========
def _get(name: str, default=None):
    """Lấy từ settings (nhiều biến tương đương) rồi tới ENV, cuối cùng default."""
    aliases = {
        "embedding_model": ["embedding_model", "embed_model", "EMBEDDING_MODEL", "EMBED_MODEL"],
        "ollama_base_url": ["ollama_base_url", "OLLAMA_BASE_URL"],
        "chroma_dir": ["chroma_dir", "CHROMA_DIR"],
        "chroma_collection": ["chroma_collection", "CHROMA_COLLECTION"],
    }
    for key in aliases.get(name, [name]):
        # settings.attr
        if hasattr(settings, key):
            return getattr(settings, key)
        # ENV
        if key.upper() in os.environ:
            return os.environ[key.upper()]
    return default


# =================== Embedding qua Ollama ===================

class OllamaEmbeddingFn(EmbeddingFunction):

    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        self.http = httpx.Client(timeout=60.0)

    def name(self) -> str:
        return f"ollama:{self.model}"

    def _embed_one(self, text: str) -> List[float]:
        r = self.http.post(f"{self.base_url}/api/embed",
                           json={"model": self.model, "input": text})
        if r.status_code >= 400:  # fallback legacy
            r = self.http.post(f"{self.base_url}/api/embeddings",
                               json={"model": self.model, "prompt": text})
        r.raise_for_status()
        data = r.json()
        if "embeddings" in data:
            return data["embeddings"][0]
        return data["embedding"]

    def embed_documents(self, input=None, documents=None, **_):
        texts = list(documents if documents is not None else (input or []))
        if not texts:
            return []
        return [self._embed_one(t) for t in texts]

    def embed_query(self, input=None, query=None, **_):
        text = input if input is not None else query
        if text is None:
            return []
        return [self._embed_one(text)]

    def __call__(self, input):
        if isinstance(input, str):
            return self.embed_query(input=input)
        return self.embed_documents(input=list(input))


# =================== Hybrid Retriever ===================

class HybridRetriever:
    def __init__(self):
        # ---- config an toàn với default ----
        emb_model = _get("embedding_model", "bge-m3")  # tốt cho tiếng Việt; thay bằng nomic-embed-text nếu bạn thích
        base_url = _get("ollama_base_url", "http://localhost:11434")
        chroma_dir = _get("chroma_dir", None)
        collection_name = _get("chroma_collection", "books_vi")

        # ---- tạo client Chroma ----
        if chroma_dir:
            self.client = PersistentClient(path=chroma_dir, settings=Settings(allow_reset=False))
        else:
            self.client = chromadb.Client(Settings(allow_reset=False))

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=OllamaEmbeddingFn(model=emb_model, base_url=base_url),
        )

    # hợp nhất điểm: lexical (title/author/category) + vector
    def _score(self, q: str, rec: Dict, vec_score: Optional[float]) -> float:
        text = f"{rec['title']} {rec['author']} {rec.get('category','')}"
        s_ratio = (fuzz.token_set_ratio(q, text) or 0.0) / 100.0  # 0..1
        title_boost = 0.20 if any(w.lower() in rec['title'].lower() for w in q.split()) else 0.0
        cat_boost = 0.15 if rec.get('category') and any(w.lower() in rec['category'].lower() for w in q.split()) else 0.0
        v = float(vec_score or 0.0)
        return 0.55 * s_ratio + 0.35 * v + title_boost + cat_boost

    def upsert_book(self, b: Dict):
        doc = f"{b['title']} — {b['author']}. The loai: {b.get('category','')}"
        self.collection.upsert(
            ids=[str(b["book_id"])],
            documents=[doc],
            metadatas=[{"book_id": b["book_id"]}],
        )

    def delete_book(self, book_id: int):
        self.collection.delete(ids=[str(book_id)])

    def search(self, user_query: str, limit: int = 5) -> list[Dict]:
        pq = parse_catalog_query(user_query)
        q = (pq["query"] or user_query).strip()
        cat = pq["category"]

        # 1) Ứng viên từ DB
        with db_conn() as conn:
            db_cands = []
            if cat:
                db_cands += fetch_books_by_category(conn, cat, limit=limit * 2)
            if q and len(q) >= 2:
                try:
                    db_cands += fetch_books_fulltext(conn, q, limit=limit * 2)
                except Exception:
                    db_cands += fetch_books_keywords(conn, q, limit=limit * 2)

        # 2) Ứng viên vector từ Chroma (có thể rỗng nếu chưa index)
        vec_scores: Dict[int, float] = {}
        try:
            res = self.collection.query(query_texts=[user_query], n_results=min(limit * 2, 10))
            ids = (res or {}).get("ids", [[]])[0] or []
            dists = (res or {}).get("distances", [[]])[0] or []
            for _id, dist in zip(ids, dists):
                try:
                    bid = int(_id)
                    dist = float(dist)
                except Exception:
                    continue
                # chuyển khoảng cách -> điểm
                vec_scores[bid] = 1.0 / (1.0 + dist)
        except Exception:
            pass

        # 3) Hợp nhất theo book_id
        by_id: Dict[int, Dict] = {r["book_id"]: r for r in db_cands}
        missing = [bid for bid in vec_scores.keys() if bid not in by_id]
        if missing:
            with db_conn() as conn:
                stmt = text("""
                    SELECT book_id, title, author, price, stock, category
                    FROM Books
                    WHERE book_id IN :ids
                """).bindparams(bindparam("ids", expanding=True))
                rows = conn.execute(stmt, {"ids": missing}).mappings().all()
                for row in rows:
                    by_id[row["book_id"]] = dict(row)

        # 4) Rerank
        scored = []
        base_q = q or user_query
        for bid, rec in by_id.items():
            score = self._score(base_q, rec, vec_scores.get(bid))
            scored.append((score, rec))
        scored.sort(key=lambda x: x[0], reverse=True)

        return [rec for _, rec in scored[:limit]]


# singleton
retriever = HybridRetriever()
