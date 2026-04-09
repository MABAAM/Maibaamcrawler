"""Credential vault — YAML/JSON file mapping URL patterns to auth methods.

Vault file format (YAML preferred):

    version: 1
    profiles:
      my-profile:
        match: "*.example.com/**"
        auth:
          type: bearer          # bearer | basic | api_key | cookie_jar | headers
          token: "${MY_TOKEN}"  # env var interpolation
        ezproxy:                # optional
          base_url: "https://ezproxy.uni.edu/login?url="
          mode: prefix          # prefix | suffix

Secrets are resolved from environment variables at load time via ${VAR} syntax.
Profile __repr__ always redacts credentials.
"""

import fnmatch
import http.cookiejar
import logging
import os
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import requests

from . import config

logger = logging.getLogger(__name__)

_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


# ── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class EZProxyConfig:
    base_url: str
    mode: str = "prefix"  # prefix | suffix

    def __repr__(self):
        return f"EZProxyConfig(base_url='{self.base_url}', mode='{self.mode}')"


@dataclass
class AuthConfig:
    type: str  # bearer | basic | api_key | cookie_jar | headers
    params: dict[str, str] = field(default_factory=dict)

    def __repr__(self):
        return f"AuthConfig(type='{self.type}', params=[REDACTED])"


@dataclass
class VaultProfile:
    name: str
    match: str
    auth: AuthConfig | None = None
    ezproxy: EZProxyConfig | None = None

    def __repr__(self):
        parts = [f"VaultProfile(name='{self.name}', match='{self.match}'"]
        if self.auth:
            parts.append(f", auth_type='{self.auth.type}'")
        if self.ezproxy:
            parts.append(f", ezproxy_mode='{self.ezproxy.mode}'")
        parts.append(")")
        return "".join(parts)


# ── Env Var Resolution ──────────────────────────────────────────────────────

def _resolve_env_vars(value: str) -> str:
    """Replace ${VAR} with os.environ[VAR]. Missing vars resolve to empty string."""
    def _replace(m):
        var_name = m.group(1)
        return os.environ.get(var_name, "")
    return _ENV_VAR_RE.sub(_replace, value)


def _resolve_dict(d: dict) -> dict:
    """Recursively resolve env vars in all string values of a dict."""
    out = {}
    for k, v in d.items():
        if isinstance(v, str):
            out[k] = _resolve_env_vars(v)
        elif isinstance(v, dict):
            out[k] = _resolve_dict(v)
        else:
            out[k] = v
    return out


# ── Load / Parse ────────────────────────────────────────────────────────────

def _parse_yaml(text: str) -> dict:
    """Parse YAML, fall back to JSON if pyyaml unavailable."""
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        import json
        return json.loads(text)


def load_vault(path: Path | str) -> dict[str, VaultProfile]:
    """Load vault file. Returns empty dict if file doesn't exist or is invalid."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = _parse_yaml(raw)
    except Exception as e:
        logger.warning(f"Failed to parse vault file {path}: {e}")
        return {}

    version = data.get("version", 1)
    if version != 1:
        logger.warning(f"Unsupported vault version {version}, expected 1")
        return {}

    profiles_raw = data.get("profiles", {})
    if not isinstance(profiles_raw, dict):
        logger.warning("Vault 'profiles' must be a mapping")
        return {}

    profiles: dict[str, VaultProfile] = {}
    for name, pdata in profiles_raw.items():
        if not isinstance(pdata, dict):
            continue
        match_pattern = pdata.get("match", "")
        if not match_pattern:
            logger.warning(f"Vault profile '{name}' missing 'match' pattern, skipping")
            continue

        # Parse auth
        auth = None
        auth_raw = pdata.get("auth")
        if isinstance(auth_raw, dict):
            auth_resolved = _resolve_dict(auth_raw)
            auth_type = auth_resolved.pop("type", "")
            if auth_type:
                auth = AuthConfig(type=auth_type, params=auth_resolved)

        # Parse EZProxy
        ezproxy = None
        ez_raw = pdata.get("ezproxy")
        if isinstance(ez_raw, dict):
            ez_resolved = _resolve_dict(ez_raw)
            ezproxy = EZProxyConfig(
                base_url=ez_resolved.get("base_url", ""),
                mode=ez_resolved.get("mode", "prefix"),
            )

        profiles[name] = VaultProfile(name=name, match=match_pattern, auth=auth, ezproxy=ezproxy)
        logger.debug(f"Loaded vault profile: {name} -> {match_pattern}")

    logger.info(f"Vault loaded: {len(profiles)} profiles from {path}")
    return profiles


# ── URL Matching ────────────────────────────────────────────────────────────

def _url_to_match_string(url: str) -> str:
    """Normalize URL to 'host/path' for glob matching."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"
    return f"{host}{path}"


