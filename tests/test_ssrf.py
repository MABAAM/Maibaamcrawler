"""SSRF guard tests — the most security-critical component."""

import pytest
from mcp_research.fetch import is_safe_url


class TestSSRFBlocked:
    """URLs that MUST be rejected."""

    @pytest.mark.parametrize("url,label", [
        ("http://127.0.0.1/", "IPv4 loopback"),
        ("http://127.0.0.1:8080/secret", "IPv4 loopback with port"),
        ("http://localhost/admin", "localhost name"),
        ("http://localhost:3000/", "localhost with port"),
        ("http://0.0.0.0/", "all-interfaces"),
        ("http://[::1]/", "IPv6 loopback"),
        ("http://192.168.1.1/", "private 192.168.x"),
        ("http://10.0.0.1/", "private 10.x"),
        ("http://172.16.0.1/", "private 172.16.x"),
        ("ftp://example.com/file", "ftp scheme"),
        ("file:///etc/passwd", "file scheme"),
        ("gopher://evil.com/", "gopher scheme"),
        ("http://", "empty hostname"),
        ("http://myrouter.local/", ".local domain"),
    ])
    def test_blocked(self, url, label):
        safe, reason = is_safe_url(url)
        assert not safe, f"Expected {label} ({url}) to be blocked, got safe=True"
        assert reason is not None


class TestSSRFAllowed:
    """URLs that MUST be allowed."""

    @pytest.mark.parametrize("url,label", [
        ("https://example.com", "public HTTPS"),
        ("http://example.com", "public HTTP"),
        ("https://docs.python.org/3/library/asyncio.html", "docs site"),
        ("https://api.github.com/repos", "API endpoint"),
        ("https://en.wikipedia.org/wiki/Python", "Wikipedia"),
    ])
    def test_allowed(self, url, label):
        safe, reason = is_safe_url(url)
        assert safe, f"Expected {label} ({url}) to be allowed, got reason={reason}"


class TestSSRFEdgeCases:
    """Tricky bypass attempts."""

    def test_no_scheme(self):
        safe, _ = is_safe_url("example.com")
        assert not safe  # no scheme = not http/https

    def test_javascript_scheme(self):
        safe, _ = is_safe_url("javascript:alert(1)")
        assert not safe

    def test_data_scheme(self):
        safe, _ = is_safe_url("data:text/html,<h1>hi</h1>")
        assert not safe

    def test_invalid_url(self):
        safe, _ = is_safe_url("")
        assert not safe
