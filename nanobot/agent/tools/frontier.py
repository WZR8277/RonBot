"""Frontier assistant tools: frontier_ingest (collect from web, no LLM) and summarize (LLM summarization)."""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from nanobot.agent.tools.base import Tool

# Reuse web fetch constants and helpers
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5

def _default_frontier_queries() -> list[str]:
    """Technical-oriented queries (framework, benchmark, API) to avoid only company/news results."""
    return [
        "AI agent framework open source",
        "LLM agent benchmark evaluation",
    ]


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of spaces/newlines and trim each line to reduce noise in raw content."""
    if not text:
        return text
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _validate_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


# URL patterns for which we only store search snippet (no GET). Avoids: huge papers, and 403 from bot-blocking sites (e.g. Wikipedia).
_SNIPPET_ONLY_PATTERNS = (
    # Papers / long docs
    "arxiv.org",
    ".pdf",
    "semanticscholar.org",
    "acm.org",
    "ieee.org",
    "springer.com",
    "sciencedirect.com",
    "nature.com/articles",
    "biorxiv.org",
    "medrxiv.org",
    "openreview.net",
    "dl.acm.org",
    "ieeexplore.ieee.org",
    # Bot-blocking or rate-limited (403 / too many requests)
    "wikipedia.org",
    "wikimedia.org",
)


def _is_snippet_only_url(url: str) -> bool:
    """True if we should not fetch this URL; only store search snippet."""
    u = url.lower()
    return any(p in u for p in _SNIPPET_ONLY_PATTERNS)


class FrontierIngestTool(Tool):
    """
    One-shot ingest: run search queries, fetch top URLs, write raw results to workspace.
    No LLM — just API calls. Use this for the 采集 step; then agent does 总结/订正/影响分析.
    """

    def __init__(
        self,
        workspace: Path,
        api_key: str | None = None,
        proxy: str | None = None,
        max_results_per_query: int = 2,
        max_chars_per_source: int = 3000,
        inbox_subdir: str = "inbox",
    ):
        self._workspace = Path(workspace)
        self._api_key = api_key or os.environ.get("BRAVE_API_KEY", "")
        self._proxy = proxy
        self._max_results_per_query = max(1, min(5, max_results_per_query))  # default 2 to keep summary load small
        self._max_chars_per_source = max(500, min(12000, max_chars_per_source))
        self._inbox = self._workspace / inbox_subdir

    @property
    def name(self) -> str:
        return "frontier_ingest"

    @property
    def description(self) -> str:
        return (
            "Run frontier ingest: default 2 queries, 2 URLs per query (smaller summary load). Write raw to inbox. "
            "**Checks local first**: if today's raw file (frontier-YYYY-MM-DD-raw.md) already exists and is non-empty, returns immediately without calling search. "
            "Only call when user explicitly asked for 前沿周报/frontier report or when Cron/system triggered. Do NOT call for greetings or unrelated requests. "
            "Recency via freshness (default pw=7d). For 中文社区/博客 include Chinese results: pass search_lang='zh' and optionally country='CN'. "
            "No LLM — use for 采集; then read_file + summarize for 总结."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Search queries (topic only); if omitted, use default. Recency by freshness, not by adding year in query.",
                },
                "count_per_query": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "URLs to fetch per query. Recommended 3-5: enough diversity, not too noisy (default 3).",
                },
                "max_queries": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "When using default queries, run only first N (default all). Ignored if queries provided.",
                },
                "freshness": {
                    "type": "string",
                    "enum": ["", "pd", "pw", "pm", "py"],
                    "description": "Brave API: pd=24h, pw=7d, pm=31d, py=1y. Default pw (7d) for 前沿; empty = no filter.",
                },
                "date": {
                    "type": "string",
                    "description": "Filename date YYYY-MM-DD (default: today).",
                },
                "max_chars_per_source": {
                    "type": "integer",
                    "minimum": 500,
                    "maximum": 12000,
                    "description": "Max chars per URL body (default 3000). Paper/PDF URLs get snippet only. Lower = smaller raw file, fits better in context.",
                },
                "search_lang": {
                    "type": "string",
                    "description": "Brave API: content language (ISO 639-1). e.g. 'zh' for Chinese, 'en' for English. Use 'zh' to include 中文社区/博客.",
                },
                "country": {
                    "type": "string",
                    "description": "Brave API: country for results (ISO 3166-1 alpha-2). e.g. 'CN' for China. Use with search_lang=zh for 中文结果.",
                },
            },
            "required": [],
        }

    async def execute(
        self,
        queries: list[str] | None = None,
        count_per_query: int | None = None,
        max_queries: int | None = None,
        freshness: str | None = None,
        date: str | None = None,
        max_chars_per_source: int | None = None,
        search_lang: str | None = None,
        country: str | None = None,
        **kwargs: Any,
    ) -> str:
        if not self._api_key:
            return (
                "Error: Brave Search API key not set. Set tools.web.search.apiKey in config or BRAVE_API_KEY."
            )
        qs = queries if queries else _default_frontier_queries()
        if not qs:
            return "Error: No queries provided and no default."
        n_per_query = count_per_query if count_per_query is not None else self._max_results_per_query
        n_per_query = max(1, min(10, n_per_query))
        if queries:
            qs = qs[:20]
        else:
            cap = max_queries if max_queries is not None else 20
            cap = max(1, min(20, cap))
            qs = qs[:cap]
        # Omit → default pw (7d) for 前沿; pass "" → no date filter
        if freshness is None:
            fresh = "pw"
        else:
            raw = (freshness or "").strip().lower()
            fresh = raw if raw in ("pd", "pw", "pm", "py") else ""
        try:
            dt = date or datetime.now().strftime("%Y-%m-%d")
        except Exception:
            dt = datetime.now().strftime("%Y-%m-%d")
        cap_per_source = self._max_chars_per_source
        if max_chars_per_source is not None:
            cap_per_source = max(500, min(12000, max_chars_per_source))
        self._inbox.mkdir(parents=True, exist_ok=True)
        out_path = self._inbox / f"frontier-{dt}-raw.md"
        # 先检查本地是否已有本日 raw 文件，有则不再调用搜索
        if out_path.exists() and out_path.stat().st_size > 0:
            size = out_path.stat().st_size
            return (
                f"Today's raw file already exists: {out_path.name} ({size} bytes). "
                "Skip ingestion. Proceed to step 2: read_file this file for 总结."
            )
        sections: list[str] = []
        total_chars = 0
        async with httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            timeout=30.0,
            proxy=self._proxy,
        ) as client:
            for query in qs:
                params: dict[str, str | int] = {"q": query, "count": n_per_query}
                if fresh:
                    params["freshness"] = fresh
                if search_lang and isinstance(search_lang, str) and len(search_lang) >= 2:
                    params["search_lang"] = search_lang.strip().lower()[:5]
                if country and isinstance(country, str) and len(country) == 2:
                    params["country"] = country.strip().upper()
                try:
                    r = await client.get(
                        "https://api.search.brave.com/res/v1/web/search",
                        params=params,
                        headers={
                            "Accept": "application/json",
                            "X-Subscription-Token": self._api_key,
                        },
                        timeout=10.0,
                    )
                    r.raise_for_status()
                    results = r.json().get("web", {}).get("results", [])[:n_per_query]
                except Exception as e:
                    logger.warning("Frontier ingest search failed for {}: {}", query, e)
                    sections.append(f"## Query: {query}\n\n(Search failed: {e})\n")
                    continue
                if not results:
                    sections.append(f"## Query: {query}\n\n(No results)\n")
                    continue
                block = [f"## Query: {query}\n"]
                for i, item in enumerate(results, 1):
                    url = item.get("url") or ""
                    title = item.get("title") or url
                    if not _validate_url(url):
                        block.append(f"### Source {i}: {title} (invalid URL)\n\n(skipped)\n")
                        continue
                    # Snippet-only URLs (papers, Wikipedia, etc.): no GET — avoids huge content and 403
                    if _is_snippet_only_url(url):
                        snippet = _normalize_whitespace((item.get("description") or "").strip())
                        if len(snippet) > 800:
                            snippet = snippet[:800] + "..."
                        text = f"# {title}\n\n{snippet}" if snippet else f"# {title}\n\n(Paper/source page; only snippet saved to keep raw file small.)"
                        n = len(text)
                        total_chars += n
                        block.append(f"### Source {i}: {title} ({url}) [{n} chars, snippet only]\n\n{text}\n")
                        continue
                    # Normal page: fetch and extract body (short timeout to avoid hanging on 403/slow sites)
                    try:
                        resp = await client.get(
                            url,
                            headers={"User-Agent": USER_AGENT},
                            timeout=15.0,
                        )
                        resp.raise_for_status()
                        ctype = resp.headers.get("content-type", "")
                        text = ""
                        if "application/json" in ctype:
                            text = json.dumps(resp.json(), indent=2, ensure_ascii=False)
                        elif "text/html" in ctype or (resp.text[:256].lower().startswith(("<!doctype", "<html"))):
                            from readability import Document
                            doc = Document(resp.text)
                            raw = doc.summary()
                            text = re.sub(r"<[^>]+>", "", raw)
                            text = text.replace("&nbsp;", " ")
                            text = _normalize_whitespace(text)
                            if doc.title():
                                text = f"# {doc.title()}\n\n{text}"
                        else:
                            text = _normalize_whitespace(resp.text)
                        if len(text) > cap_per_source:
                            text = text[:cap_per_source] + "\n\n[truncated]"
                        n = len(text)
                        total_chars += n
                        block.append(f"### Source {i}: {title} ({url}) [{n} chars]\n\n{text}\n")
                    except Exception as e:
                        logger.warning("Frontier ingest fetch failed {}: {}", url, e)
                        block.append(f"### Source {i}: {title} ({url})\n\n(fetch failed: {e})\n")
                sections.append("\n".join(block))
        body = "\n\n---\n\n".join(sections)
        header = (
            f"# Frontier raw ingest {dt}\n"
            f"Total sections: {len(sections)} | Total chars: {total_chars}\n"
            f"Paper/PDF URLs: snippet only. Others capped at {cap_per_source} chars. Use [N chars] to decide when to call summarize (~2000+).\n\n---\n\n"
        )
        out_path.write_text(header + body, encoding="utf-8")
        rel_path = out_path.relative_to(self._workspace)
        return (
            f"Wrote {len(sections)} sections, {total_chars} chars total to {rel_path}. "
            f"Paper/PDF/Wikipedia etc. saved as snippet only; other pages capped at {cap_per_source} chars. "
            f"Read with read_file(path=\"{rel_path}\"). If the file is large, process one ## Query block at a time or call summarize on long sections to avoid context overflow."
        )


class SummarizeTool(Tool):
    """
    Summarize long text with LLM. Use when content exceeds threshold (e.g. 2000 chars)
    so the agent can compress before merging or feeding to next step.
    """

    def __init__(
        self,
        provider: Any,  # LLMProvider
        model: str | None = None,
        max_tokens: int = 1500,
        max_input_chars: int = 8000,
    ):
        self._provider = provider
        self._model = model or (provider.get_default_model() if hasattr(provider, "get_default_model") else "gpt-4o-mini")
        self._max_tokens = max_tokens
        self._max_input_chars = max_input_chars

    @property
    def name(self) -> str:
        return "summarize"

    @property
    def description(self) -> str:
        return (
            "Summarize long text with the LLM. Call this when content is over ~2000 chars (e.g. one source section) "
            "to get a shorter summary before merging. Returns summary only. Input can be from read_file or a paste."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to summarize (will be truncated if very long)",
                },
                "max_output_chars": {
                    "type": "integer",
                    "description": "Hint for max summary length (default 1500)",
                    "minimum": 200,
                    "maximum": 4000,
                },
            },
            "required": ["text"],
        }

    async def execute(
        self,
        text: str,
        max_output_chars: int | None = None,
        **kwargs: Any,
    ) -> str:
        if not (text and text.strip()):
            return "Error: text is required."
        inp = text.strip()
        if len(inp) > self._max_input_chars:
            inp = inp[: self._max_input_chars] + "\n\n[input truncated for model]"
        max_out = max_output_chars or 1500
        max_out = max(200, min(4000, max_out))
        sys = (
            "You are a concise summarizer. Summarize the following text in the same language. "
            "Keep key facts, numbers, names, and conclusions. Output only the summary, no preamble or title."
        )
        user = f"Summarize the following (max ~{max_out} chars):\n\n{inp}"
        try:
            response = await self._provider.chat(
                messages=[
                    {"role": "system", "content": sys},
                    {"role": "user", "content": user},
                ],
                model=self._model,
                max_tokens=min(self._max_tokens, max_out // 2 + 500),
                temperature=0.3,
            )
            out = (response.content or "").strip()
            if not out:
                return "Error: Empty summary from model."
            if len(out) > max_out:
                out = out[:max_out] + "..."
            return f"Summary ({len(out)} chars):\n\n{out}"
        except Exception as e:
            logger.error("Summarize failed: {}", e)
            return f"Error: Summarize failed: {e}"
