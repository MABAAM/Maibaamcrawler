"""FastMCP server exposing 8 research tools: web_search, fetch_url, research, youtube_essence, deep_ingest, academic_lookup, twitter_extract, vault_status."""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import config, ollama, search as search_mod, fetch as fetch_mod
from . import youtube as youtube_mod, ingest as ingest_mod
from . import academic as academic_mod, twitter as twitter_mod

logger = logging.getLogger(__name__)

server = FastMCP("mcp-research")

_CONCURRENCY = asyncio.Semaphore(config.MAX_CONCURRENCY)

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

_LOCAL_READ = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


@server.tool(annotations=_READ_ONLY)
async def web_search(query: str, max_results: int = 5,
                     summarize: bool = False, auto_fetch_top: bool = False) -> str:
    """Search the web using a 3-tier cascade (Brave → DuckDuckGo → scraper).

    Args:
        query: Search query string.
        max_results: Maximum number of results to return (1-20).
        summarize: If true and Ollama is available, summarize the results.
        auto_fetch_top: If true, also fetch the full content of the top result.
    """
    max_results = min(max(1, max_results), 20)

    results, hint = await asyncio.to_thread(search_mod.web_search, query, max_results)

    # Summarize results via Ollama
    summary = None
    if summarize and results:
        formatted = "\n".join(f"- {r['title']}: {r['snippet']}" for r in results if r.get("snippet"))
        if formatted:
            summary = await asyncio.to_thread(
                ollama.ollama_query,
                f'Summarize these web search results for the query "{query}":\n\n{formatted}',
                "Summarize these web search results concisely. Focus on the most relevant information.",
                "", 500,
            )

    # Auto-fetch top result
    full_content = None
    if auto_fetch_top and results and results[0].get("url"):
        fetched = await asyncio.to_thread(fetch_mod.fetch_url, results[0]["url"], False)
        if "error" not in fetched:
            full_content = fetched.get("content_md", "")

    # Format as markdown
    lines = [f"## Search Results for: {query}\n"]
    if hint:
        lines.append(f"*{hint}*\n")
    for i, r in enumerate(results, 1):
        lines.append(f"### {i}. [{r['title']}]({r['url']})")
        if r.get("snippet"):
            lines.append(r["snippet"])
        lines.append("")
    if summary:
        lines.append("## Summary\n")
        lines.append(summary)
    elif summarize:
        lines.append("*Ollama not available for summarization.*")
    if full_content:
        lines.append(f"\n## Full Content: {results[0]['title']}\n")
        lines.append(full_content)

    return "\n".join(lines)


@server.tool(annotations=_READ_ONLY)
async def fetch_url(url: str, summarize: bool = False,
                    max_chars: int = 0) -> str:
    """Fetch a URL, convert to markdown. SSRF-protected and cached.

    Args:
        url: The URL to fetch.
        summarize: If true and Ollama is available, include a summary.
        max_chars: Maximum content chars (default ~15K/4K tokens). Set higher for full pages.
    """
    if max_chars <= 0:
        max_chars = config.FETCH_DEFAULT_CHARS
    max_chars = min(max(100, max_chars), config.FETCH_MD_MAX_CHARS)
    result = await asyncio.to_thread(fetch_mod.fetch_url, url, summarize, max_chars)

    if "error" in result:
        return f"**Error:** {result['error']}"

    lines = [f"## {result.get('title', 'Untitled')}"]
    lines.append(f"**URL:** {result['url']}")
    lines.append(f"**Length:** {result.get('content_length', 0)} chars")
    if result.get("from_cache"):
        lines.append("*(from cache)*")
    if result.get("captcha_blocked"):
        lines.append(f"\n**CAPTCHA Detected:** {result.get('captcha_provider', 'unknown')}")
        lines.append(f"*{result.get('captcha_suggestion', '')}*")
    lines.append("")
    if result.get("summary"):
        lines.append("### Summary\n")
        lines.append(result["summary"])
        lines.append("")
    lines.append(result.get("content_md", ""))

    return "\n".join(lines)


