"""Academic paper resolution — DOI, ArXiv, PubMed with institutional access via vault.

Resolves identifiers to metadata + optional full text using EZproxy or direct access.
"""

import logging
import re
import urllib.parse

import requests

from . import config

logger = logging.getLogger(__name__)

# ── Identifier patterns ─────────────────────────────────────────────────────

_DOI_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)?(?:doi:)?(10\.\d{4,}/\S+)$", re.IGNORECASE)
_ARXIV_RE = re.compile(r"^(?:https?://arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?$", re.IGNORECASE)
_PUBMED_RE = re.compile(r"^(?:https?://pubmed\.ncbi\.nlm\.nih\.gov/)?(?:PMID:?\s*)?(\d{7,8})/?$", re.IGNORECASE)

# ── Publisher detection ─────────────────────────────────────────────────────

_PUBLISHER_MAP = {
    "ieeexplore.ieee.org": "IEEE",
    "link.springer.com": "Springer",
    "sciencedirect.com": "Elsevier",
    "dl.acm.org": "ACM",
    "arxiv.org": "ArXiv",
    "wiley.com": "Wiley",
    "onlinelibrary.wiley.com": "Wiley",
    "tandfonline.com": "Taylor & Francis",
    "nature.com": "Nature",
    "jstor.org": "JSTOR",
    "pubmed.ncbi.nlm.nih.gov": "PubMed",
    "pubs.acs.org": "ACS",
    "aip.scitation.org": "AIP",
    "iopscience.iop.org": "IOP",
    "pnas.org": "PNAS",
    "science.org": "AAAS",
}


def detect_publisher(url: str) -> str | None:
    """Detect academic publisher from URL domain."""
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return None
    for domain, publisher in _PUBLISHER_MAP.items():
        if host == domain or host.endswith("." + domain):
            return publisher
    return None


# ── Identifier detection ────────────────────────────────────────────────────

def detect_identifier(text: str) -> tuple[str, str]:
    """Detect identifier type and extract normalized ID.

    Returns: (type, normalized_id) where type is 'doi', 'arxiv', 'pubmed', or 'url'.
    """
    text = text.strip()

    m = _DOI_RE.match(text)
    if m:
        return "doi", m.group(1)

    m = _ARXIV_RE.match(text)
    if m:
        return "arxiv", m.group(1)

    m = _PUBMED_RE.match(text)
    if m:
        return "pubmed", m.group(1)

    if text.startswith("http"):
        return "url", text

    return "unknown", text


def is_doi(text: str) -> bool:
    return detect_identifier(text)[0] == "doi"


def is_arxiv_id(text: str) -> bool:
    return detect_identifier(text)[0] == "arxiv"


def is_pubmed_id(text: str) -> bool:
    return detect_identifier(text)[0] == "pubmed"


# ── DOI Resolution ──────────────────────────────────────────────────────────

