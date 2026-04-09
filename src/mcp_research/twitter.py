"""X.com/Twitter extraction — tweets, threads via yt-dlp, API, or authenticated fetch.

Strategy cascade:
1. yt-dlp --dump-json (works for public tweets, media, metadata)
2. Twitter API v2 with bearer token from vault
3. Cookie-based authenticated fetch + HTML parsing (last resort)
"""

import json
import logging
import re
import shutil
import subprocess

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_TWEET_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:x\.com|twitter\.com)/(\w+)/status/(\d+)",
    re.IGNORECASE,
)

_PROFILE_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:x\.com|twitter\.com)/(\w+)/?$",
    re.IGNORECASE,
)


def _check_ytdlp() -> str | None:
    """Return yt-dlp path or None."""
    return shutil.which("yt-dlp")


def _get_cookie_path() -> str | None:
    """Get cookie jar path from vault if configured for x.com."""
    try:
        from .vault import get_vault, match_url
        profiles = get_vault()
        profile = match_url("https://x.com/i/status/0", profiles)
        if profile and profile.auth and profile.auth.type == "cookie_jar":
            return profile.auth.params.get("path")
    except Exception:
        pass
    return None


def _get_bearer_token() -> str | None:
    """Get bearer token from vault if configured for api.x.com."""
    try:
        from .vault import get_vault, match_url
        profiles = get_vault()
        # Check for api.x.com profile
        profile = match_url("https://api.x.com/2/tweets/0", profiles)
        if profile and profile.auth:
            if profile.auth.type == "bearer":
                return profile.auth.params.get("token")
    except Exception:
        pass
    return None


# ── Strategy 1: yt-dlp ──────────────────────────────────────────────────────

def _ytdlp_extract(url: str) -> dict | None:
    """Extract tweet data via yt-dlp --dump-json."""
    ytdlp = _check_ytdlp()
    if not ytdlp:
        return None

    cmd = [ytdlp, "--dump-json", "--no-download", "--no-playlist", url]

    cookie_path = _get_cookie_path()
    if cookie_path:
        cmd.extend(["--cookies", cookie_path])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            logger.debug(f"yt-dlp failed: {proc.stderr[:200]}")
            return None

        data = json.loads(proc.stdout)
        return {
            "text": data.get("description", ""),
            "author": data.get("uploader", data.get("uploader_id", "")),
            "author_id": data.get("uploader_id", ""),
            "timestamp": data.get("timestamp"),
            "upload_date": data.get("upload_date", ""),
            "metrics": {
                "likes": data.get("like_count"),
                "retweets": data.get("repost_count"),
                "views": data.get("view_count"),
                "comments": data.get("comment_count"),
            },
            "media_urls": [f.get("url") for f in data.get("formats", [])[:3] if f.get("url")],
            "title": data.get("title", ""),
            "access_method": "yt-dlp",
        }
    except subprocess.TimeoutExpired:
        logger.debug("yt-dlp timed out")
        return None
    except Exception as e:
        logger.debug(f"yt-dlp extraction error: {e}")
        return None


# ── Strategy 2: Twitter API v2 ──────────────────────────────────────────────