@server.tool(annotations=_READ_ONLY)
async def research(query: str, depth: str = "standard",
                   context: str = "") -> str:
    """Compound research: search → fetch top pages → summarize → synthesize.

    Args:
        query: The research question.
        depth: Research depth — "quick" (2 pages), "standard" (5 pages), or "deep" (10 pages).
        context: Optional context from prior research to inform synthesis.
    """
    _DEPTH_CONFIGS = {
        "quick":    {"max_results": 3, "fetch_top": 2, "do_synthesize": False},
        "standard": {"max_results": 7, "fetch_top": 5, "do_synthesize": True},
        "deep":     {"max_results": 12, "fetch_top": 10, "do_synthesize": True},
    }
    if depth not in _DEPTH_CONFIGS:
        depth = "standard"
    depth_config = _DEPTH_CONFIGS[depth]

    # Step 0: Query rewriting via Ollama
    search_query = query
    rewritten = await asyncio.to_thread(ollama.rewrite_query, query)
    if rewritten:
        search_query = rewritten
        logger.info(f"Query rewritten: {query!r} → {search_query!r}")

    # Step 1: Web search
    results, hint = await asyncio.to_thread(search_mod.web_search, search_query, depth_config["max_results"])

    # Zero-result retry with expanded query
    if not results:
        expanded = await asyncio.to_thread(
            ollama.ollama_query,
            f'This search query returned no results: "{search_query}". Suggest a simpler, broader search query (keywords only, max 8 words):',
            "Output only the search query, nothing else.",
            "", 40,
        )
        if expanded:
            expanded = expanded.strip().strip("\"'")
            if expanded and expanded != search_query:
                results, hint = await asyncio.to_thread(search_mod.web_search, expanded, depth_config["max_results"])
                if results:
                    hint = f"Original query had no results. Expanded to: {expanded}"

    if not results:
        return f"## Research: {query}\n\nNo results found. {hint or ''}\n"

    # Step 2: Fetch top N pages in parallel
    fetch_urls = [r["url"] for r in results[:depth_config["fetch_top"]] if r.get("url")]

    def _fetch_one(u: str) -> dict | None:
        try:
            result = fetch_mod.fetch_url(u, summarize=True)
            if "error" not in result:
                return result
        except Exception as e:
            logger.warning(f"Fetch failed for {u}: {e}")
        return None

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=min(5, len(fetch_urls))) as executor:
        futures = [loop.run_in_executor(executor, _fetch_one, u) for u in fetch_urls]
        fetched_raw = await asyncio.gather(*futures)
    fetched_pages = [p for p in fetched_raw if p is not None]

    # Step 3: Synthesize
    synthesis = None
    if depth_config["do_synthesize"] and fetched_pages:
        summaries = []
        for i, page in enumerate(fetched_pages):
            title = page.get("title", "Untitled")
            summary = page.get("summary") or page.get("content_md", "")[:500]
            summaries.append(f"[{i+1}] {title}: {summary}")
        synthesis = await asyncio.to_thread(ollama.synthesize, query, summaries, context)

    # Log
    fetch_mod._log_event(query, len(fetched_pages), "research", extra={
        "depth": depth,
        "synthesis_len": len(synthesis or ""),
        "pages_fetched": len(fetched_pages),
    })

    # Format output
    lines = [f"## Research: {query}\n"]
    if search_query != query:
        lines.append(f"*Search query: {search_query}*\n")
    if hint:
        lines.append(f"*{hint}*\n")

    captcha_blocked = [p for p in fetched_pages if p.get("captcha_blocked")]
    lines.append(f"### Sources ({len(fetched_pages)} pages fetched)\n")
    per_source_cap = config.RESEARCH_PER_SOURCE_CHARS
    for i, page in enumerate(fetched_pages, 1):
        title = page.get("title", "Untitled")
        url = page.get("url", "")
        # Prefer summary over raw content for token efficiency
        summary = page.get("summary") or page.get("content_md", "")[:per_source_cap]
        lines.append(f"**[{i}] [{title}]({url})**")
        if page.get("captcha_blocked"):
            lines.append(f"*CAPTCHA blocked ({page.get('captcha_provider', 'unknown')}) — content may be incomplete*")
        if summary:
            lines.append(summary[:per_source_cap])
        lines.append("")
    if captcha_blocked:
        lines.append(f"*{len(captcha_blocked)} source(s) were CAPTCHA-blocked. Configure vault credentials for better access.*\n")

    if synthesis:
        lines.append("### Synthesis\n")
        lines.append(synthesis)
    elif depth_config["do_synthesize"]:
        lines.append("*Ollama not available for synthesis.*")

    if not fetched_pages:
        lines.append("### Search Results\n")
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. [{r['title']}]({r['url']}) — {r.get('snippet', '')}")

    return "\n".join(lines)


