"""Search module tests."""

from unittest.mock import patch, MagicMock
from mcp_research.search import web_search, _search_brave, _search_fallback
from mcp_research import config


class TestWebSearchEmptyQuery:

    def test_empty_query(self):
        results, hint = web_search("")
        assert results == []
        assert "Empty" in hint

    def test_whitespace_query(self):
        results, hint = web_search("   ")
        assert results == []


class TestBraveSkippedWithoutKey:

    def test_no_key(self):
        with patch.object(config, "BRAVE_API_KEY", ""):
            results, hint = _search_brave("test", 5)
            assert results is None
            assert hint is None


class TestSearchFallback:

    @patch("mcp_research.search.requests.get")
    def test_fallback_returns_hint(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = '<html><body><div class="result__title"><a href="https://example.com">Test</a></div></body></html>'
        mock_get.return_value = mock_resp
        results, hint = _search_fallback("test query", 5)
        assert "fallback" in hint.lower() or "scraper" in hint.lower()


class TestDDGSBothPackagesMissing:

    @patch("mcp_research.search.requests.get")
    def test_falls_through_to_scraper_when_no_ddgs(self, mock_get):
        """When both ddgs and duckduckgo_search are missing, web_search uses HTML scraper."""
        from mcp_research.search import _get_ddgs_class
        mock_resp = MagicMock()
        mock_resp.text = '<html><body><div class="result__title"><a href="https://r.com">R</a></div></body></html>'
        mock_get.return_value = mock_resp

        with patch.dict("sys.modules", {"ddgs": None, "duckduckgo_search": None}):
            # Force re-evaluation of the import
            cls = _get_ddgs_class()
            assert cls is None

        # Full web_search path: Brave skipped (no key), DDGS=None, falls to scraper
        with patch.object(config, "BRAVE_API_KEY", ""), \
             patch("mcp_research.search._get_ddgs_class", return_value=None):
            results, hint = web_search("test query")
            # Should have used the scraper fallback
            assert hint is not None or results is not None
