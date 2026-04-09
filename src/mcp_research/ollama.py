"""Optional Ollama integration for summarization, query rewriting, and synthesis."""

import base64
import logging
import time

import requests

from . import config

logger = logging.getLogger(__name__)

_ollama_available_cache: bool | None = None
_ollama_available_ts: float = 0
_ollama_consecutive_failures: int = 0
_ollama_backoff_until: float = 0


def ollama_available() -> bool:
    """Ping Ollama /api/tags — result cached for 60s. Circuit breaker: after 2 consecutive
    failures, backs off exponentially (60s → 120s → 300s, capped at 600s)."""
    global _ollama_available_cache, _ollama_available_ts, _ollama_backoff_until
    if not config.OLLAMA_URL:
        return False
    now = time.time()
    # Circuit breaker: respect backoff
    if now < _ollama_backoff_until:
        return False
    if _ollama_available_cache is not None and (now - _ollama_available_ts) < 60:
        return _ollama_available_cache
    try:
        resp = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
        _ollama_available_cache = resp.ok
        if resp.ok:
            _reset_circuit_breaker()
    except Exception:
        _ollama_available_cache = False
        _record_failure()
    _ollama_available_ts = now
    return _ollama_available_cache


def _record_failure():
    """Track consecutive failures for circuit breaker."""
    global _ollama_consecutive_failures, _ollama_backoff_until
    _ollama_consecutive_failures += 1
    if _ollama_consecutive_failures >= 2:
        backoff = min(60 * (2 ** (_ollama_consecutive_failures - 2)), 600)
        _ollama_backoff_until = time.time() + backoff
        logger.info(f"Ollama circuit breaker: backing off {backoff}s after {_ollama_consecutive_failures} failures")


def _reset_circuit_breaker():
    """Reset circuit breaker on successful response."""
    global _ollama_consecutive_failures, _ollama_backoff_until
    _ollama_consecutive_failures = 0
    _ollama_backoff_until = 0


def ollama_query(prompt: str, system: str = "", model: str = "",
                 max_tokens: int = 2000, images_b64: list[str] | None = None) -> str | None:
    """Query Ollama. Returns response text or None on failure — never raises."""
    if not ollama_available():
        return None
    model = model or config.OLLAMA_MODEL
    max_tokens = min(max_tokens, 8000)
    payload = {"model": model, "prompt": prompt, "stream": False}
    if system:
        payload["system"] = system
    if images_b64:
        payload["images"] = images_b64
    timeout = 120 if images_b64 else 60
    try:
        resp = requests.post(
            f"{config.OLLAMA_URL}/api/generate",
            json=payload, timeout=timeout,
        )
        resp.raise_for_status()
        _reset_circuit_breaker()
        text = resp.json().get("response", "")
        max_chars = max_tokens * 4
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... [truncated to ~{max_tokens} tokens]"
        return text
    except Exception as e:
        logger.debug(f"Ollama query failed: {e}")
        _record_failure()
        return None


def ollama_describe_image(file_path: str) -> str | None:
    """Describe an image via Ollama vision model. Returns None if no vision model configured."""
    if not config.OLLAMA_VISION_MODEL:
        return None
    try:
        with open(file_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
        return ollama_query(
            prompt="Describe the content of this image in detail. Include any text, diagrams, or technical information visible.",
            model=config.OLLAMA_VISION_MODEL,
            max_tokens=500,
            images_b64=[img_b64],
        )
    except Exception as e:
        logger.debug(f"Image description failed: {e}")
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
