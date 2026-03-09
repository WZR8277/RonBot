"""Markdown-aware chunking for knowledge_base with source/date/reliability metadata."""

import re
from pathlib import Path
from typing import Any


def _reliability_from_path(relative_path: str) -> str:
    """Infer reliability from path: uncertain/ -> uncertain, else reliable."""
    return "uncertain" if "uncertain" in relative_path.replace("\\", "/") else "reliable"


def _date_from_path_or_content(relative_path: str, text: str) -> str:
    """Extract date from filename (YYYY-MM-DD) or from first line like '日期: YYYY-MM-DD'."""
    # Filename: facts.md, 2025-03-09-agent-advances.md, uncertain/2025-03-08.md
    base = Path(relative_path).stem
    match = re.search(r"(\d{4}-\d{2}-\d{2})", base)
    if match:
        return match.group(1)
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text[:200])
    if match:
        return match.group(1)
    return ""


def chunk_markdown(
    content: str,
    source_path: str,
    *,
    max_chunk_chars: int = 800,
    overlap_chars: int = 80,
) -> list[dict[str, Any]]:
    """
    Split markdown into chunks preserving section context. Each chunk has:
    - text, source, date, reliability.
    """
    reliability = _reliability_from_path(source_path)
    date = _date_from_path_or_content(source_path, content)

    # Split by ## or ### headers so chunks align to sections
    parts: list[str] = []
    current: list[str] = []
    for line in content.splitlines():
        if re.match(r"^#{2,6}\s", line):
            if current:
                block = "\n".join(current).strip()
                if block:
                    parts.append(block)
            current = [line]
        else:
            current.append(line)
    if current:
        block = "\n".join(current).strip()
        if block:
            parts.append(block)

    # If no headers, split by paragraphs
    if not parts:
        for para in re.split(r"\n\s*\n", content):
            para = para.strip()
            if para:
                parts.append(para)

    chunks: list[dict[str, Any]] = []
    for part in parts:
        if len(part) <= max_chunk_chars:
            chunks.append({
                "text": part,
                "source": source_path,
                "date": date,
                "reliability": reliability,
            })
            continue
        # Long block: split by sentences or fixed size with overlap
        start = 0
        while start < len(part):
            end = min(start + max_chunk_chars, len(part))
            if end < len(part):
                # Try to break at sentence or newline
                break_at = part.rfind("\n", start, end + 1)
                if break_at == -1:
                    break_at = part.rfind(". ", start, end + 1)
                if break_at != -1:
                    end = break_at + 1
            slice_text = part[start:end].strip()
            if slice_text:
                chunks.append({
                    "text": slice_text,
                    "source": source_path,
                    "date": date,
                    "reliability": reliability,
                })
            start = end - overlap_chars if end < len(part) else len(part)

    return chunks


def chunk_knowledge_base(knowledge_base_path: Path) -> list[dict[str, Any]]:
    """
    Walk knowledge_base dir, read .md files, return list of chunks with metadata.
    Paths in chunks are relative to knowledge_base_path.
    """
    all_chunks: list[dict[str, Any]] = []
    if not knowledge_base_path.is_dir():
        return all_chunks

    for path in knowledge_base_path.rglob("*.md"):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(path.relative_to(knowledge_base_path))
        for ch in chunk_markdown(content, rel):
            ch["source"] = rel  # keep relative
            all_chunks.append(ch)
    return all_chunks
