"""3-tier web search cascade: Brave API → DuckDuckGo library → HTML scraper."""

import logging
import time
import urllib.parse

import requests

from . import config

logger = logging.getLogger(__name__)


def _get_ddgs_class():
    """Resolve DuckDuckGo search class from either package name."""
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        pass
    try:
        from duckduckgo_search import DDGS
        return DDGS
    except ImportError:
        return None


def web_search(query: str, max_results: int = 5) -> tuple[list[dict], str | None]:
    """Run the 3-tier search cascade. Returns (results, hint_or_none)."""
    query = str(query).strip()
    if not query:
        return [], "Empty query."

    # Tier 1: Brave Search API
    brave_results, brave_hint = _search_brave(query, max_results)
    if brave_results:
        return brave_results, None

    # Tier 2: DuckDuckGo DDGS library (retry once)
    DDGS = _get_ddgs_class()
    if DDGS is None:
        return _search_fallback(query, max_results)
    try:
        last_err = None
        for attempt in range(2):
            try:
                results = []
                with DDGS() as client:
                    for r in client.text(query, max_results=max_results):
                        results.append({
                            "title": r.get("title", ""),
                            "url": r.get("href", ""),
                            "snippet": r.get("body", ""),
                        })
                if results:
                    return results, brave_hint
                if attempt == 0:
                    time.sleep(1.5)
            except Exception as e:
                last_err = e
                if attempt == 0:
                    logger.info(f"DDGS attempt 1 failed ({e}), retrying in 2s")
                    time.sleep(2)

        if last_err:
            logger.warning(f"DDGS failed after retry: {last_err}")
            return _search_fallback(query, max_results)
        return [], brave_hint or "DuckDuckGo returned 0 results — try a different query."
    except Exception:
        return _search_fallback(query, max_results)


def _search_brave(query: str, max_results: int) -> tuple[list[dict] | None, str | None]:
    """Tier 1: Brave Search API (skipped if no BRAVE_API_KEY)."""
    if not config.BRAVE_API_KEY:
        return None, None
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": config.BRAVE_API_KEY,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for r in data.get("web", {}).get("results", [])[:max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("description", ""),
            })
        return results, None
    except Exception as e:
        # Sanitize: never leak API key in error messages
        err_msg = str(e)
        if config.BRAVE_API_KEY and config.BRAVE_API_KEY in err_msg:
            err_msg = err_msg.replace(config.BRAVE_API_KEY, "[REDACTED]")
        logger.warning(f"Brave API error: {err_msg}")
        return None, f"Brave Search API error: {err_msg}"


def _search_fallback(query: str, max_results: int) -> tuple[list[dict], str | None]:
    """Tier 3: Scrape DuckDuckGo Lite HTML — no extra deps beyond requests + bs4."""
    hint = "DuckDuckGo API unavailable — used HTML scraper fallback."
    try:
        from bs4 import BeautifulSoup
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for a in soup.select(".result__title a")[:max_results]:
            results.append({"title": a.get_text(), "url": a.get("href", ""), "snippet": ""})
        if results:
            return results, hint
        return [], "All search methods failed — no results found."
    except Exception as e:
        logger.warning(f"Fallback search failed: {e}")
        return [], f"All search methods failed — {e}"