@server.tool(annotations=_READ_ONLY)
async def youtube_essence(url: str, mode: str = "standard") -> str:
    """Extract essence from a YouTube video: transcript, summary, key points, chapters, quotes.

    Args:
        url: YouTube URL (youtube.com/watch?v=, youtu.be/, youtube.com/shorts/).
        mode: Extraction depth — "quick" (TL;DR), "standard" (+ chapters), or "deep" (+ quotes).
    """
    async with _CONCURRENCY:
        result = await asyncio.to_thread(youtube_mod.youtube_essence, url, mode)

    if "error" in result:
        return f"**Error:** {result['error']}"

    lines = [f"## {result.get('title', 'YouTube Video')}"]
    lines.append(f"**URL:** {result['url']}")
    lines.append(f"**Duration:** {result.get('duration', '?')} | **Mode:** {mode}")
    if result.get("from_cache"):
        lines.append("*(from cache)*")
    lines.append("")

    if result.get("summary"):
        lines.append("### Summary\n")
        lines.append(result["summary"])
        lines.append("")

    if result.get("key_points"):
        lines.append("### Key Points\n")
        for kp in result["key_points"]:
            lines.append(f"- {kp}")
        lines.append("")

    if result.get("chapters"):
        lines.append("### Chapters\n")
        for ch in result["chapters"]:
            lines.append(f"- [{ch['time']}] {ch['title']}")
        lines.append("")

    if result.get("quotes"):
        lines.append("### Notable Quotes\n")
        for q in result["quotes"]:
            lines.append(f"> {q}")
        lines.append("")

    if result.get("transcript_excerpt"):
        excerpt = result["transcript_excerpt"]
        lines.append("### Transcript Excerpt\n")
        if len(excerpt) > 3000:
            lines.append(excerpt[:3000])
            lines.append("\n*[... transcript truncated]*")
        else:
            lines.append(excerpt)

    src = result.get("transcription_source")
    if src:
        lines.append(f"\n*Transcription: {src} ({result.get('transcript_length', 0)} chars)*")

    return "\n".join(lines)


@server.tool(annotations=_LOCAL_READ)
async def deep_ingest(path: str, include_types: str = "",
                      max_files: int = 200, summarize: bool = False) -> str:
    """Extract text from files in a directory or single file. Supports text, PDF, DOCX, XLSX, PPTX, audio, video, images.

    Args:
        path: Directory or file path to process.
        include_types: Comma-separated type filter (text,pdf,audio,video,image,office). Empty = all.
        max_files: Maximum files to process (1-5000).
        summarize: If true, generate an AI summary of the combined content.
    """
    max_files = min(max(1, max_files), 5000)

    async with _CONCURRENCY:
        result = await asyncio.to_thread(
            ingest_mod.deep_ingest, path,
            include_types=include_types, max_files=max_files,
            summarize=summarize,
        )

    if "error" in result:
        return f"**Error:** {result['error']}"

    lines = [f"## Deep Ingest: {os.path.basename(path)}"]
    lines.append(f"**Processed:** {result['files_processed']} | **Skipped:** {result['files_skipped']}")
    lines.append("")

    if result.get("by_type"):
        lines.append("### By Type\n")
        for ftype, counts in result["by_type"].items():
            lines.append(f"- **{ftype}**: {counts['ok']} extracted, {counts.get('skip', 0)} skipped")
        lines.append("")

    if result.get("summary"):
        lines.append("### Summary\n")
        lines.append(result["summary"])
        lines.append("")

    if result.get("content"):
        file_count = len(result["content"])
        shown = min(file_count, 15)
        lines.append(f"### Extracted Content ({file_count} files, showing {shown})\n")
        for c in result["content"][:shown]:
            lines.append(f"**{c['file']}** ({c['type']}, {c['chars']} chars)")
            excerpt = c.get("text", "")[:300]
            if excerpt:
                lines.append(excerpt)
            lines.append("")
        if file_count > shown:
            lines.append(f"*... and {file_count - shown} more files*")

    if result.get("errors"):
        lines.append(f"### Errors ({len(result['errors'])})\n")
        for err in result["errors"][:10]:
            lines.append(f"- {err}")

    return "\n".join(lines)