def match_url(url: str, profiles: dict[str, VaultProfile]) -> VaultProfile | None:
    """Find the first vault profile whose match pattern matches the URL. Returns None if no match."""
    match_str = _url_to_match_string(url)
    for profile in profiles.values():
        if fnmatch.fnmatch(match_str, profile.match):
            return profile
    return None


# ── Apply Auth to Session ───────────────────────────────────────────────────

def apply_auth(session: requests.Session, profile: VaultProfile) -> None:
    """Inject authentication into a requests.Session based on vault profile."""
    if not profile.auth:
        return

    auth = profile.auth
    params = auth.params

    if auth.type == "bearer":
        token = params.get("token", "")
        if token:
            session.headers["Authorization"] = f"Bearer {token}"

    elif auth.type == "basic":
        username = params.get("username", "")
        password = params.get("password", "")
        if username:
            session.auth = (username, password)

    elif auth.type == "api_key":
        header_name = params.get("header", "X-API-Key")
        value = params.get("value", "")
        if value:
            session.headers[header_name] = value

    elif auth.type == "cookie_jar":
        cookie_path = params.get("path", "")
        if cookie_path and Path(cookie_path).exists():
            try:
                jar = http.cookiejar.MozillaCookieJar(cookie_path)
                jar.load(ignore_discard=True, ignore_expires=True)
                session.cookies.update(jar)
            except Exception as e:
                logger.warning(f"Failed to load cookie jar {cookie_path}: {e}")

    elif auth.type == "headers":
        custom = {k: v for k, v in params.items() if isinstance(v, str)}
        session.headers.update(custom)

    else:
        logger.warning(f"Unknown auth type '{auth.type}' in profile '{profile.name}'")


# ── EZProxy URL Rewriting ───────────────────────────────────────────────────

def rewrite_ezproxy(url: str, ezproxy: EZProxyConfig) -> str:
    """Rewrite a URL through an EZproxy.

    Prefix mode: https://ezproxy.uni.edu/login?url=https://ieee.org/doc/123
    Suffix mode: https://ieee-org.ezproxy.uni.edu/doc/123
    """
    if ezproxy.mode == "suffix":
        parsed = urllib.parse.urlparse(url)
        ez_parsed = urllib.parse.urlparse(ezproxy.base_url)
        # Replace dots in host with hyphens, append ezproxy domain
        proxied_host = parsed.hostname.replace(".", "-") + "." + ez_parsed.hostname
        return urllib.parse.urlunparse((
            parsed.scheme, proxied_host, parsed.path,
            parsed.params, parsed.query, parsed.fragment,
        ))
    # Default: prefix mode
    return f"{ezproxy.base_url}{url}"


# ── Module-Level Vault State ────────────────────────────────────────────────

_vault_profiles: dict[str, VaultProfile] | None = None
_vault_lock = threading.Lock()
_vault_mtime: float = 0


def get_vault() -> dict[str, VaultProfile]:
    """Get vault profiles, loading from file on first call. Thread-safe."""
    global _vault_profiles, _vault_mtime
    with _vault_lock:
        if _vault_profiles is None:
            _vault_profiles = load_vault(config.VAULT_FILE)
            try:
                _vault_mtime = config.VAULT_FILE.stat().st_mtime
            except OSError:
                _vault_mtime = 0
            if config.VAULT_HOT_RELOAD and _vault_profiles:
                _start_watcher()
        return _vault_profiles


def reload_vault() -> None:
    """Force reload the vault from disk."""
    global _vault_profiles, _vault_mtime
    with _vault_lock:
        _vault_profiles = load_vault(config.VAULT_FILE)
        try:
            _vault_mtime = config.VAULT_FILE.stat().st_mtime
        except OSError:
            _vault_mtime = 0


def _start_watcher() -> None:
    """Start a daemon thread that polls vault file mtime every 5s."""
    def _watch():
        global _vault_mtime
        while True:
            time.sleep(5)
            try:
                current_mtime = config.VAULT_FILE.stat().st_mtime
                if current_mtime != _vault_mtime:
                    logger.info("Vault file changed, reloading")
                    reload_vault()
            except OSError:
                pass

    t = threading.Thread(target=_watch, daemon=True, name="vault-watcher")
    t.start()
