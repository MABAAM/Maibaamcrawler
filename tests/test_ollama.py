"""Ollama module tests — graceful degradation when unavailable."""

from unittest.mock import patch
from mcp_research import ollama, config


class TestOllamaUnavailable:

    def test_disabled_when_url_empty(self):
        with patch.object(config, "OLLAMA_URL", ""):
            # Reset cache
            ollama._ollama_available_cache = None
            assert ollama.ollama_available() is False

    def test_query_returns_none_when_unavailable(self):
        with patch.object(config, "OLLAMA_URL", ""):
            ollama._ollama_available_cache = None
            result = ollama.ollama_query("test prompt")
            assert result is None

    def test_summarize_returns_none_when_unavailable(self):
        with patch.object(config, "OLLAMA_URL", ""):
            ollama._ollama_available_cache = None
            result = ollama.summarize_text("some text")
            assert result is None

    def test_rewrite_returns_none_when_unavailable(self):
        with patch.object(config, "OLLAMA_URL", ""):
            ollama._ollama_available_cache = None
            result = ollama.rewrite_query("some query")
            assert result is None

    def test_synthesize_returns_none_when_unavailable(self):
        with patch.object(config, "OLLAMA_URL", ""):
            ollama._ollama_available_cache = None
            result = ollama.synthesize("q", ["s1", "s2"])
            assert result is None
