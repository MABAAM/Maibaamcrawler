"""Optional Ollama integration for summarization, query rewriting, and synthesis."""

import logging
import time

import requests

from . import config

logger = logging.getLogger(__name__)

_ollama_available_cache: bool | None = None
_ollama_available_ts: float = 0


def ollama_available() -> bool:
    """Ping Ollama /api/tags — result cached for 60s."""
    global _ollama_available_cache, _ollama_available_ts
    if not config.OLLAMA_URL:
        return False
    if _ollama_available_cache is not None and (time.time() - _ollama_available_ts) < 60:
        return _ollama_available_cache
    try:
        resp = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
        _ollama_available_cache = resp.ok
    except Exception:
        _ollama_available_cache = False
    _ollama_available_ts = time.time()
    return _ollama_available_cache


def ollama_query(prompt: str, system: str = "", model: str = "",
                 max_tokens: int = 2000) -> str | None:
    """Query Ollama. Returns response text or None on failure — never raises."""
    if not ollama_available():
        return None
    model = model or config.OLLAMA_MODEL
    max_tokens = min(max_tokens, 8000)
    payload = {"model": model, "prompt": prompt, "stream": False}
    if system:
        payload["system"] = system
    try:
        resp = requests.post(
            f"{config.OLLAMA_URL}/api/generate",
            json=payload, timeout=60,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
        max_chars = max_tokens * 4
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... [truncated to ~{max_tokens} tokens]"
        return text
    except Exception as e:
        logger.debug(f"Ollama query failed: {e}")
        return None


def summarize_text(text: str, context: str = "") -> str | None:
    """Summarize text via Ollama."""
    ctx = f" in the context of: {context}" if context else ""
    return ollama_query(
        prompt=f"Summarize this web page concisely{ctx}:\n\n{text[:8000]}",
        system="Provide a clear, focused summary of the web page content. Max 300 words.",
        max_tokens=500,
    )


def rewrite_query(query: str) -> str | None:
    """Rewrite a question as an optimized search query."""
    result = ollama_query(
        prompt=f'Rewrite this question as an optimized web search query (keywords only, no explanation, max 10 words):\n"{query}"',
        system="Output only the search query, nothing else.",
        max_tokens=50,
    )
    if result:
        result = result.strip().strip("\"'")
        if len(result) > 5:
            return result
    return None


def synthesize(query: str, summaries: list[str], context: str = "") -> str | None:
    """Synthesize a final answer from multiple source summaries."""
    synthesis_input = "\n\n".join(summaries)
    context_prefix = f"Context from previous research:\n{context}\n\n" if context else ""
    return ollama_query(
        prompt=f'{context_prefix}Based on these web sources about "{query}":\n\n{synthesis_input}\n\nProvide a comprehensive synthesis answering the question.',
        system="Synthesize the provided web sources into a clear, well-structured answer. Cite source numbers [1], [2], etc.",
        max_tokens=2000,
    )
