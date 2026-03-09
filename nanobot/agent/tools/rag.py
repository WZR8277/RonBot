"""RAG tools: rag_index (build hybrid index) and rag_query (hybrid + reliability-aware retrieval)."""

from pathlib import Path
from typing import Any

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
            "Call this after adding or changing markdown files in knowledge_base. "
            "Uses hybrid indexing (vector + BM25) with reliability metadata (reliable vs uncertain)."
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


class RAGQueryTool(Tool):
    """Query the RAG knowledge base: hybrid search (vector + BM25, RRF) with reliability-aware ranking."""

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
            "Search the RAG knowledge base with a natural language query. "
            "Returns relevant chunks from knowledge_base (hybrid vector + BM25, RRF merge). "
            "Use reliability: 'reliable_only' to exclude uncertain items, 'prefer_reliable' to rank them higher, 'all' for no filter."
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
        if not results:
            return "No relevant chunks found. Run rag_index first if you have not indexed knowledge_base."
        lines = ["## RAG results (knowledge_base)\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"### [{i}] {r.get('source', '')} ({r.get('date', '')}, {r.get('reliability', '')})")
            lines.append(r.get("text", "").strip()[:1200])
            lines.append("")
        return "\n".join(lines).strip()
