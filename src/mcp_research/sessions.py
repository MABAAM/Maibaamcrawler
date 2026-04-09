"""Per-domain session pooling with vault-aware auth injection.

Sessions are reused across tool calls for connection keep-alive and cookie persistence.
Each domain gets its own requests.Session with auth from the credential vault.
"""

import logging
import random
import threading
import time
import urllib.parse

import requests

from . import config

logger = logging.getLogger(__name__)


class SessionPool:
    """Thread-safe pool of requests.Session objects keyed by (scheme, host)."""

    def __init__(self, vault_profiles=None):
        self._sessions: dict[tuple[str, str], tuple[requests.Session, float]] = {}
        self._lock = threading.Lock()
        self._vault_profiles = vault_profiles or {}

    def get_session(self, url: str) -> requests.Session:
        """Get or create a session for the given URL's domain."""
        parsed = urllib.parse.urlparse(url)
        key = (parsed.scheme or "https", parsed.hostname or "")

        with self._lock:
            if key in self._sessions:
                session, _ = self._sessions[key]
                self._sessions[key] = (session, time.time())
                return session

            session = self._create_session(url)
            self._sessions[key] = (session, time.time())
            return session

    def _create_session(self, url: str) -> requests.Session:
        """Create a new session, applying vault auth if a profile matches."""
        session = requests.Session()
        session.headers["User-Agent"] = random.choice(config.USER_AGENTS)

        if self._vault_profiles:
            from .vault import match_url, apply_auth
            profile = match_url(url, self._vault_profiles)
            if profile:
                apply_auth(session, profile)
                logger.debug(f"Applied vault profile '{profile.name}' to session")

        return session

    def close_all(self) -> None:
        """Close all sessions."""
        with self._lock:
            for session, _ in self._sessions.values():
                try:
                    session.close()
                except Exception:
                    pass
            self._sessions.clear()

    def evict_stale(self) -> int:
        """Remove sessions idle longer than SESSION_IDLE_TTL. Returns count evicted."""
        cutoff = time.time() - config.SESSION_IDLE_TTL
        evicted = 0
        with self._lock:
            stale_keys = [k for k, (_, ts) in self._sessions.items() if ts < cutoff]
            for key in stale_keys:
                session, _ = self._sessions.pop(key)
                try:
                    session.close()
                except Exception:
                    pass
                evicted += 1
        if evicted:
            logger.debug(f"Evicted {evicted} stale sessions")
        return evicted

    def reload_vault(self, profiles) -> None:
        """Update vault profiles and clear all sessions (they'll be recreated with new auth)."""
        with self._lock:
            self._vault_profiles = profiles or {}
            for session, _ in self._sessions.values():
                try:
                    session.close()
                except Exception:
                    pass
            self._sessions.clear()
        logger.info("Session pool cleared after vault reload")

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)


# ── Module-level pool ───────────────────────────────────────────────────────

_pool: SessionPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> SessionPool:
    """Get the global session pool, creating it on first call."""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        from .vault import get_vault
        profiles = get_vault()
        _pool = SessionPool(vault_profiles=profiles)
        return _pool
