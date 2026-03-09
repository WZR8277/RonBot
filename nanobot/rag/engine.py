"""RAG engine: hybrid (vector + BM25) retrieval with RRF and reliability-aware ranking."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from nanobot.rag.chunking import chunk_knowledge_base

ReliabilityFilter = Literal["reliable_only", "prefer_reliable", "all"]
RRF_K = 60  # RRF constant (typical default)


def _get_embedding_fn(api_key: str = "", model: str = ""):
    """Return an embedding function: OpenAI if api_key+model, else sentence-transformers."""
    if api_key and model:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)

            def _embed(texts: list[str]) -> list[list[float]]:
                r = client.embeddings.create(input=texts, model=model)
                return [e.embedding for e in r.data]
            return _embed
        except Exception as e:
            logger.warning("RAG: OpenAI embedding failed, falling back to local: {}", e)

    try:
        from sentence_transformers import SentenceTransformer
        # Lightweight, good for retrieval
        m = os.environ.get("NANOBOT_RAG_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
        _model = SentenceTransformer(m)

        def _embed(texts: list[str]) -> list[list[float]]:
            return _model.encode(texts, convert_to_numpy=True).tolist()
        return _embed
    except ImportError:
        return None


class RAGEngine:
    """
    Hybrid RAG: vector (Chroma) + BM25, merged with RRF.
    Reliability-aware: filter or boost by reliable/uncertain.
    """

    def __init__(
        self,
        workspace: Path,
        knowledge_base_subdir: str = "knowledge_base",
        embedding_api_key: str = "",
        embedding_model: str = "",
        persist_dir: Path | None = None,
    ):
        self.workspace = Path(workspace)
        self.kb_path = self.workspace / knowledge_base_subdir
        self.persist_dir = Path(persist_dir) if persist_dir else self.workspace / "memory" / "rag"
        self._embedding_api_key = embedding_api_key or os.environ.get("OPENAI_API_KEY", "")
        self._embedding_model = embedding_model or "text-embedding-3-small"
        self._embed_fn = None
        self._chroma = None
        self._bm25 = None
        self._chunks: list[dict[str, Any]] = []
        self._chunk_ids: list[str] = []  # id per chunk, same order as _chunks
        self._id_to_chunk: dict[str, dict] = {}

    def _ensure_embedding(self) -> bool:
        if self._embed_fn is not None:
            return True
        self._embed_fn = _get_embedding_fn(self._embedding_api_key, self._embedding_model)
        return self._embed_fn is not None

    def _chunk_id(self, i: int, chunk: dict) -> str:
        h = hashlib.sha256((chunk["text"] + chunk["source"]).encode()).hexdigest()[:16]
        return f"{i}_{h}"

    def build_index(self) -> str:
        """Build or rebuild vector + BM25 index from knowledge_base. Returns status message."""
        try:
            import chromadb
            from chromadb.config import Settings
            from rank_bm25 import BM25Okapi
        except ImportError as e:
            return (
                "Error: RAG optional deps not installed. Run: pip install nanobot-ai[rag]"
            ) + f" ({e})"

        if not self._ensure_embedding():
            return (
                "Error: No embedding available. Set tools.rag.apiKey and embeddingModel in config, "
                "or install sentence-transformers (pip install nanobot-ai[rag])."
            )

        chunks = chunk_knowledge_base(self.kb_path)
        if not chunks:
            return "No .md files found under knowledge_base; index empty."

        self._chunks = chunks
        self._chunk_ids = [self._chunk_id(i, c) for i, c in enumerate(chunks)]
        self._id_to_chunk = {self._chunk_ids[i]: c for i, c in enumerate(chunks)}
        texts = [c["text"] for c in chunks]

        # BM25
        tokenized = [t.split() for t in texts]
        self._bm25 = BM25Okapi(tokenized)

        # Chroma: persist under workspace/memory/rag
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._chroma = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        col_name = "kb"
        try:
            self._chroma.delete_collection(col_name)
        except Exception:
            pass
        collection = self._chroma.create_collection(name=col_name, metadata={"description": "knowledge_base"})

        # Embed in batches (sync embed_fn); store documents for reload after restart
        batch_size = 32
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_chunks = chunks[i : i + batch_size]
            emb = self._embed_fn(batch)
            ids_batch = [self._chunk_id(i + j, c) for j, c in enumerate(batch_chunks)]
            metadatas_batch = [
                {"source": c["source"], "date": c.get("date", ""), "reliability": c.get("reliability", "reliable")}
                for c in batch_chunks
            ]
            collection.add(ids=ids_batch, embeddings=emb, documents=batch, metadatas=metadatas_batch)

        logger.info("RAG index built: {} chunks from {}", len(chunks), self.kb_path)
        return f"Indexed {len(chunks)} chunks from {self.kb_path}."

    def _load_from_chroma(self) -> bool:
        """Load chunks and BM25 from persisted Chroma (after restart). Returns True if loaded."""
        try:
            import chromadb
            from rank_bm25 import BM25Okapi
        except ImportError:
            return False
        if not self.persist_dir.exists():
            return False
        try:
            from chromadb.config import Settings
            client = chromadb.PersistentClient(path=str(self.persist_dir), settings=Settings(anonymized_telemetry=False))
            coll = client.get_collection("kb")
        except Exception:
            return False
        data = coll.get(include=["documents", "metadatas"])
        if not data or not data["ids"]:
            return False
        self._chunks = []
        self._chunk_ids = list(data["ids"])
        for i, id_ in enumerate(data["ids"]):
            doc = (data["documents"] or [None])[i] or ""
            meta = (data["metadatas"] or [{}])[i] or {}
            ch = {
                "text": doc,
                "source": meta.get("source", ""),
                "date": meta.get("date", ""),
                "reliability": meta.get("reliability", "reliable"),
            }
            self._chunks.append(ch)
            self._id_to_chunk[id_] = ch
        tokenized = [c["text"].split() for c in self._chunks]
        self._bm25 = BM25Okapi(tokenized)
        self._chroma = client
        logger.info("RAG loaded from Chroma: {} chunks", len(self._chunks))
        return True

    def _vector_search(self, query_text: str, top_k: int) -> list[tuple[str, float]]:
        if not self._chroma or not self._ensure_embedding():
            return []
        q_emb = self._embed_fn([query_text])[0]
        coll = self._chroma.get_collection("kb")
        n = min(top_k * 2, len(self._id_to_chunk))
        results = coll.query(query_embeddings=[q_emb], n_results=n)
        ids = results["ids"][0]
        return list(zip(ids, [1.0] * len(ids)))

    def _bm25_search(self, query_text: str, top_k: int) -> list[tuple[str, float]]:
        if not self._bm25:
            return []
        tokenized_q = query_text.split()
        scores = self._bm25.get_scores(tokenized_q)
        order = scores.argsort()[::-1]
        out = []
        for idx in order[: top_k * 2]:
            if scores[idx] <= 0:
                break
            id_ = self._chunk_ids[idx] if idx < len(self._chunk_ids) else self._chunk_id(idx, self._chunks[idx])
            out.append((id_, float(scores[idx])))
        return out

    def query(
        self,
        query_text: str,
        top_k: int = 5,
        reliability: ReliabilityFilter = "prefer_reliable",
    ) -> list[dict[str, Any]]:
        """
        Hybrid search: vector + BM25, RRF merge, then apply reliability filter/weight.
        Returns list of { text, source, date, reliability }.
        """
        if not self._chunks and (self._chroma is None or self._bm25 is None):
            if self._chroma is None and self.persist_dir.exists():
                self._load_from_chroma()
            if not self._chunks:
                return []

        try:
            vec_list = self._vector_search(query_text, top_k)
        except Exception as e:
            logger.debug("RAG vector search failed: {}", e)
            vec_list = []
        try:
            bm25_list = self._bm25_search(query_text, top_k)
        except Exception as e:
            logger.debug("RAG BM25 search failed: {}", e)
            bm25_list = []

        # RRF merge: score(d) = sum 1/(k+rank)
        rrf_scores: dict[str, float] = {}
        for rank, (id_, _) in enumerate(vec_list, start=1):
            rrf_scores[id_] = rrf_scores.get(id_, 0) + 1.0 / (RRF_K + rank)
        for rank, (id_, _) in enumerate(bm25_list, start=1):
            rrf_scores[id_] = rrf_scores.get(id_, 0) + 1.0 / (RRF_K + rank)

        # Reliability weighting: boost reliable when prefer_reliable
        if reliability == "prefer_reliable" and rrf_scores:
            for id_ in list(rrf_scores.keys()):
                ch = self._id_to_chunk.get(id_)
                if ch and ch.get("reliability") == "reliable":
                    rrf_scores[id_] *= 1.5
        elif reliability == "reliable_only":
            rrf_scores = {id_: s for id_, s in rrf_scores.items()
                          if self._id_to_chunk.get(id_, {}).get("reliability") == "reliable"}

        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: -rrf_scores[x])[: top_k * 2]
        seen = set()
        out: list[dict[str, Any]] = []
        for id_ in sorted_ids:
            if id_ in seen:
                continue
            seen.add(id_)
            ch = self._id_to_chunk.get(id_)
            if not ch:
                continue
            out.append({
                "text": ch["text"],
                "source": ch.get("source", ""),
                "date": ch.get("date", ""),
                "reliability": ch.get("reliability", "reliable"),
            })
            if len(out) >= top_k:
                break
        return out