@server.tool(annotations=_READ_ONLY)
async def academic_lookup(identifier: str, fetch_fulltext: bool = True) -> str:
    """Resolve a DOI, ArXiv ID, or PubMed ID. Fetch paper via institutional access if configured in vault.

    Args:
        identifier: DOI (10.xxxx/...), ArXiv ID (2301.12345), PubMed ID (12345678), or publisher URL.
        fetch_fulltext: Attempt to fetch the full paper text via vault credentials / EZproxy.
    """
    async with _CONCURRENCY:
        result = await asyncio.to_thread(academic_mod.academic_lookup, identifier, fetch_fulltext)

    if "error" in result:
        hint = result.get("hint", "")
        return f"**Error:** {result['error']}\n{hint}" if hint else f"**Error:** {result['error']}"

    lines = [f"## {result.get('title', 'Academic Paper')}"]

    if result.get("doi"):
        lines.append(f"**DOI:** {result['doi']}")
    if result.get("arxiv_id"):
        lines.append(f"**ArXiv:** {result['arxiv_id']}")
    if result.get("pmid"):
        lines.append(f"**PubMed:** {result['pmid']}")

    if result.get("authors"):
        lines.append(f"**Authors:** {', '.join(result['authors'][:10])}")
    if result.get("journal"):
        lines.append(f"**Journal:** {result['journal']}")
    if result.get("year"):
        lines.append(f"**Year:** {result['year']}")
    if result.get("publisher_name") or result.get("publisher"):
        lines.append(f"**Publisher:** {result.get('publisher_name') or result.get('publisher', '')}")
    if result.get("access_method"):
        lines.append(f"**Access:** {result['access_method']}")
    if result.get("pdf_url"):
        lines.append(f"**PDF:** {result['pdf_url']}")

    if result.get("access_error"):
        lines.append(f"\n*{result['access_error']}*")

    lines.append("")

    if result.get("abstract"):
        lines.append("### Abstract\n")
        lines.append(result["abstract"])
        lines.append("")

    if result.get("full_text_md"):
        full_text = result["full_text_md"]
        cap = config.ACADEMIC_FULLTEXT_CHARS
        lines.append("### Full Text\n")
        if len(full_text) > cap:
            lines.append(full_text[:cap])
            lines.append(f"\n*[... truncated at {cap:,} chars — request with higher max_chars for full text]*")
        else:
            lines.append(full_text)

    if result.get("note"):
        lines.append(f"\n*{result['note']}*")

    return "\n".join(lines)


