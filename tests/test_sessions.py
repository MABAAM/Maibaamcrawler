"""Session pool tests — creation, reuse, vault injection, eviction."""

import time
from unittest.mock import patch, MagicMock

import requests

from mcp_research.sessions import SessionPool
from mcp_research.vault import VaultProfile, AuthConfig


class TestSessionCreation:

    def test_creates_session_for_domain(self):
        pool = SessionPool()
        session = pool.get_session("https://example.com/page")
        assert isinstance(session, requests.Session)
        assert pool.active_count == 1

    def test_reuses_session_for_same_domain(self):
        pool = SessionPool()
        s1 = pool.get_session("https://example.com/a")
        s2 = pool.get_session("https://example.com/b")
        assert s1 is s2
        assert pool.active_count == 1

    def test_different_sessions_for_different_domains(self):
        pool = SessionPool()
        s1 = pool.get_session("https://example.com/a")
        s2 = pool.get_session("https://other.com/b")
        assert s1 is not s2
        assert pool.active_count == 2

    def test_session_has_user_agent(self):
        pool = SessionPool()
        session = pool.get_session("https://example.com")
        assert "User-Agent" in session.headers


class TestVaultInjection:

    def test_bearer_injected(self):
        profiles = {
            "test": VaultProfile(name="test", match="*.example.com/**",
                                 auth=AuthConfig(type="bearer", params={"token": "my-token"})),
        }
        pool = SessionPool(vault_profiles=profiles)
        session = pool.get_session("https://api.example.com/data")
        assert "Authorization" in session.headers
        assert session.headers["Authorization"] == "Bearer my-token"

    def test_no_match_no_auth(self):
        profiles = {
            "test": VaultProfile(name="test", match="*.example.com/**",
                                 auth=AuthConfig(type="bearer", params={"token": "x"})),
        }
        pool = SessionPool(vault_profiles=profiles)
        session = pool.get_session("https://other.com/page")
        assert "Authorization" not in session.headers


class TestEviction:

    def test_stale_sessions_evicted(self):
        pool = SessionPool()
        pool.get_session("https://example.com")
        assert pool.active_count == 1
        # Manually set timestamp to old
        for key in pool._sessions:
            session, _ = pool._sessions[key]
            pool._sessions[key] = (session, time.time() - 99999)
        evicted = pool.evict_stale()
        assert evicted == 1
        assert pool.active_count == 0

    def test_fresh_sessions_kept(self):
        pool = SessionPool()
        pool.get_session("https://example.com")
        evicted = pool.evict_stale()
        assert evicted == 0
        assert pool.active_count == 1


class TestCloseAll:

    def test_closes_and_clears(self):
        pool = SessionPool()
        pool.get_session("https://a.com")
        pool.get_session("https://b.com")
        assert pool.active_count == 2
        pool.close_all()
        assert pool.active_count == 0


class TestReloadVault:

    def test_clears_sessions_on_reload(self):
        pool = SessionPool()
        pool.get_session("https://example.com")
        assert pool.active_count == 1
        new_profiles = {
            "new": VaultProfile(name="new", match="*.new.com/**",
                                auth=AuthConfig(type="bearer", params={"token": "new-token"})),
        }
        pool.reload_vault(new_profiles)
        assert pool.active_count == 0