def _api_extract(tweet_id: str) -> dict | None:
    """Extract tweet via Twitter API v2 (requires bearer token in vault)."""
    token = _get_bearer_token()
    if not token:
        return None

    try:
        resp = requests.get(
            f"https://api.x.com/2/tweets/{tweet_id}",
            params={
                "expansions": "author_id,attachments.media_keys",
                "tweet.fields": "text,created_at,public_metrics,conversation_id",
                "user.fields": "name,username",
                "media.fields": "url,preview_image_url",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.debug(f"Twitter API returned {resp.status_code}")
            return None

        data = resp.json().get("data", {})
        includes = resp.json().get("includes", {})
        users = {u["id"]: u for u in includes.get("users", [])}
        author_id = data.get("author_id", "")
        author_info = users.get(author_id, {})
        metrics = data.get("public_metrics", {})

        return {
            "text": data.get("text", ""),
            "author": author_info.get("name", ""),
            "author_id": author_info.get("username", ""),
            "timestamp": data.get("created_at", ""),
            "conversation_id": data.get("conversation_id", ""),
            "metrics": {
                "likes": metrics.get("like_count"),
                "retweets": metrics.get("retweet_count"),
                "replies": metrics.get("reply_count"),
                "views": metrics.get("impression_count"),
            },
            "media_urls": [m.get("url") or m.get("preview_image_url", "")
                          for m in includes.get("media", [])],
            "access_method": "twitter_api_v2",
        }
    except Exception as e:
        logger.debug(f"Twitter API error: {e}")
        return None


# ── Strategy 3: Authenticated HTML fetch ────────────────────────────────────

def _html_extract(url: str) -> dict | None:
    """Last resort: fetch tweet page with cookies and parse HTML."""
    try:
        from .sessions import get_pool
        pool = get_pool()
        session = pool.get_session(url)
        resp = session.get(url, timeout=15)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Try to find tweet text in meta tags (works even with JS-rendered pages)
        og_desc = soup.find("meta", property="og:description")
        og_title = soup.find("meta", property="og:title")

        text = og_desc["content"] if og_desc and og_desc.get("content") else ""
        author = og_title["content"].split(" on X")[0] if og_title and og_title.get("content") else ""

        if not text:
            return None

        return {
            "text": text,
            "author": author,
            "access_method": "html_fetch",
        }
    except Exception as e:
        logger.debug(f"HTML extraction failed: {e}")
        return None


# ── Public API ──────────────────────────────────────────────────────────────

def extract_tweet(url: str) -> dict:
    """Extract a single tweet. Tries yt-dlp → API → HTML fetch."""
    m = _TWEET_URL_RE.match(url)
    if not m:
        return {"error": f"Invalid tweet URL: {url}",
                "hint": "Expected format: https://x.com/username/status/1234567890"}

    username, tweet_id = m.group(1), m.group(2)

    # Strategy cascade
    result = _ytdlp_extract(url)
    if not result:
        result = _api_extract(tweet_id)
    if not result:
        result = _html_extract(url)
    if not result:
        # Check what's available to give a helpful error
        has_ytdlp = _check_ytdlp() is not None
        has_token = _get_bearer_token() is not None
        has_cookies = _get_cookie_path() is not None
        hints = []
        if not has_ytdlp:
            hints.append("Install yt-dlp: pip install 'mcp-research[twitter]'")
        if not has_token and not has_cookies:
            hints.append("Configure X.com credentials in vault.yaml (cookie_jar or bearer token)")
        return {"error": "Could not extract tweet", "url": url, "hints": hints}

    result["url"] = url
    result["tweet_id"] = tweet_id
    return result


def extract_thread(url: str) -> dict:
    """Extract a tweet thread (conversation)."""
    # First get the root tweet
    tweet = extract_tweet(url)
    if "error" in tweet:
        return tweet

    conversation_id = tweet.get("conversation_id")
    if not conversation_id:
        # Without API access, we can't reliably get threads
        return {
            "thread": [tweet],
            "url": url,
            "note": "Thread extraction requires Twitter API v2 access. Only the root tweet was retrieved.",
        }

    # Try to get thread via API
    token = _get_bearer_token()
    if not token:
        return {
            "thread": [tweet],
            "url": url,
            "note": "Thread extraction requires Twitter API v2 bearer token in vault.",
        }

    try:
        resp = requests.get(
            "https://api.x.com/2/tweets/search/recent",
            params={
                "query": f"conversation_id:{conversation_id} from:{tweet.get('author_id', '')}",
                "tweet.fields": "text,created_at,public_metrics,conversation_id",
                "max_results": 100,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            thread_tweets = sorted(data, key=lambda t: t.get("created_at", ""))
            thread = [{"text": t.get("text", ""), "timestamp": t.get("created_at", "")}
                      for t in thread_tweets]
            return {"thread": [tweet] + thread, "url": url, "access_method": "twitter_api_v2"}
    except Exception as e:
        logger.debug(f"Thread fetch failed: {e}")

    return {"thread": [tweet], "url": url, "note": "Could not fetch additional thread tweets."}
