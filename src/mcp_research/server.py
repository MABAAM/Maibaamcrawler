"""FastMCP server exposing 3 research tools: web_search, fetch_url, research."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import config, ollama, search as search_mod, fetch as fetch_mod

logger = logging.getLogger(__name__)

server = FastMCP("mcp-research")

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
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
                    max_chars: int = 50000) -> str:
    """Fetch a URL, convert to markdown. SSRF-protected and cached.

    Args:
        url: The URL to fetch.
        summarize: If true and Ollama is available, include a summary.
        max_chars: Maximum characters of content to return.
    """
    max_chars = min(max(100, max_chars), config.FETCH_MD_MAX_CHARS)
    result = await asyncio.to_thread(fetch_mod.fetch_url, url, summarize, max_chars)

    if "error" in result:
        return f"**Error:** {result['error']}"

    lines = [f"## {result.get('title', 'Untitled')}"]
    lines.append(f"**URL:** {result['url']}")
    lines.append(f"**Length:** {result.get('content_length', 0)} chars")
    if result.get("from_cache"):
        lines.append("*(from cache)*")
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

    lines.append(f"### Sources ({len(fetched_pages)} pages fetched)\n")
    for i, page in enumerate(fetched_pages, 1):
        title = page.get("title", "Untitled")
        url = page.get("url", "")
        summary = page.get("summary", "")
        lines.append(f"**[{i}] [{title}]({url})**")
        if summary:
            lines.append(summary)
        lines.append("")

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
