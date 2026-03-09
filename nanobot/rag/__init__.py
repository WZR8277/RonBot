"""RAG module: hybrid retrieval (vector + BM25) with RRF and reliability-aware ranking."""

from nanobot.rag.chunking import chunk_knowledge_base
from nanobot.rag.engine import RAGEngine

__all__ = ["RAGEngine", "chunk_knowledge_base"]
