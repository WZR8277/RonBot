"""RAG engine: hybrid (vector + BM25) retrieval with rank-normalized merge."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Literal, Optional

from loguru import logger

from nanobot.rag.chunking import chunk_knowledge_base

ReliabilityFilter = Literal["reliable_only", "prefer_reliable", "all"]
ChunkScope = Literal["all", "concrete_only"]
# 合并时 BM25 权重大于向量，两边按排名归一化后再加权
VEC_WEIGHT = 0.4
BM25_WEIGHT = 0.6
GENERAL_WEIGHT = 0.2
CONTAINMENT_SCORE = 1.0
CONTAINMENT_THRESHOLD = 0.2


def _normalize_bm25_token(w: str) -> str:
    """去掉首尾非字母数字字符，使 **autogen** 与 autogen 能匹配。"""
    if not w:
        return w
    start, end = 0, len(w)
    while start < end and not w[start].isalnum() and not ("\u4e00" <= w[start] <= "\u9fff"):
        start += 1
    while end > start and not w[end - 1].isalnum() and not ("\u4e00" <= w[end - 1] <= "\u9fff"):
        end -= 1
    return w[start:end].lower() if start < end else w.lower()


# 与 BM25 分词一致：顿号、逗号等作为词边界，避免 "CrewAI、AutoGen、ReAct" 被整段当成一个 token 导致 autogen 匹配不到
_BM25_SEP = frozenset(" \t\n\r\u3000\u3001\u002c\u060c\ufe50\ufe51\u00b7")  # 空格、顿号、逗号、间隔号等


def _tokenize_for_bm25(text: str) -> list[str]:
    """
    Tokenize for BM25: CJK 按字切分；非 CJK 按「连续非空白且非分隔符」为一词；
    顿号、逗号等视为分隔符，使 "CrewAI、AutoGen、ReAct" 拆成 autogen / react 等独立 token，提高 BM25 命中。
    对非 CJK 词做首尾去标点规范化，使 **AutoGen** 与 AutoGen 能匹配。
    """
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if "\u4e00" <= c <= "\u9fff":  # CJK 统一汉字
            tokens.append(c)
            i += 1
        elif c.isspace() or c in _BM25_SEP:
            i += 1
        else:
            start = i
            while i < n and not text[i].isspace() and text[i] not in _BM25_SEP and not ("\u4e00" <= text[i] <= "\u9fff"):
                i += 1
            word = text[start:i]
            if word:
                normalized = _normalize_bm25_token(word)
                if normalized:
                    tokens.append(normalized)
    return tokens
# 检索时 query 最大长度，过长会稀释 embedding 或触发模型截断，导致结果偏泛化
MAX_QUERY_CHARS = 150


def _bm25_containment_bonus(chunk_text: str, query: str) -> float:
    """
    给 BM25 侧用的「含查询/子句」加分：整句包含 +0.5，按词包含 +0.3，子串(4~10 字)包含 +0.2。
    归一化空白并统一全角％为半角%，避免漏判。与 BM25 token 打分一起构成「BM25 侧」最终得分。
    """
    if not query or len(query.strip()) < 2:
        return 0.0
    q = query.strip()
    t = " ".join(chunk_text.split())
    t = t.replace("\uFF05", "%")
    qn = " ".join(q.split()).replace("\uFF05", "%")
    if qn in t:
        return 0.5
    for w in q.split():
        if len(w) >= 2 and w.replace("\uFF05", "%") in t:
            return 0.3
    for start in range(0, max(1, len(q) - 3), 2):
        for length in (4, 6, 8, 10):
            if start + length <= len(q):
                sub = q[start : start + length]
                if sub in t:
                    return 0.2
    return 0.0


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
        self._index_embedding_model: str = ""  # set on load/build for mismatch check
        self._last_load_mtime: float = 0.0  # 用于 query 时判断索引是否被其他进程重建，需重载

    def _get_embedding_model_name(self) -> str:
        """Return a string identifying the embedding model (for index metadata / mismatch check)."""
        if self._embedding_api_key and self._embedding_model:
            return f"openai:{self._embedding_model}"
        return f"local:{os.environ.get('NANOBOT_RAG_EMBEDDING_MODEL', 'all-MiniLM-L6-v2')}"

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
                "Error: RAG optional deps not installed. Run: pip install 'nanobot-ai[rag]' "
                "or manually: pip install chromadb rank-bm25 sentence-transformers."
            ) + f" ({e})"

        if not self._ensure_embedding():
            return (
                "Error: No embedding available. Set tools.rag.apiKey and embeddingModel in config, "
                "or install sentence-transformers (pip install 'nanobot-ai[rag]')."
            )

        kb_path_resolved = self.kb_path.resolve()
        chunks = chunk_knowledge_base(self.kb_path)
        if not chunks:
            logger.warning("RAG build_index: no chunks from {!r} (dir exists: {})", kb_path_resolved, self.kb_path.exists())
            return (
                "Success: knowledge_base has no .md files yet (or directory missing). "
                "Index is empty — normal on first run."
            )
        sources = sorted({c.get("source", "") for c in chunks if c.get("source")})
        n_reliable = sum(1 for c in chunks if "【可靠】" in c.get("text", ""))
        logger.info(
            "RAG build_index: {} chunks from {!r}, sources={}, chunks_with_【可靠】={}",
            len(chunks),
            kb_path_resolved,
            sources,
            n_reliable,
        )

        self._chunks = chunks
        self._chunk_ids = [self._chunk_id(i, c) for i, c in enumerate(chunks)]
        self._id_to_chunk = {self._chunk_ids[i]: c for i, c in enumerate(chunks)}
        texts = [c["text"] for c in chunks]

        # BM25（中文友好：CJK 按字、其余按词，避免中文 query 时 bm25_hits=0）
        tokenized = [_tokenize_for_bm25(t) for t in texts]
        self._bm25 = BM25Okapi(tokenized)

        # Chroma: persist under workspace/memory/rag
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._chroma = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        # Chroma requires collection name 3-63 chars, alphanumeric/underscore
        col_name = "knowledge_base"
        try:
            self._chroma.delete_collection(col_name)
        except Exception:
            pass
        emb_meta = {"description": "knowledge_base", "embedding_model": self._get_embedding_model_name()}
        collection = self._chroma.create_collection(name=col_name, metadata=emb_meta)

        # Embed in batches (sync embed_fn); store documents for reload after restart
        batch_size = 32
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_chunks = chunks[i : i + batch_size]
            emb = self._embed_fn(batch)
            ids_batch = [self._chunk_id(i + j, c) for j, c in enumerate(batch_chunks)]
            metadatas_batch = [
                {
                    "source": c["source"],
                    "date": c.get("date", ""),
                    "reliability": c.get("reliability", "reliable"),
                    "chunk_type": c.get("chunk_type", "general"),
                }
                for c in batch_chunks
            ]
            collection.add(ids=ids_batch, embeddings=emb, documents=batch, metadatas=metadatas_batch)

        self._index_embedding_model = self._get_embedding_model_name()
        try:
            self._last_load_mtime = self.persist_dir.stat().st_mtime
        except OSError:
            self._last_load_mtime = 0.0
        return f"Success: indexed {len(chunks)} chunks from {kb_path_resolved} (sources: {', '.join(sources)})."

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
            coll = client.get_collection("knowledge_base")
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
                "chunk_type": meta.get("chunk_type", "general"),
            }
            self._chunks.append(ch)
            self._id_to_chunk[id_] = ch
        tokenized = [_tokenize_for_bm25(c["text"]) for c in self._chunks]
        self._bm25 = BM25Okapi(tokenized)
        self._chroma = client
        self._index_embedding_model = ""
        try:
            coll = client.get_collection("knowledge_base")
            self._index_embedding_model = (coll.metadata or {}).get("embedding_model", "")
        except Exception:
            pass
        try:
            self._last_load_mtime = self.persist_dir.stat().st_mtime
        except OSError:
            self._last_load_mtime = 0.0
        logger.info("RAG loaded from Chroma: {} chunks", len(self._chunks))
        return True

    def _vector_search(
        self,
        query_text: str,
        top_k: int,
        *,
        where: dict[str, str] | None = None,
    ) -> list[tuple[str, float]]:
        if not self._chroma or not self._ensure_embedding():
            return []
        q_emb = self._embed_fn([query_text])[0]
        coll = self._chroma.get_collection("knowledge_base")
        n = min(max(top_k * 4, 12), len(self._id_to_chunk))
        kwargs: dict = {"query_embeddings": [q_emb], "n_results": n}
        if where:
            kwargs["where"] = where
        results = coll.query(**kwargs)
        ids = results["ids"][0]
        return list(zip(ids, [1.0] * len(ids)))

    def _bm25_search(
        self,
        query_text: str,
        top_k: int,
        *,
        only_concrete_fact: bool = False,
    ) -> list[tuple[str, float]]:
        if not self._bm25:
            return []
        tokenized_q = _tokenize_for_bm25(query_text)
        if not tokenized_q:
            return []
        scores = self._bm25.get_scores(tokenized_q)
        order = scores.argsort()[::-1]
        out = []
        n_candidates = max(top_k * 4, 12)
        # concrete_only 时只扫前 N 位凑 concrete_fact；否则扫全表收集所有 score>0，避免只取前 N 位时第二位即为 0 导致 bm25_hits=1
        max_scan = len(order) if only_concrete_fact else len(order)
        for i, idx in enumerate(order):
            if i >= max_scan:
                break
            if scores[idx] <= 0:
                continue  # 跳过 0 分，继续收集其它正分文档，不要 break 导致只返回 1 条
            if only_concrete_fact and self._chunks[idx].get("chunk_type") != "concrete_fact":
                continue
            id_ = self._chunk_ids[idx] if idx < len(self._chunk_ids) else self._chunk_id(idx, self._chunks[idx])
            out.append((id_, float(scores[idx])))
            if len(out) >= n_candidates:
                break
        return out

    def query(
        self,
        query_text: str,
        top_k: int = 5,
        reliability: ReliabilityFilter = "prefer_reliable",
        scope: ChunkScope = "all",
    ) -> list[dict[str, Any]]:
        """
        两种搜索语义（不可混用）：
        - 去重：scope=concrete_only, reliability=all → 只搜并只返回「具体事实」，可靠+待验证都看，用于判断「库里是否已有这条进展」。
        - 订正：scope=all, reliability=reliable_only → 搜并返回「具体事实+general」，仅可靠库，供订正参考，不含待验证。
        检索层在 concrete_only 时只在 concrete_fact 上做向量/BM25，避免 general 占满 top-K 导致 returned=0。
        """
        if not self._chunks and (self._chroma is None or self._bm25 is None):
            if self._chroma is None and self.persist_dir.exists():
                self._load_from_chroma()
            if not self._chunks:
                return []
        # 若索引目录在本次加载后被更新（如其他进程执行了 rag_index），重载以保持 BM25 与 Chroma 一致
        if self._chunks and self._chroma is not None and self.persist_dir.exists():
            try:
                current_mtime = self.persist_dir.stat().st_mtime
                if current_mtime > self._last_load_mtime:
                    self._load_from_chroma()
            except OSError:
                pass

        # 过长 query 截断：保留前 N 字，避免 embedding 被平均化或超长截断导致结果泛化
        search_query = query_text.strip()
        orig_len = len(search_query)
        if orig_len > MAX_QUERY_CHARS:
            search_query = search_query[:MAX_QUERY_CHARS].rstrip()
            logger.info("RAG query truncated to {} chars for retrieval (was {} chars)", MAX_QUERY_CHARS, orig_len)

        q = search_query.strip()

        # 两种搜索语义：
        # 1) 去重：只搜/只返回「具体事实」，可靠+待验证都看 → scope=concrete_only, reliability=all
        # 2) 订正：搜「具体事实+general」，只看可靠库 → scope=all, reliability=reliable_only
        # concrete_only 时：向量用 where 只搜 concrete_fact（避免语义把泛化段排前）；BM25 也只在 concrete_fact 里取 top-K，否则全库 top 可能被「框架」等词占满、具体事实条排不进前 N 导致 returned=0。
        if scope == "concrete_only":
            n_concrete = min(max(top_k * 6, 30), len(self._id_to_chunk))
            try:
                vec_list = self._vector_search(
                    search_query, n_concrete, where={"chunk_type": "concrete_fact"}
                )
            except Exception as e:
                logger.warning("RAG vector search failed: {}", e)
                vec_list = []
            try:
                bm25_list = self._bm25_search(
                    search_query, max(top_k * 6, 30), only_concrete_fact=True
                )
            except Exception as e:
                logger.warning("RAG BM25 search failed: {}", e)
                bm25_list = []
        else:
            n_pool = min(max(top_k * 8, 40), len(self._id_to_chunk))
            try:
                vec_list = self._vector_search(search_query, n_pool)
            except Exception as e:
                logger.warning("RAG vector search failed: {}", e)
                vec_list = []
            try:
                bm25_list = self._bm25_search(search_query, n_pool)
            except Exception as e:
                logger.warning("RAG BM25 search failed: {}", e)
                bm25_list = []

        # Embedding 模型不一致时提示
        current_emb = self._get_embedding_model_name()
        if self._index_embedding_model and current_emb != self._index_embedding_model:
            logger.warning(
                "RAG embedding model mismatch: index {!r} vs query {!r}; re-run rag_index to rebuild.",
                self._index_embedding_model,
                current_emb,
            )

        logger.info(
            "RAG retrieval: vector_hits={}, bm25_hits={}, query={!r}",
            len(vec_list),
            len(bm25_list),
            search_query[:80],
        )
        if not vec_list and not bm25_list:
            logger.warning("RAG retrieval: both vector and BM25 returned 0.")

        # 按排名分别归一化再加权合并（两边分数量纲可能不同，用 1/rank 归一后 BM25 权重大于向量）
        def _rank_norm(r: Optional[int]) -> float:
            return 1.0 / r if r is not None and r >= 1 else 0.0
        vec_rank: dict[str, int] = {id_: rank for rank, (id_, _) in enumerate(vec_list, start=1)}
        bm25_rank: dict[str, int] = {id_: rank for rank, (id_, _) in enumerate(bm25_list, start=1)}
        all_ids = set(vec_rank) | set(bm25_rank)
        rrf_scores: dict[str, float] = {}
        for id_ in all_ids:
            rv, rb = vec_rank.get(id_), bm25_rank.get(id_)
            s = VEC_WEIGHT * _rank_norm(rv) + BM25_WEIGHT * _rank_norm(rb)
            rrf_scores[id_] = s
        # 共识加分：同时排在向量与 BM25 前几名的 chunk 略加分（双路都认为相关）
        n_top = min(4, len(vec_list), len(bm25_list))
        if n_top >= 1:
            vec_top = {id_ for id_, _ in vec_list[:n_top]}
            bm25_top = {id_ for id_, _ in bm25_list[:n_top]}
            for id_ in vec_top & bm25_top:
                rrf_scores[id_] = rrf_scores.get(id_, 0) + 0.08

        # 候选内 containment：含 query 子串/词的 chunk 提到 CONTAINMENT_SCORE，保证排前
        if len(q) >= 2:
            for id_ in list(rrf_scores.keys()):
                ch = self._id_to_chunk.get(id_)
                if not ch:
                    continue
                if scope == "concrete_only" and ch.get("chunk_type") != "concrete_fact":
                    continue
                if _bm25_containment_bonus(ch.get("text", ""), q) >= CONTAINMENT_THRESHOLD:
                    rrf_scores[id_] = max(rrf_scores.get(id_, 0), CONTAINMENT_SCORE)

        # Reliability：过滤或加权
        if reliability == "reliable_only":
            rrf_scores = {
                id_: s for id_, s in rrf_scores.items()
                if self._id_to_chunk.get(id_, {}).get("reliability") == "reliable"
            }
        elif reliability == "prefer_reliable":
            for id_ in list(rrf_scores.keys()):
                if self._id_to_chunk.get(id_, {}).get("reliability") == "reliable":
                    rrf_scores[id_] *= 1.5

        # general 块降权（已拿 CONTAINMENT 的不再乘），使泛化条在按得分排序时自然靠后
        for id_ in list(rrf_scores.keys()):
            ch = self._id_to_chunk.get(id_)
            if not ch or ch.get("chunk_type") != "general":
                continue
            if len(q) >= 2 and _bm25_containment_bonus(ch.get("text", ""), q) >= CONTAINMENT_THRESHOLD:
                continue
            rrf_scores[id_] *= GENERAL_WEIGHT

        # 排序：仅按相关性（先 containment 再得分）。订正查的是「相关内容」不强制具体事实靠前；去重端只返回 concrete_fact 由下方 scope 过滤保证
        def _sort_key(id_: str) -> tuple[int, float]:
            s = rrf_scores.get(id_, 0)
            tier_containment = 0 if s >= CONTAINMENT_SCORE else 1
            return (tier_containment, -s)

        sorted_ids = sorted(rrf_scores.keys(), key=_sort_key)[: max(top_k * 3, 15)]
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for id_ in sorted_ids:
            if id_ in seen:
                continue
            ch = self._id_to_chunk.get(id_)
            if not ch:
                continue
            if scope == "concrete_only" and ch.get("chunk_type") != "concrete_fact":
                continue
            seen.add(id_)
            out.append({
                "text": ch["text"],
                "source": ch.get("source", ""),
                "date": ch.get("date", ""),
                "reliability": ch.get("reliability", "reliable"),
            })
            if len(out) >= top_k:
                break
        return out
