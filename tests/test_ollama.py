"""Ollama module tests — graceful degradation, circuit breaker, vision."""

import time
from unittest.mock import patch, MagicMock

from mcp_research import ollama, config


def _reset_state():
    """Reset all module-level caches between tests."""
    ollama._ollama_available_cache = None
    ollama._ollama_available_ts = 0
    ollama._ollama_consecutive_failures = 0
    ollama._ollama_backoff_until = 0


class TestOllamaUnavailable:

    def test_disabled_when_url_empty(self):
        _reset_state()
        with patch.object(config, "OLLAMA_URL", ""):
            assert ollama.ollama_available() is False

    def test_query_returns_none_when_unavailable(self):
        _reset_state()
        with patch.object(config, "OLLAMA_URL", ""):
            result = ollama.ollama_query("test prompt")
            assert result is None

    def test_summarize_returns_none_when_unavailable(self):
        _reset_state()
        with patch.object(config, "OLLAMA_URL", ""):
            result = ollama.summarize_text("some text")
            assert result is None

    def test_rewrite_returns_none_when_unavailable(self):
        _reset_state()
        with patch.object(config, "OLLAMA_URL", ""):
            result = ollama.rewrite_query("some query")
            assert result is None

    def test_synthesize_returns_none_when_unavailable(self):
        _reset_state()
        with patch.object(config, "OLLAMA_URL", ""):
            result = ollama.synthesize("q", ["s1", "s2"])
            assert result is None


class TestCircuitBreaker:

    def test_first_failure_no_backoff(self):
        _reset_state()
        ollama._record_failure()
        assert ollama._ollama_consecutive_failures == 1
        assert ollama._ollama_backoff_until == 0

    def test_second_failure_triggers_backoff(self):
        _reset_state()
        ollama._record_failure()
        ollama._record_failure()
        assert ollama._ollama_consecutive_failures == 2
        assert ollama._ollama_backoff_until > time.time()

    def test_backoff_increases_exponentially(self):
        _reset_state()
        ollama._record_failure()  # 1 — no backoff
        ollama._record_failure()  # 2 — 60s
        t1 = ollama._ollama_backoff_until
        ollama._record_failure()  # 3 — 120s
        t2 = ollama._ollama_backoff_until
        # 3rd failure backoff should be larger than 2nd
        assert t2 > t1

    def test_backoff_capped_at_600(self):
        _reset_state()
        for _ in range(20):
            ollama._record_failure()
        # backoff = min(60 * 2^(n-2), 600) — should never exceed 600
        assert ollama._ollama_backoff_until <= time.time() + 601

    def test_reset_clears_state(self):
        _reset_state()
        ollama._record_failure()
        ollama._record_failure()
        assert ollama._ollama_consecutive_failures == 2
        ollama._reset_circuit_breaker()
        assert ollama._ollama_consecutive_failures == 0
        assert ollama._ollama_backoff_until == 0

    def test_available_returns_false_during_backoff(self):
        _reset_state()
        with patch.object(config, "OLLAMA_URL", "http://localhost:11434"):
            ollama._ollama_backoff_until = time.time() + 999
            assert ollama.ollama_available() is False

    @patch("mcp_research.ollama.requests.get")
    def test_successful_ping_resets_breaker(self, mock_get):
        _reset_state()
        mock_get.return_value = MagicMock(ok=True)
        ollama._ollama_consecutive_failures = 3
        with patch.object(config, "OLLAMA_URL", "http://localhost:11434"):
            result = ollama.ollama_available()
        assert result is True
        assert ollama._ollama_consecutive_failures == 0

    @patch("mcp_research.ollama.requests.get", side_effect=ConnectionError)
    def test_failed_ping_records_failure(self, mock_get):
        _reset_state()
        with patch.object(config, "OLLAMA_URL", "http://localhost:11434"):
            result = ollama.ollama_available()
        assert result is False
        assert ollama._ollama_consecutive_failures == 1


class TestDescribeImage:

    def test_returns_none_when_no_vision_model(self):
        with patch.object(config, "OLLAMA_VISION_MODEL", ""):
            result = ollama.ollama_describe_image("/fake/image.png")
            assert result is None

    def test_returns_none_on_missing_file(self):
        with patch.object(config, "OLLAMA_VISION_MODEL", "llava"):
            result = ollama.ollama_describe_image("/nonexistent/image.png")
            assert result is None
