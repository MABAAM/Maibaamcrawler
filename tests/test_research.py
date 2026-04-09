"""Compound research pipeline tests."""

from unittest.mock import patch, MagicMock
import asyncio

from mcp_research.server import research


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestResearchPipeline:

    @patch("mcp_research.fetch.fetch_url")
    @patch("mcp_research.search.web_search")
    @patch("mcp_research.ollama.rewrite_query", return_value=None)
    def test_standard_depth_formats_output(self, mock_rewrite, mock_search, mock_fetch):
        mock_search.return_value = (
            [{"title": "Test", "url": "https://example.com", "snippet": "A test page"}],
            None,
        )
        mock_fetch.return_value = {
            "title": "Test", "url": "https://example.com",
            "content_md": "Test content", "content_length": 12,
            "summary": "A summary", "from_cache": False,
        }
        result = _run(research("test query", depth="standard"))
        assert "Research: test query" in result
        assert "Sources" in result

    @patch("mcp_research.search.web_search")
    @patch("mcp_research.ollama.rewrite_query", return_value=None)
    def test_no_results_message(self, mock_rewrite, mock_search):
        mock_search.return_value = ([], "No results")
        result = _run(research("obscure query"))
        assert "No results found" in result

    @patch("mcp_research.fetch.fetch_url")
    @patch("mcp_research.search.web_search")
    @patch("mcp_research.ollama.rewrite_query", return_value="rewritten query")
    def test_query_rewriting(self, mock_rewrite, mock_search, mock_fetch):
        mock_search.return_value = (
            [{"title": "Test", "url": "https://example.com", "snippet": "snippet"}],
            None,
        )
        mock_fetch.return_value = {
            "title": "Test", "url": "https://example.com",
            "content_md": "content", "content_length": 7,
            "summary": None, "from_cache": False,
        }
        result = _run(research("my question", depth="quick"))
        assert "rewritten query" in result
