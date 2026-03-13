"""RAG tools: rag_index (build hybrid index) and rag_query (hybrid + reliability-aware retrieval)."""

from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool


def _make_engine(workspace: Path, kb_subdir: str, api_key: str, embedding_model: str, persist_dir: Path | None):
    from nanobot.rag.engine import RAGEngine
    return RAGEngine(
        workspace=workspace,
        knowledge_base_subdir=kb_subdir,
        embedding_api_key=api_key,
        embedding_model=embedding_model,
        persist_dir=persist_dir,
    )


def _log_rag_preview(results: list, max_chunks: int = 3, snippet_len: int = 280) -> None:
    """Log a short preview of retrieved chunks (source + snippet) for debugging."""
    for i, r in enumerate(results[:max_chunks], 1):
        src = r.get("source", "")
        date = r.get("date", "")
        text = (r.get("text", "") or "").strip()
        preview = (text[:snippet_len] + "…") if len(text) > snippet_len else text
        logger.info(
            "rag result [{}/{}] source={!r} date={!r} | {}",
            i,
            len(results),
            src,
            date,
            preview.replace("\n", " "),
        )
    if len(results) > max_chunks:
        logger.info("rag result ... and {} more chunks", len(results) - max_chunks)


class RAGIndexTool(Tool):
    """Build or rebuild the RAG index from workspace/knowledge_base (hybrid: vector + BM25)."""

    def __init__(
        self,
        workspace: Path,
        knowledge_base_subdir: str = "knowledge_base",
        embedding_api_key: str = "",
        embedding_model: str = "",
        persist_dir: Path | None = None,
    ):
        self._workspace = Path(workspace)
        self._kb_subdir = knowledge_base_subdir
        self._api_key = embedding_api_key
        self._embedding_model = embedding_model
        self._persist_dir = persist_dir
        self._engine = _make_engine(
            self._workspace, self._kb_subdir, self._api_key, self._embedding_model, self._persist_dir
        )

    @property
    def name(self) -> str:
        return "rag_index"

    @property
    def description(self) -> str:
        return (
            "Build or rebuild the RAG index from the knowledge_base directory under workspace. "
            "Indexes **only facts.md and uncertain/*.md** (README.md and other files excluded); **tags each chunk by reliability** (path in uncertain/ -> uncertain, else reliable). "
            "**Must be called after every write to knowledge_base** (e.g. agent-frontier step 6 after step 5); otherwise new content will not be searchable. No parameters."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        loop = __import__("asyncio").get_event_loop()
        return await loop.run_in_executor(None, self._engine.build_index)


class RagEnsureInitializedTool(Tool):
    """First step: check if RAG index exists (chroma.sqlite3); if not, run rag_index to initialize."""

    def __init__(
        self,
        workspace: Path,
        knowledge_base_subdir: str = "knowledge_base",
        embedding_api_key: str = "",
        embedding_model: str = "",
        persist_dir: Path | None = None,
    ):
        self._workspace = Path(workspace)
        self._kb_subdir = knowledge_base_subdir
        self._api_key = embedding_api_key
        self._embedding_model = embedding_model
        self._persist_dir = persist_dir or (self._workspace / "memory" / "rag")
        self._engine = _make_engine(
            self._workspace, self._kb_subdir, self._api_key, self._embedding_model, self._persist_dir
        )

    @property
    def name(self) -> str:
        return "rag_ensure_initialized"

    @property
    def description(self) -> str:
        return (
            "**Call this first** in the frontier pipeline. Checks if RAG index exists (workspace/memory/rag/chroma.sqlite3). "
            "If it does not exist, runs rag_index to initialize; if it exists, returns without changing. No parameters."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        persist_dir = Path(self._persist_dir)
        chroma_file = persist_dir / "chroma.sqlite3"
        if chroma_file.exists():
            return "RAG already initialized (chroma.sqlite3 present). Proceed to step 1 (采集)."
        loop = __import__("asyncio").get_event_loop()
        result = await loop.run_in_executor(None, self._engine.build_index)
        if result.startswith("Success"):
            return "RAG index initialized (chroma.sqlite3 was missing). " + result
        return result


class RAGQueryTool(Tool):
    """Query the RAG knowledge base: hybrid (vector + BM25, rank-normalized), reliability filter, results by relevance."""

    def __init__(
        self,
        workspace: Path,
        knowledge_base_subdir: str = "knowledge_base",
        embedding_api_key: str = "",
        embedding_model: str = "",
        persist_dir: Path | None = None,
    ):
        self._workspace = Path(workspace)
        self._kb_subdir = knowledge_base_subdir
        self._api_key = embedding_api_key
        self._embedding_model = embedding_model
        self._persist_dir = persist_dir
        self._engine = _make_engine(
            self._workspace, self._kb_subdir, self._api_key, self._embedding_model, self._persist_dir
        )

    @property
    def name(self) -> str:
        return "rag_query"

    @property
    def description(self) -> str:
        return (
            "Search the RAG knowledge base (hybrid vector + BM25, rank-normalized merge). "
            "Returns **concrete facts and general** (scope=all), **ordered by relevance** (containment then score; general chunks down-weighted). "
            "reliability: 'reliable_only' = only facts.md，不含待验证；'prefer_reliable' = reliable 加权；'all' = 全库。"
            " **步骤 4.3 订正** 用 reliability='reliable_only' 取可靠库供参考。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query (e.g. 'recent agent framework advances', 'LLM tool use')",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max number of chunks to return",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                },
                "reliability": {
                    "type": "string",
                    "enum": ["reliable_only", "prefer_reliable", "all"],
                    "description": "Filter by reliability: reliable_only, prefer_reliable, or all",
                    "default": "prefer_reliable",
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        query: str,
        top_k: int = 5,
        reliability: str = "prefer_reliable",
        **kwargs: Any,
    ) -> str:
        if not query or not query.strip():
            return "Error: query is required."
        top_k = max(1, min(20, top_k))
        rel = reliability if reliability in ("reliable_only", "prefer_reliable", "all") else "prefer_reliable"
        loop = __import__("asyncio").get_event_loop()
        try:
            results = await loop.run_in_executor(
                None,
                lambda: self._engine.query(query_text=query.strip(), top_k=top_k, reliability=rel),
            )
        except Exception as e:
            return f"Error: RAG query failed: {e}"
        # 打印召回等指标到日志（loguru 使用 {} 格式化）
        logger.info(
            "rag_query: query={!r}, top_k={}, reliability={}, returned={} chunks",
            query.strip()[:80],
            top_k,
            rel,
            len(results),
        )
        if results:
            _log_rag_preview(results)
        if not results:
            return (
                "No relevant chunks (index empty or first run). "
                "Proceed with 订正 using 本期摘要; then 写回 and call rag_index once."
            )
        lines = [
            f"## RAG results (knowledge_base)\nquery={query.strip()!r}, top_k={top_k}, returned={len(results)} chunks\n",
        ]
        for i, r in enumerate(results, 1):
            lines.append(f"### [{i}] {r.get('source', '')} ({r.get('date', '')}, {r.get('reliability', '')})")
            lines.append(r.get("text", "").strip()[:1200])
            lines.append("")
        return "\n".join(lines).strip()


class RAGQueryDedupTool(Tool):
    """Query for deduplication only: returns only concrete facts (【可靠】), general chunks are excluded."""

    def __init__(
        self,
        workspace: Path,
        knowledge_base_subdir: str = "knowledge_base",
        embedding_api_key: str = "",
        embedding_model: str = "",
        persist_dir: Path | None = None,
    ):
        self._workspace = Path(workspace)
        self._kb_subdir = knowledge_base_subdir
        self._api_key = embedding_api_key
        self._embedding_model = embedding_model
        self._persist_dir = persist_dir
        self._engine = _make_engine(
            self._workspace, self._kb_subdir, self._api_key, self._embedding_model, self._persist_dir
        )

    @property
    def name(self) -> str:
        return "rag_query_dedup"

    @property
    def description(self) -> str:
        return (
            "For frontier **去重 only** (step 3). Fixed: reliability=all (facts+uncertain), scope=concrete_only. "
            "Returns **only concrete facts** (可靠+待验证都查)；general 不参与。 "
            "「是否已有」= 已在可靠或待验证里都算重复，避免重复写入。 "
            "Build multiple queries from 本期摘要 → call once per query. For 订正 (step 4.3) use **rag_query** with reliability=reliable_only (no uncertain)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Short query from 本期摘要: one topic or phrase, ~10–30 chars (e.g. 'MCP 2.0', 'LangGraph benchmark', 'GPT-5.4 计算机使用'). Long query hurts retrieval.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max number of chunks to return (default 5, max 10)",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        query: str,
        top_k: int = 5,
        **kwargs: Any,
    ) -> str:
        if not query or not query.strip():
            return "Error: query is required."
        top_k = max(1, min(10, top_k))
        loop = __import__("asyncio").get_event_loop()
        try:
            results = await loop.run_in_executor(
                None,
                lambda: self._engine.query(
                    query_text=query.strip(),
                    top_k=top_k,
                    reliability="all",
                    scope="concrete_only",
                ),
            )
        except Exception as e:
            return f"Error: RAG query failed: {e}"
        logger.info(
            "rag_query_dedup 召回: query={!r}, top_k={}, reliability=all, scope=concrete_only, returned={} chunks",
            query.strip()[:80],
            top_k,
            len(results),
        )
        if results:
            _log_rag_preview(results)
        if not results:
            return (
                "No relevant chunks (index empty or first run). "
                "Proceed with 订正 using 本期摘要; then 写回 and call rag_index once."
            )
        lines = [
            f"## RAG results (knowledge_base, full)\nquery={query.strip()!r}, top_k={top_k}, returned={len(results)} chunks\n",
        ]
        for i, r in enumerate(results, 1):
            lines.append(f"### [{i}] {r.get('source', '')} ({r.get('date', '')}, {r.get('reliability', '')})")
            lines.append(r.get("text", "").strip()[:1200])
            lines.append("")
        return "\n".join(lines).strip()
