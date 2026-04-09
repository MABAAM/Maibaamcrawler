"""CAPTCHA and bot-detection wall detection.

Detection-only — no solving. Results are included in fetch output so callers
(MCP clients, VS Code extensions) can decide how to handle blocked sources.
"""

from dataclasses import dataclass

import requests


@dataclass
class CaptchaResult:
    detected: bool
    provider: str | None  # cloudflare | hcaptcha | recaptcha | akamai | generic
    url: str
    suggestion: str

    def __bool__(self):
        return self.detected


# ── Signature sets ──────────────────────────────────────────────────────────

_CLOUDFLARE_SIGS = (
    "cf-challenge-running",
    "just a moment...",
    "_cf_chl_opt",
    "cdn-cgi/challenge-platform",
    "cf-please-wait",
    "cloudflare ray id",
)

_HCAPTCHA_SIGS = (
    "hcaptcha.com/1/api.js",
    "h-captcha-response",
    "hcaptcha-box",
)

_RECAPTCHA_SIGS = (
    "google.com/recaptcha",
    "g-recaptcha-response",
    "recaptcha/api.js",
)

_AKAMAI_SIGS = (
    "akamai bot manager",
    "akam/13/",
    "_abck",
)

_GENERIC_SIGS = (
    "access denied",
    "bot detection",
    "please verify you are human",
    "browser verification",
    "are you a robot",
    "complete the security check",
)

_WAF_SERVERS = ("cloudflare", "ddos-guard", "sucuri", "akamaighost", "imperva")


# ── Detection ───────────────────────────────────────────────────────────────

def detect_captcha(resp: requests.Response, body_text: str = "") -> CaptchaResult:
    """Check if a response is a CAPTCHA/bot-detection wall.

    Args:
        resp: The HTTP response object.
        body_text: The decoded body text (first 5000 chars is sufficient).

    Returns:
        CaptchaResult with detection details.
    """
    url = resp.url or ""

    # Only check 403/503 status codes
    if resp.status_code not in (403, 503):
        return CaptchaResult(False, None, url, "")

    # Check body signatures (case-insensitive, first 5000 chars)
    snippet = (body_text or "")[:5000].lower()

    # Check server header for WAF hints
    server_header = (resp.headers.get("Server", "") or "").lower()
    is_waf = any(w in server_header for w in _WAF_SERVERS)

    # Provider-specific detection
    for sig in _CLOUDFLARE_SIGS:
        if sig in snippet:
            return CaptchaResult(
                True, "cloudflare", url,
                "Cloudflare challenge detected. Try with authenticated session or browser cookies.",
            )

    for sig in _HCAPTCHA_SIGS:
        if sig in snippet:
            return CaptchaResult(
                True, "hcaptcha", url,
                "hCaptcha challenge detected. Requires browser interaction to solve.",
            )

    for sig in _RECAPTCHA_SIGS:
        if sig in snippet:
            return CaptchaResult(
                True, "recaptcha", url,
                "reCAPTCHA challenge detected. Requires browser interaction to solve.",
            )

    for sig in _AKAMAI_SIGS:
        if sig in snippet:
            return CaptchaResult(
                True, "akamai", url,
                "Akamai bot detection. Try with authenticated session or different User-Agent.",
            )

    for sig in _GENERIC_SIGS:
        if sig in snippet:
            return CaptchaResult(
                True, "generic", url,
                "Bot detection wall. Try with authenticated session or browser cookies.",
            )

    # WAF server header without body sigs — possible soft block
    if is_waf and resp.status_code == 403:
        return CaptchaResult(
            True, "generic", url,
            f"Blocked by WAF ({server_header}). Try with authenticated session.",
        )

    return CaptchaResult(False, None, url, "")