@server.tool(annotations=_READ_ONLY)
async def twitter_extract(url: str, include_thread: bool = False) -> str:
    """Extract tweet or thread from X.com/Twitter. Supports yt-dlp, API, and cookie-based access.

    Args:
        url: Tweet URL (x.com/user/status/id or twitter.com/user/status/id).
        include_thread: If true, fetch the full conversation thread.
    """
    async with _CONCURRENCY:
        if include_thread:
            result = await asyncio.to_thread(twitter_mod.extract_thread, url)
        else:
            result = await asyncio.to_thread(twitter_mod.extract_tweet, url)

    if "error" in result:
        hints = result.get("hints", [])
        msg = f"**Error:** {result['error']}"
        if hints:
            msg += "\n" + "\n".join(f"- {h}" for h in hints)
        return msg

    # Thread format
    if "thread" in result:
        lines = [f"## Thread from {result.get('url', url)}"]
        if result.get("note"):
            lines.append(f"*{result['note']}*")
        lines.append("")
        for i, tweet in enumerate(result["thread"], 1):
            author = tweet.get("author", "")
            text = tweet.get("text", "")
            ts = tweet.get("timestamp", "")
            lines.append(f"**[{i}]** {f'@{author} ' if author else ''}{f'({ts})' if ts else ''}")
            lines.append(text)
            lines.append("")
        return "\n".join(lines)

    # Single tweet format
    lines = [f"## Tweet by @{result.get('author_id') or result.get('author', 'unknown')}"]
    if result.get("author") and result.get("author") != result.get("author_id"):
        lines.append(f"**{result['author']}**")
    lines.append(f"**URL:** {result.get('url', url)}")
    if result.get("timestamp") or result.get("upload_date"):
        lines.append(f"**Date:** {result.get('timestamp') or result.get('upload_date', '')}")
    if result.get("access_method"):
        lines.append(f"**Via:** {result['access_method']}")
    lines.append("")

    lines.append(result.get("text", ""))
    lines.append("")

    metrics = result.get("metrics", {})
    metric_parts = []
    for key in ("likes", "retweets", "replies", "views", "comments"):
        val = metrics.get(key)
        if val is not None:
            metric_parts.append(f"{key}: {val:,}" if isinstance(val, int) else f"{key}: {val}")
    if metric_parts:
        lines.append(f"*{' | '.join(metric_parts)}*")

    if result.get("media_urls"):
        lines.append("\n### Media")
        for mu in result["media_urls"][:5]:
            lines.append(f"- {mu}")

    return "\n".join(lines)


@server.tool(annotations=_LOCAL_READ)
async def vault_status() -> str:
    """Show credential vault status, loaded profiles, and optional dependency availability. Never exposes secrets."""
    import shutil
    from .vault import get_vault

    profiles = get_vault()
    lines = ["## Vault Status\n"]

    if not profiles:
        vault_path = str(config.VAULT_FILE)
        lines.append("No profiles loaded.\n")
        lines.append(f"**Vault file:** `{vault_path}`")
        lines.append(f"**Exists:** {config.VAULT_FILE.exists()}")
        lines.append(f"**Hot reload:** {config.VAULT_HOT_RELOAD}\n")
        lines.append(f"Create `{vault_path}` to configure authentication for protected sources.")
    else:
        lines.append(f"**Profiles loaded:** {len(profiles)}")
        lines.append(f"**Vault file:** `{config.VAULT_FILE}`")
        lines.append(f"**Hot reload:** {config.VAULT_HOT_RELOAD}")
        lines.append("")
        lines.append("### Profiles\n")
        lines.append("| Profile | Match Pattern | Auth Type | EZProxy |")
        lines.append("|---------|--------------|-----------|---------|")
        for name, profile in profiles.items():
            auth_type = profile.auth.type if profile.auth else "-"
            ezproxy = profile.ezproxy.mode if profile.ezproxy else "-"
            lines.append(f"| {name} | `{profile.match}` | {auth_type} | {ezproxy} |")

    # Dependency check
    lines.append("\n### Optional Dependencies\n")
    deps = [
        ("yt-dlp", lambda: shutil.which("yt-dlp") is not None, "twitter, youtube"),
        ("PyPDF2", lambda: __import__("PyPDF2") or True, "academic, ingest"),
        ("python-docx", lambda: __import__("docx") or True, "ingest"),
        ("openpyxl", lambda: __import__("openpyxl") or True, "ingest"),
        ("python-pptx", lambda: __import__("pptx") or True, "ingest"),
        ("faster-whisper", lambda: __import__("faster_whisper") or True, "youtube, ingest"),
        ("ffmpeg", lambda: shutil.which("ffmpeg") is not None, "video extraction"),
        ("ollama", lambda: shutil.which("ollama") is not None, "summarization"),
    ]
    for name, check, used_by in deps:
        try:
            ok = check()
        except Exception:
            ok = False
        status = "installed" if ok else "**missing**"
        lines.append(f"- **{name}**: {status} *(used by: {used_by})*")

    lines.append(f"\nInstall all: `pip install 'mcp-research[all]'`")

    return "\n".join(lines)
