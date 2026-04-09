"""URL fetch with SSRF guard, HTML→Markdown conversion, retry logic, and caching."""

import hashlib
import ipaddress
import json
import logging
import os
import random
import re
import tempfile
import time
import urllib.parse

import requests

from . import config

logger = logging.getLogger(__name__)


# ── SSRF Guard ───────────────────────────────────────────────────────────────

def is_safe_url(url: str) -> tuple[bool, str | None]:
    """Reject URLs targeting localhost, private IPs, or non-HTTP schemes.
    Resolves DNS to catch rebinding attacks (e.g. 127.0.0.1.nip.io)."""
    import socket
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False, "Invalid URL."
    if parsed.scheme not in ("http", "https"):
        return False, f"Scheme {parsed.scheme!r} not allowed — only http/https."
    hostname = parsed.hostname or ""
    if not hostname:
        return False, "No hostname in URL."
    # Block known localhost aliases
    if hostname in ("localhost", "0.0.0.0") or hostname.endswith(".local"):
        return False, "Localhost URLs are blocked."
    # Resolve hostname to actual IPs and check each one
    try:
        addrinfos = socket.getaddrinfo(hostname, parsed.port or 80, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False, f"Cannot resolve hostname: {hostname}"
    for family, _, _, _, sockaddr in addrinfos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
            # Unwrap IPv4-mapped IPv6 (::ffff:127.0.0.1 → 127.0.0.1)
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
                return False, f"Blocked: {hostname} resolves to private/reserved IP {ip_str}."
        except ValueError:
            return False, f"Invalid resolved address: {ip_str}"
    return True, None


# ── PDF Extraction ──────────────────────────────────────────────────────────

def _extract_pdf_text(raw_bytes: bytes, url: str) -> tuple[str | None, str]:
    """Extract text from PDF bytes via PyPDF2. Returns (text, title) or (None, '')."""
    tmp_path = None
    try:
        from PyPDF2 import PdfReader
        import io
        reader = PdfReader(io.BytesIO(raw_bytes))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        title = urllib.parse.urlparse(url).path.split("/")[-1] or "PDF"
        return text, title
    except ImportError:
        # Fallback: write to temp file and try basic text extraction
        return None, ""
    except Exception as e:
        logger.warning(f"PDF extraction failed: {e}")
        return None, ""


# ── Fetch with Retry ─────────────────────────────────────────────────────────

def fetch_with_retry(url: str, timeout: int = config.FETCH_TIMEOUT,
                     max_retries: int = config.FETCH_MAX_RETRIES,
                     session: requests.Session | None = None):
    """GET with exponential backoff on 429/5xx. Manual redirect following with per-hop SSRF.
    Returns (response, error_str)."""
    headers = {"User-Agent": random.choice(config.USER_AGENTS)}
    _get = session.get if session else requests.get
    last_err = None
    for attempt in range(max_retries):
        try:
            current_url = url
            for _hop in range(10):
                resp = _get(
                    current_url, headers=headers, timeout=timeout,
                    stream=True, allow_redirects=False,
                )
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "")
                    next_url = urllib.parse.urljoin(current_url, location)
                    safe, reason = is_safe_url(next_url)
                    if not safe:
                        return None, f"Redirect to blocked URL: {reason}"
                    current_url = next_url
                    continue
                break
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = f"HTTP {resp.status_code}"
                if attempt < max_retries - 1:
                    time.sleep((2 ** attempt) + random.uniform(0, 1))
                    continue
                return None, f"HTTP {resp.status_code} after {max_retries} retries"
            return resp, None
        except requests.exceptions.Timeout:
            last_err = "timeout"
            if attempt < max_retries - 1:
                time.sleep((2 ** attempt) + random.uniform(0, 1))
                continue
            return None, f"Timeout after {max_retries} retries"
        except requests.exceptions.RequestException as e:
            return None, str(e)[:200]
    return None, str(last_err)[:200]


# ── HTML → Markdown ──────────────────────────────────────────────────────────