def _crossref_metadata(doi: str) -> dict | None:
    """Fetch metadata from doi.org via content negotiation (Crossref citeproc JSON)."""
    try:
        resp = requests.get(
            f"https://doi.org/{doi}",
            headers={
                "Accept": "application/citeproc+json",
                "User-Agent": "mcp-research/0.3.0 (mailto:research@maibaam.com)",
            },
            timeout=10,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        authors = []
        for a in data.get("author", []):
            name = f"{a.get('given', '')} {a.get('family', '')}".strip()
            if name:
                authors.append(name)
        return {
            "doi": doi,
            "title": data.get("title", ""),
            "authors": authors,
            "journal": data.get("container-title", ""),
            "year": str(data.get("issued", {}).get("date-parts", [[""]])[0][0]),
            "abstract": data.get("abstract", ""),
            "publisher": data.get("publisher", ""),
            "url": data.get("URL", ""),
            "type": data.get("type", ""),
        }
    except Exception as e:
        logger.debug(f"Crossref lookup failed for {doi}: {e}")
        return None


def _resolve_doi_url(doi: str) -> str | None:
    """Follow DOI redirect to get the publisher landing page URL."""
    try:
        resp = requests.head(
            f"https://doi.org/{doi}",
            allow_redirects=True,
            timeout=10,
            headers={"User-Agent": "mcp-research/0.3.0"},
        )
        return resp.url
    except Exception:
        return None


def resolve_doi(doi: str, fetch_fulltext: bool = True) -> dict:
    """Resolve a DOI to metadata + optional full text.

    If vault has a matching profile with EZproxy, rewrites the URL for institutional access.
    """
    # Get metadata
    meta = _crossref_metadata(doi)
    if not meta:
        return {"error": f"Could not resolve DOI: {doi}", "doi": doi}

    result = dict(meta)
    result["access_method"] = "metadata_only"

    if not fetch_fulltext:
        return result

    # Get publisher URL
    publisher_url = meta.get("url") or _resolve_doi_url(doi)
    if not publisher_url:
        return result

    result["publisher_url"] = publisher_url
    result["publisher_name"] = detect_publisher(publisher_url) or meta.get("publisher", "")

    # Try to fetch full text via vault session
    try:
        from .sessions import get_pool
        from .vault import get_vault, match_url, rewrite_ezproxy

        pool = get_pool()
        profiles = get_vault()
        profile = match_url(publisher_url, profiles)

        fetch_url = publisher_url
        if profile and profile.ezproxy:
            fetch_url = rewrite_ezproxy(publisher_url, profile.ezproxy)
            result["access_method"] = f"ezproxy ({profile.name})"
        elif profile:
            result["access_method"] = f"authenticated ({profile.name})"

        session = pool.get_session(fetch_url)
        resp = session.get(fetch_url, timeout=15)

        if resp.status_code == 200:
            content_type = resp.headers.get("Content-Type", "").lower()
            if "pdf" in content_type or fetch_url.endswith(".pdf"):
                from .fetch import _extract_pdf_text
                text, _ = _extract_pdf_text(resp.content, fetch_url)
                if text:
                    result["full_text_md"] = text[:config.FETCH_MD_MAX_CHARS]
            elif "html" in content_type:
                from .fetch import html_to_markdown, smart_truncate
                md, _ = html_to_markdown(resp.text, base_url=fetch_url)
                result["full_text_md"] = smart_truncate(md, config.FETCH_MD_MAX_CHARS)
        elif resp.status_code in (401, 403):
            result["access_error"] = f"Access denied (HTTP {resp.status_code}). Configure vault credentials for {result['publisher_name']}."
    except Exception as e:
        logger.debug(f"Full text fetch failed for {doi}: {e}")

    return result


# ── ArXiv ───────────────────────────────────────────────────────────────────

def fetch_arxiv(arxiv_id: str) -> dict:
    """Fetch ArXiv paper — abstract from abs page, full text from PDF."""
    result = {
        "arxiv_id": arxiv_id,
        "publisher": "ArXiv",
        "access_method": "open_access",
    }

    # Fetch abstract page
    try:
        resp = requests.get(
            f"https://arxiv.org/abs/{arxiv_id}",
            timeout=10,
            headers={"User-Agent": "mcp-research/0.3.0"},
        )
        if resp.status_code == 200:
            from .fetch import html_to_markdown
            md, title = html_to_markdown(resp.text, base_url=f"https://arxiv.org/abs/{arxiv_id}")
            result["title"] = title or ""
            # Extract abstract from the page
            text = resp.text
            abs_start = text.find('<blockquote class="abstract')
            if abs_start != -1:
                abs_end = text.find("</blockquote>", abs_start)
                if abs_end != -1:
                    from bs4 import BeautifulSoup
                    abstract_html = text[abs_start:abs_end + len("</blockquote>")]
                    soup = BeautifulSoup(abstract_html, "html.parser")
                    abstract = soup.get_text(strip=True)
                    if abstract.startswith("Abstract:"):
                        abstract = abstract[9:].strip()
                    result["abstract"] = abstract
    except Exception as e:
        logger.debug(f"ArXiv abs fetch failed: {e}")

    # Fetch PDF for full text
    try:
        resp = requests.get(
            f"https://arxiv.org/pdf/{arxiv_id}",
            timeout=30,
            headers={"User-Agent": "mcp-research/0.3.0"},
            stream=True,
        )
        if resp.status_code == 200:
            chunks = []
            total = 0
            for chunk in resp.iter_content(8192):
                chunks.append(chunk)
                total += len(chunk)
                if total > config.FETCH_MAX_BYTES:
                    break
            from .fetch import _extract_pdf_text
            text, _ = _extract_pdf_text(b"".join(chunks), f"https://arxiv.org/pdf/{arxiv_id}")
            if text:
                result["full_text_md"] = text[:config.FETCH_MD_MAX_CHARS]
    except Exception as e:
        logger.debug(f"ArXiv PDF fetch failed: {e}")

    result["pdf_url"] = f"https://arxiv.org/pdf/{arxiv_id}"
    return result


# ── PubMed ──────────────────────────────────────────────────────────────────

def resolve_pubmed(pmid: str) -> dict:
    """Resolve a PubMed ID to metadata. Chains to DOI resolution if DOI found."""
    result = {"pmid": pmid, "publisher": "PubMed"}

    try:
        resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params={"db": "pubmed", "id": pmid, "retmode": "json"},
            timeout=10,
            headers={"User-Agent": "mcp-research/0.3.0"},
        )
        if resp.status_code == 200:
            data = resp.json()
            doc = data.get("result", {}).get(pmid, {})
            result["title"] = doc.get("title", "")
            result["journal"] = doc.get("fulljournalname", doc.get("source", ""))
            result["year"] = doc.get("pubdate", "").split(" ")[0] if doc.get("pubdate") else ""
            authors = [a.get("name", "") for a in doc.get("authors", [])]
            result["authors"] = authors

            # Extract DOI from articleids
            for aid in doc.get("articleids", []):
                if aid.get("idtype") == "doi":
                    result["doi"] = aid["value"]
                    break

            # Chain to DOI resolution for full text
            if "doi" in result:
                doi_result = resolve_doi(result["doi"])
                if "full_text_md" in doi_result:
                    result["full_text_md"] = doi_result["full_text_md"]
                    result["access_method"] = doi_result.get("access_method", "")
                if "abstract" not in result and doi_result.get("abstract"):
                    result["abstract"] = doi_result["abstract"]

    except Exception as e:
        logger.debug(f"PubMed lookup failed for {pmid}: {e}")
        result["error"] = f"PubMed lookup failed: {e}"

    return result


# ── Unified Entry Point ─────────────────────────────────────────────────────

def academic_lookup(identifier: str, fetch_fulltext: bool = True) -> dict:
    """Detect identifier type and resolve accordingly."""
    id_type, normalized = detect_identifier(identifier)

    if id_type == "doi":
        return resolve_doi(normalized, fetch_fulltext)
    elif id_type == "arxiv":
        return fetch_arxiv(normalized)
    elif id_type == "pubmed":
        return resolve_pubmed(normalized)
    elif id_type == "url":
        # Try to detect publisher and fetch directly
        publisher = detect_publisher(normalized)
        if publisher:
            return {"url": normalized, "publisher": publisher, "access_method": "direct_url",
                    "note": "Use fetch_url tool for direct URL access with vault authentication."}
        return {"url": normalized, "error": "Not a recognized academic identifier or publisher URL."}
    else:
        return {"error": f"Could not recognize identifier: {identifier}",
                "hint": "Supported: DOI (10.xxxx/...), ArXiv ID (2301.12345), PubMed ID (12345678), or publisher URL."}
