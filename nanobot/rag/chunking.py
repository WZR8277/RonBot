"""Markdown-aware chunking for knowledge_base: 标题 → 段落/列表项 → 固定字数兜底."""

import re
from pathlib import Path
from typing import Any

# 单条超过此长度才用「按字数」兜底切分
MAX_CHUNK_CHARS = 800
OVERLAP_CHARS = 80


def _reliability_from_path(relative_path: str) -> str:
    """路径含 uncertain/ → uncertain，否则 reliable."""
    return "uncertain" if "uncertain" in relative_path.replace("\\", "/") else "reliable"


def _date_from_path_or_content(relative_path: str, text: str) -> str:
    """从文件名或正文前 200 字提取 YYYY-MM-DD."""
    base = Path(relative_path).stem
    match = re.search(r"(\d{4}-\d{2}-\d{2})", base)
    if match:
        return match.group(1)
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text[:200])
    if match:
        return match.group(1)
    return ""


def _chunk_type(text: str) -> str:
    """【可靠】/【concrete_fact】→ concrete_fact；【general】或格式说明 → general."""
    if not text:
        return "general"
    if "【concrete_fact】" in text or "【可靠】" in text:
        return "concrete_fact"
    if "【general】" in text:
        return "general"
    if "以下由定时任务" in text or ("格式" in text and "追加" in text):
        return "general"
    return "general"


def _append_chunk(
    chunks: list[dict[str, Any]], text: str, source_path: str, date: str, reliability: str
) -> None:
    chunks.append({
        "text": text,
        "source": source_path,
        "date": date,
        "reliability": reliability,
        "chunk_type": _chunk_type(text),
    })


def _split_by_bullets(body: str) -> list[str]:
    """按列表项切分：每一条 '- ...' 及其续行为一块（语义边界）；列表前若有非 bullet 行则并入第一条。"""
    lines = body.splitlines()
    items: list[str] = []
    current: list[str] = []
    leading: list[str] = []
    for line in lines:
        if re.match(r"^\s*-\s+", line):
            if current:
                items.append("\n".join(current).strip())
                current = [line]
            else:
                if leading:
                    current = leading + [line]
                    leading = []
                else:
                    current = [line]
        else:
            if current:
                current.append(line)
            else:
                leading.append(line)
    if current:
        block = "\n".join(current).strip()
        if block:
            items.append(block)
    elif leading:
        block = "\n".join(leading).strip()
        if block:
            items.append(block)
    return items


def _split_by_char(text: str, max_chars: int, overlap: int) -> list[str]:
    """按字数兜底切分，带重叠。"""
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            break_at = text.rfind("\n", start, end + 1)
            if break_at == -1:
                break_at = text.rfind(". ", start, end + 1)
            if break_at != -1:
                end = break_at + 1
        out.append(text[start:end].strip())
        if not out[-1]:
            out.pop()
        start = end - overlap if end < len(text) else len(text)
    return out


def chunk_markdown(
    content: str,
    source_path: str,
    *,
    max_chunk_chars: int = MAX_CHUNK_CHARS,
    overlap_chars: int = OVERLAP_CHARS,
) -> list[dict[str, Any]]:
    """
    分块策略：标题 → 段落/列表项 → 固定字数（兜底）。
    - 先按 ## / ### 切出区块；
    - 每区块内：若有列表（- 行），则每条列表项单独成块（MOSAIC、MACT 等自然独立）；
    - 否则按段落（\\n\\n）切；
    - 单块超过 max_chunk_chars 时再按字数+重叠兜底。
    """
    reliability = _reliability_from_path(source_path)
    date = _date_from_path_or_content(source_path, content)

    # 1) 标题级：按 ## / ### 切
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

    if not parts:
        for para in re.split(r"\n\s*\n", content):
            para = para.strip()
            if para:
                parts.append(para)

    chunks: list[dict[str, Any]] = []
    for part in parts:
        lines = part.splitlines()
        if not lines:
            continue
        # 区块标题：第一个标题行或到第一个 bullet/空行
        header_lines: list[str] = []
        i = 0
        while i < len(lines) and not re.match(r"^\s*-\s+", lines[i]):
            if lines[i].strip() == "" and header_lines:
                break
            header_lines.append(lines[i])
            i += 1
        section_header = "\n".join(header_lines).strip()
        body = "\n".join(lines[i:]).strip() if i < len(lines) else ""

        if not body:
            if section_header:
                _append_chunk(chunks, section_header, source_path, date, reliability)
            continue

        # 2) 段落/列表项：有列表则按条，否则按段落
        bullet_items = _split_by_bullets(body)
        if len(bullet_items) >= 1:
            # 列表项语义：每条独立块
            for idx, item in enumerate(bullet_items):
                if not item.strip():
                    continue
                text = (section_header + "\n\n" + item) if idx == 0 and section_header else item
                if len(text) > max_chunk_chars:
                    for slice_text in _split_by_char(text, max_chunk_chars, overlap_chars):
                        if slice_text:
                            _append_chunk(chunks, slice_text, source_path, date, reliability)
                else:
                    _append_chunk(chunks, text, source_path, date, reliability)
        else:
            # 无列表：按段落
            for para in re.split(r"\n\s*\n", body):
                para = para.strip()
                if not para:
                    continue
                text = (section_header + "\n\n" + para) if section_header else para
                if len(text) > max_chunk_chars:
                    for slice_text in _split_by_char(text, max_chunk_chars, overlap_chars):
                        if slice_text:
                            _append_chunk(chunks, slice_text, source_path, date, reliability)
                else:
                    _append_chunk(chunks, text, source_path, date, reliability)

    return chunks


def chunk_knowledge_base(knowledge_base_path: Path) -> list[dict[str, Any]]:
    """仅索引 facts.md 与 uncertain/*.md，返回带 source/date/reliability/chunk_type 的 chunks。"""
    all_chunks: list[dict[str, Any]] = []
    if not knowledge_base_path.is_dir():
        return all_chunks
    for path in knowledge_base_path.rglob("*.md"):
        rel = str(path.relative_to(knowledge_base_path)).replace("\\", "/")
        if rel != "facts.md" and not rel.startswith("uncertain/"):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for ch in chunk_markdown(content, rel):
            ch["source"] = rel
            all_chunks.append(ch)
    return all_chunks