def html_to_markdown(html_text: str, base_url: str = "") -> tuple[str, str]:
    """Convert HTML to structured Markdown. Returns (markdown, title)."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return html_text, ""

    soup = BeautifulSoup(html_text, "html.parser")

    # Strip non-content elements
    for tag in soup.find_all(["script", "style", "noscript", "nav", "footer",
                               "aside", "iframe", "svg", "form", "button", "header"]):
        tag.decompose()

    parts = []
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6",
                              "p", "li", "pre", "code", "blockquote",
                              "table", "tr", "td", "th",
                              "a", "ul", "ol", "dl", "dt", "dd"]):
        tag = el.name.lower()
        text = el.get_text(separator=" ", strip=True)
        if not text:
            continue

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            parts.append(f'\n{"#" * level} {text}\n')
        elif tag == "pre":
            code_el = el.find("code")
            code_text = code_el.get_text() if code_el else el.get_text()
            lang = ""
            if code_el and code_el.get("class"):
                cls = " ".join(code_el["class"])
                lang_match = re.search(r"language-(\w+)", cls)
                if lang_match:
                    lang = lang_match.group(1)
            parts.append(f"\n```{lang}\n{code_text.strip()}\n```\n")
        elif tag == "code" and el.parent and el.parent.name != "pre":
            parts.append(f"`{text}`")
        elif tag == "a":
            href = el.get("href", "")
            if href and not href.startswith("#") and not href.startswith("javascript:"):
                if base_url and not href.startswith(("http://", "https://")):
                    href = urllib.parse.urljoin(base_url, href)
                parts.append(f"[{text}]({href})")
        elif tag == "blockquote":
            for line in text.split("\n"):
                parts.append(f"> {line.strip()}")
        elif tag == "li":
            parts.append(f"- {text}")
        elif tag == "tr":
            cells = [td.get_text(strip=True) for td in el.find_all(["td", "th"])]
            if cells:
                parts.append("| " + " | ".join(cells) + " |")
        elif tag == "p":
            parts.append(text)
        elif tag == "dt":
            parts.append(f"**{text}**")
        elif tag == "dd":
            parts.append(f"  {text}")

    md = "\n\n".join(parts)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md, title


# ── Smart Truncation ─────────────────────────────────────────────────────────

def smart_truncate(text: str, max_chars: int) -> str:
    """Truncate at nearest section boundary rather than mid-paragraph."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last_heading = cut.rfind("\n## ")
    if last_heading > max_chars * 0.6:
        return cut[:last_heading].rstrip() + "\n\n[... truncated ...]"
    last_para = cut.rfind("\n\n")
    if last_para > max_chars * 0.6:
        return cut[:last_para].rstrip() + "\n\n[... truncated ...]"
    return cut.rstrip() + "\n\n[... truncated ...]"


# ── Cache Helpers ────────────────────────────────────────────────────────────

def _get_cache_path(url: str) -> str:
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return str(config.CACHE_DIR / f"{url_hash}.json")


def _is_cache_fresh(cache_path: str) -> bool:
    try:
        import os
        if not os.path.exists(cache_path):
            return False
        mtime = os.path.getmtime(cache_path)
        return (time.time() - mtime) < config.CACHE_TTL_HOURS * 3600
    except Exception:
        return False


def _read_cache(cache_path: str) -> dict | None:
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


_last_eviction_ts: float = 0

def _write_cache(cache_path: str, data: dict) -> None:
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass
    _maybe_evict_cache()


def _maybe_evict_cache() -> None:
    """Age-based + size-based cache eviction, at most once per 5 minutes."""
    global _last_eviction_ts
    now = time.time()
    if now - _last_eviction_ts < 300:
        return
    _last_eviction_ts = now
    try:
        cache_dir = config.CACHE_DIR
        cutoff = now - (config.CACHE_TTL_HOURS * 3600)
        entries = sorted(cache_dir.iterdir(), key=lambda p: p.stat().st_mtime)
        total_bytes = 0
        max_bytes = config.CACHE_MAX_SIZE_MB * 1024 * 1024
        for entry in entries:
            if not entry.is_file():
                continue
            st = entry.stat()
            if st.st_mtime < cutoff:
                entry.unlink(missing_ok=True)
                continue
            total_bytes += st.st_size
            if total_bytes > max_bytes:
                entry.unlink(missing_ok=True)
    except Exception:
        pass


# ── Log Helper ───────────────────────────────────────────────────────────────

def _log_event(query: str, result_count: int, source_tier: str = "fetch",
               error: str | None = None, extra: dict | None = None) -> None:
    """Append to search_log.ndjson."""
    try:
        from datetime import datetime, timezone
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "query": str(query)[:500],
            "result_count": result_count,
            "source_tier": source_tier,
            "error": str(error)[:200] if error else None,
        }
        if extra:
            rec.update(extra)
        log_path = config.LOG_DIR / "search_log.ndjson"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


