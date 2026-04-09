"""Credential vault tests — loading, env vars, URL matching, EZproxy, security."""

import os
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_research.vault import (
    load_vault, match_url, apply_auth, rewrite_ezproxy,
    _resolve_env_vars, _url_to_match_string,
    VaultProfile, AuthConfig, EZProxyConfig,
)


class TestEnvVarResolution:

    def test_resolves_known_var(self):
        with patch.dict(os.environ, {"MY_TOKEN": "secret123"}):
            assert _resolve_env_vars("${MY_TOKEN}") == "secret123"

    def test_missing_var_resolves_to_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            result = _resolve_env_vars("${NONEXISTENT_VAR_XYZ}")
            assert result == ""

    def test_multiple_vars(self):
        with patch.dict(os.environ, {"A": "hello", "B": "world"}):
            assert _resolve_env_vars("${A} ${B}") == "hello world"

    def test_no_vars_unchanged(self):
        assert _resolve_env_vars("plain text") == "plain text"

    def test_partial_resolution(self):
        with patch.dict(os.environ, {"FOUND": "yes"}):
            result = _resolve_env_vars("${FOUND} ${MISSING_VAR_XYZ}")
            assert result == "yes "


class TestLoadVault:

    def test_missing_file_returns_empty(self, tmp_path):
        result = load_vault(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_load_yaml(self, tmp_path):
        vault = tmp_path / "vault.yaml"
        vault.write_text("""
version: 1
profiles:
  test-profile:
    match: "*.example.com/**"
    auth:
      type: bearer
      token: "my-token"
""", encoding="utf-8")
        profiles = load_vault(vault)
        assert "test-profile" in profiles
        assert profiles["test-profile"].match == "*.example.com/**"
        assert profiles["test-profile"].auth.type == "bearer"
        assert profiles["test-profile"].auth.params["token"] == "my-token"

    def test_load_json_fallback(self, tmp_path):
        vault = tmp_path / "vault.json"
        vault.write_text(json.dumps({
            "version": 1,
            "profiles": {
                "json-profile": {
                    "match": "*.test.com/**",
                    "auth": {"type": "basic", "username": "user", "password": "pass"},
                }
            }
        }), encoding="utf-8")
        # Force JSON path by patching yaml import
        with patch.dict("sys.modules", {"yaml": None}):
            profiles = load_vault(vault)
            assert "json-profile" in profiles

    def test_env_var_in_vault(self, tmp_path):
        vault = tmp_path / "vault.yaml"
        vault.write_text("""
version: 1
profiles:
  env-profile:
    match: "*.example.com/**"
    auth:
      type: api_key
      header: "X-Key"
      value: "${TEST_API_KEY_XYZ}"
""", encoding="utf-8")
        with patch.dict(os.environ, {"TEST_API_KEY_XYZ": "resolved-key"}):
            profiles = load_vault(vault)
            assert profiles["env-profile"].auth.params["value"] == "resolved-key"

    def test_ezproxy_config(self, tmp_path):
        vault = tmp_path / "vault.yaml"
        vault.write_text("""
version: 1
profiles:
  uni:
    match: "*.ieee.org/**"
    ezproxy:
      base_url: "https://ezproxy.uni.edu/login?url="
      mode: prefix
""", encoding="utf-8")
        profiles = load_vault(vault)
        assert profiles["uni"].ezproxy.base_url == "https://ezproxy.uni.edu/login?url="
        assert profiles["uni"].ezproxy.mode == "prefix"

    def test_invalid_yaml(self, tmp_path):
        vault = tmp_path / "vault.yaml"
        vault.write_text("{{{{invalid yaml", encoding="utf-8")
        result = load_vault(vault)
        assert result == {}

    def test_wrong_version(self, tmp_path):
        vault = tmp_path / "vault.yaml"
        vault.write_text("version: 99\nprofiles: {}", encoding="utf-8")
        result = load_vault(vault)
        assert result == {}

    def test_missing_match_skipped(self, tmp_path):
        vault = tmp_path / "vault.yaml"
        vault.write_text("""
version: 1
profiles:
  no-match:
    auth:
      type: bearer
      token: "x"
""", encoding="utf-8")
        profiles = load_vault(vault)
        assert "no-match" not in profiles


class TestURLMatching:

    @pytest.fixture
    def profiles(self):
        return {
            "ieee": VaultProfile(name="ieee", match="*.ieee.org/**",
                                 auth=AuthConfig(type="bearer", params={"token": "x"})),
            "springer": VaultProfile(name="springer", match="link.springer.com/**",
                                     auth=AuthConfig(type="api_key", params={"header": "X-Key", "value": "y"})),
            "wildcard": VaultProfile(name="wildcard", match="*.example.com/**"),
        }

    def test_matches_subdomain(self, profiles):
        p = match_url("https://ieeexplore.ieee.org/document/123", profiles)
        assert p is not None
        assert p.name == "ieee"

    def test_matches_exact_host(self, profiles):
        p = match_url("https://link.springer.com/article/10.1007/s123", profiles)
        assert p is not None
        assert p.name == "springer"

    def test_no_match_returns_none(self, profiles):
        p = match_url("https://random-site.com/page", profiles)
        assert p is None

    def test_first_match_wins(self):
        profiles = {
            "specific": VaultProfile(name="specific", match="api.example.com/v2/**"),
            "general": VaultProfile(name="general", match="*.example.com/**"),
        }
        p = match_url("https://api.example.com/v2/data", profiles)
        assert p.name == "specific"

    def test_url_normalization(self):
        assert _url_to_match_string("https://example.com/path?q=1#frag") == "example.com/path"


class TestApplyAuth:

    def test_bearer(self):
        import requests
        session = requests.Session()
        profile = VaultProfile(name="t", match="*",
                               auth=AuthConfig(type="bearer", params={"token": "abc123"}))
        apply_auth(session, profile)
        assert session.headers["Authorization"] == "Bearer abc123"

    def test_basic(self):
        import requests
        session = requests.Session()
        profile = VaultProfile(name="t", match="*",
                               auth=AuthConfig(type="basic", params={"username": "u", "password": "p"}))
        apply_auth(session, profile)
        assert session.auth == ("u", "p")

    def test_api_key(self):
        import requests
        session = requests.Session()
        profile = VaultProfile(name="t", match="*",
                               auth=AuthConfig(type="api_key", params={"header": "X-Custom", "value": "key"}))
        apply_auth(session, profile)
        assert session.headers["X-Custom"] == "key"

    def test_custom_headers(self):
        import requests
        session = requests.Session()
        profile = VaultProfile(name="t", match="*",
                               auth=AuthConfig(type="headers", params={"X-A": "1", "X-B": "2"}))
        apply_auth(session, profile)
        assert session.headers["X-A"] == "1"
        assert session.headers["X-B"] == "2"

    def test_no_auth(self):
        import requests
        session = requests.Session()
        profile = VaultProfile(name="t", match="*")
        apply_auth(session, profile)  # should not crash


class TestEZProxyRewrite:

    def test_prefix_mode(self):
        ez = EZProxyConfig(base_url="https://ezproxy.uni.edu/login?url=", mode="prefix")
        result = rewrite_ezproxy("https://ieeexplore.ieee.org/doc/123", ez)
        assert result == "https://ezproxy.uni.edu/login?url=https://ieeexplore.ieee.org/doc/123"

    def test_suffix_mode(self):
        ez = EZProxyConfig(base_url="https://ezproxy.uni.edu/", mode="suffix")
        result = rewrite_ezproxy("https://ieeexplore.ieee.org/doc/123", ez)
        assert "ieeexplore-ieee-org" in result
        assert "ezproxy.uni.edu" in result


class TestReprRedaction:

    def test_vault_profile_repr_no_secrets(self):
        p = VaultProfile(name="test", match="*",
                         auth=AuthConfig(type="bearer", params={"token": "SUPER_SECRET"}))
        r = repr(p)
        assert "SUPER_SECRET" not in r
        assert "bearer" in r

    def test_auth_config_repr_redacted(self):
        a = AuthConfig(type="api_key", params={"value": "secret123"})
        r = repr(a)
        assert "secret123" not in r
        assert "REDACTED" in r


class TestCookieJarAuth:

    def test_cookie_jar_loads_file(self, tmp_path):
        """apply_auth with cookie_jar type loads a Netscape cookie file."""
        import requests
        cookie_file = tmp_path / "cookies.txt"
        # Minimal Netscape cookie jar format
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".example.com\tTRUE\t/\tFALSE\t0\tsession_id\tabc123\n",
            encoding="utf-8",
        )
        session = requests.Session()
        profile = VaultProfile(
            name="t", match="*",
            auth=AuthConfig(type="cookie_jar", params={"path": str(cookie_file)}),
        )
        apply_auth(session, profile)
        # Cookie should be loaded into session
        assert len(session.cookies) >= 1

    def test_cookie_jar_missing_file(self):
        """apply_auth with cookie_jar gracefully handles missing file."""
        import requests
        session = requests.Session()
        profile = VaultProfile(
            name="t", match="*",
            auth=AuthConfig(type="cookie_jar", params={"path": "/nonexistent/cookies.txt"}),
        )
        apply_auth(session, profile)  # should not crash
        assert len(session.cookies) == 0

    def test_unknown_auth_type_no_crash(self):
        """apply_auth with unknown type logs warning but doesn't crash."""
        import requests
        session = requests.Session()
        profile = VaultProfile(
            name="t", match="*",
            auth=AuthConfig(type="magic_token", params={"value": "x"}),
        )
        apply_auth(session, profile)  # should not crash


class TestEZProxySuffixEdgeCases:

    def test_hostname_with_many_dots(self):
        """Suffix mode replaces all dots in hostname."""
        ez = EZProxyConfig(base_url="https://proxy.uni.edu/", mode="suffix")
        result = rewrite_ezproxy("https://www.sub.ieeexplore.ieee.org/doc/123", ez)
        assert "www-sub-ieeexplore-ieee-org" in result
        assert "proxy.uni.edu" in result

    def test_preserves_path_and_query(self):
        """Suffix mode preserves the original path."""
        ez = EZProxyConfig(base_url="https://proxy.uni.edu/", mode="suffix")
        result = rewrite_ezproxy("https://ieeexplore.ieee.org/doc/123?view=full", ez)
        assert "/doc/123" in result
        assert "view=full" in result