# ── Main Fetch Function ─────────────────────────────────────────────────────

def fetch_url(url: str, summarize: bool = False,
              max_chars: int = config.FETCH_MD_MAX_CHARS) -> dict:
    """Fetch a URL, convert to markdown. SSRF-protected and cached.
    Returns dict with url, title, content_md, content_length, summary, from_cache."""
    safe, reason = is_safe_url(url)
    if not safe:
        return {"error": f"URL blocked: {reason}"}

    # Check cache
    cache_path = _get_cache_path(url)
    if _is_cache_fresh(cache_path):
        cached = _read_cache(cache_path)
        if cached:
            cached["from_cache"] = True
            _log_event(url, 1, "cache")
            return cached

    # Get authenticated session from pool
    try:
        from .sessions import get_pool
        session = get_pool().get_session(url)
    except Exception:
        session = None

    # Fetch
    resp, fetch_err = fetch_with_retry(url, session=session)
    if fetch_err:
        _log_event(url, 0, "fetch", error=fetch_err)
        return {"error": f"Fetch failed: {fetch_err}"}

    # Content-type check
    content_type = resp.headers.get("Content-Type", "").lower()

    # PDF extraction (before binary rejection)
    if "application/pdf" in content_type or url.lower().endswith(".pdf"):
        pdf_chunks = []
        pdf_total = 0
        for chunk in resp.iter_content(8192):
            pdf_chunks.append(chunk)
            pdf_total += len(chunk)
            if pdf_total > config.FETCH_MAX_BYTES:
                break
        pdf_raw = b"".join(pdf_chunks)
        content_md, title = _extract_pdf_text(pdf_raw, url)
        if content_md is None:
            return {"error": "PDF extraction failed. Install PyPDF2: pip install 'mcp-research[ingest]'"}
        content_md = smart_truncate(content_md, max_chars)
        summary = None
        if summarize and content_md:
            from . import ollama
            summary = ollama.summarize_text(content_md)
        result = {
            "url": url, "title": title or "", "content_md": content_md,
            "content_length": len(content_md), "summary": summary, "from_cache": False,
        }
        _write_cache(cache_path, result)
        _log_event(url, 1, "fetch")
        return result

    if any(ct in content_type for ct in ("image/", "video/", "audio/", "octet-stream")):
        return {"error": f"Binary content ({content_type.split(';')[0]}) — not fetchable as text."}

    # Read body with size cap
    chunks = []
    total = 0
    for chunk in resp.iter_content(8192):
        chunks.append(chunk)
        total += len(chunk)
        if total > config.FETCH_MAX_BYTES:
            break
    raw = b"".join(chunks)

    # Convert
    html_text = ""
    if "application/json" in content_type:
        try:
            json_data = json.loads(raw.decode("utf-8", errors="replace"))
            content_md = f"```json\n{json.dumps(json_data, indent=2, ensure_ascii=False)[:max_chars]}\n```"
        except Exception:
            content_md = f"```\n{raw.decode('utf-8', errors='replace')[:max_chars]}\n```"
        title = urllib.parse.urlparse(url).path.split("/")[-1] or "JSON"
    else:
        html_text = raw.decode("utf-8", errors="replace")
        content_md, title = html_to_markdown(html_text, base_url=url)

    # CAPTCHA detection (on HTML responses only)
    captcha_info = {}
    if "html" in content_type:
        try:
            from .captcha import detect_captcha
            captcha = detect_captcha(resp, html_text)
            if captcha.detected:
                captcha_info = {
                    "captcha_blocked": True,
                    "captcha_provider": captcha.provider,
                    "captcha_suggestion": captcha.suggestion,
                }
        except Exception:
            pass

    # Truncate
    content_md = smart_truncate(content_md, max_chars)

    # Summarize via Ollama
    summary = None
    if summarize and content_md:
        from . import ollama
        summary = ollama.summarize_text(content_md)

    result = {
        "url": url,
        "title": title or "",
        "content_md": content_md,
        "content_length": len(content_md),
        "summary": summary,
        "from_cache": False,
        **captcha_info,
    }

    # Write cache
    _write_cache(cache_path, result)
    _log_event(url, 1, "fetch")

    return result
