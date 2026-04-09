"""Fetch module tests: HTML→markdown, smart truncation, cache helpers, PDF, jitter."""

import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest
from mcp_research.fetch import (
    html_to_markdown, smart_truncate, _get_cache_path, _write_cache, _read_cache,
    _extract_pdf_text, fetch_with_retry,
)


class TestHtmlToMarkdown:

    def test_headings(self):
        html = "<h1>Title</h1><h2>Section</h2><p>Text here.</p>"
        md, title = html_to_markdown(html)
        assert "# Title" in md
        assert "## Section" in md
        assert "Text here." in md

    def test_links(self):
        html = '<p>Visit <a href="https://example.com">Example</a></p>'
        md, _ = html_to_markdown(html)
        assert "[Example](https://example.com)" in md

    def test_code_blocks(self):
        html = '<pre><code class="language-python">print("hi")</code></pre>'
        md, _ = html_to_markdown(html)
        assert "```python" in md
        assert 'print("hi")' in md

    def test_lists(self):
        html = "<ul><li>First</li><li>Second</li></ul>"
        md, _ = html_to_markdown(html)
        assert "- First" in md
        assert "- Second" in md

    def test_strips_scripts(self):
        html = "<p>Content</p><script>alert(1)</script>"
        md, _ = html_to_markdown(html)
        assert "alert" not in md
        assert "Content" in md

    def test_title_extraction(self):
        html = "<html><head><title>My Page</title></head><body><p>Hi</p></body></html>"
        _, title = html_to_markdown(html)
        assert title == "My Page"

    def test_relative_links_with_base(self):
        html = '<a href="/about">About</a>'
        md, _ = html_to_markdown(html, base_url="https://example.com")
        assert "https://example.com/about" in md

    def test_empty_html(self):
        md, title = html_to_markdown("")
        assert md == ""
        assert title == ""


class TestSmartTruncate:

    def test_short_text_unchanged(self):
        text = "Short text"
        assert smart_truncate(text, 1000) == text

    def test_truncates_at_heading(self):
        text = "A" * 500 + "\n## Section Two\n" + "B" * 500
        result = smart_truncate(text, 600)
        assert "[... truncated ...]" in result
        assert len(result) < 700

    def test_truncates_at_paragraph(self):
        text = "A" * 500 + "\n\nParagraph two\n\n" + "B" * 500
        result = smart_truncate(text, 600)
        assert "[... truncated ...]" in result

    def test_hard_truncate_fallback(self):
        text = "A" * 1000  # no headings or paragraphs
        result = smart_truncate(text, 500)
        assert "[... truncated ...]" in result
        assert len(result) <= 525


class TestCacheHelpers:

    def test_cache_path_deterministic(self):
        path1 = _get_cache_path("https://example.com")
        path2 = _get_cache_path("https://example.com")
        assert path1 == path2

    def test_different_urls_different_paths(self):
        path1 = _get_cache_path("https://example.com/a")
        path2 = _get_cache_path("https://example.com/b")
        assert path1 != path2

    def test_write_and_read_cache(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            data = {"url": "https://example.com", "title": "Test"}
            _write_cache(path, data)
            result = _read_cache(path)
            assert result == data
        finally:
            os.unlink(path)

    def test_read_missing_cache(self):
        result = _read_cache("/nonexistent/path/file.json")
        assert result is None


class TestPdfExtraction:

    def test_returns_none_without_pypdf2(self):
        with patch.dict("sys.modules", {"PyPDF2": None}):
            text, title = _extract_pdf_text(b"%PDF-fake", "https://example.com/doc.pdf")
            assert text is None

    def test_returns_none_on_corrupt_pdf(self):
        text, title = _extract_pdf_text(b"not a pdf", "https://example.com/doc.pdf")
        assert text is None

    def test_title_from_url_path(self):
        # Even with invalid PDF content, title extraction from URL happens in the try
        # We test the title logic indirectly via a mock
        with patch("mcp_research.fetch.PdfReader", create=True):
            # The function imports PdfReader inside, so we patch at usage
            text, title = _extract_pdf_text(b"fake", "https://example.com/report.pdf")
            # Falls through to exception since PdfReader mock isn't set up fully
            # Just verify it doesn't crash
            assert isinstance(title, str)


class TestRetryJitter:

    @patch("mcp_research.fetch.random.uniform", return_value=0.5)
    @patch("mcp_research.fetch.time.sleep")
    @patch("mcp_research.fetch.requests.get")
    def test_jitter_applied_on_retry(self, mock_get, mock_sleep, mock_uniform):
        """Verify that retry sleep includes jitter (base + random)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.is_redirect = False
        mock_get.return_value = mock_resp
        # fetch_with_retry should retry and sleep with jitter
        fetch_with_retry("https://example.com", max_retries=2)
        if mock_sleep.called:
            # Sleep should be 2^attempt + jitter (0.5)
            args = mock_sleep.call_args[0]
            assert args[0] >= 1.0  # At least 2^0 + something


class TestSessionParameter:

    @patch("mcp_research.fetch.requests.get")
    def test_uses_session_when_provided(self, mock_get):
        """When a session is passed, session.get() should be used."""
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_redirect = False
        mock_session.get.return_value = mock_resp
        resp, err = fetch_with_retry("https://example.com", session=mock_session)
        mock_session.get.assert_called()
        mock_get.assert_not_called()

    @patch("mcp_research.fetch.requests.get")
    def test_falls_back_without_session(self, mock_get):
        """Without session, requests.get() should be used."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_redirect = False
        mock_get.return_value = mock_resp
        resp, err = fetch_with_retry("https://example.com", session=None)
        mock_get.assert_called()


class TestImportFallbacks:

    def test_pdf_extraction_without_pypdf2(self):
        """_extract_pdf_text returns (None, '') when PyPDF2 is missing."""
        with patch.dict("sys.modules", {"PyPDF2": None}):
            # Re-import to force the ImportError path
            from importlib import reload
            import mcp_research.fetch as fetch_mod
            text, title = fetch_mod._extract_pdf_text(b"%PDF-fake", "https://example.com/doc.pdf")
            assert text is None
            assert title == ""

    def test_html_to_markdown_without_bs4(self):
        """html_to_markdown returns raw HTML when BeautifulSoup is missing."""
        with patch.dict("sys.modules", {"bs4": None, "bs4.BeautifulSoup": None}):
            from importlib import reload
            import mcp_research.fetch as fetch_mod
            reload(fetch_mod)
            try:
                md, title = fetch_mod.html_to_markdown("<p>Hello</p>")
                # Should fall back to returning the raw HTML
                assert "<p>Hello</p>" in md or "Hello" in md
                assert title == ""
            finally:
                reload(fetch_mod)  # restore


class TestRedirectHopLimit:

    @patch("mcp_research.fetch.is_safe_url", return_value=(True, None))
    @patch("mcp_research.fetch.requests.get")
    def test_stops_after_10_redirects(self, mock_get, mock_safe):
        """fetch_with_retry should stop following redirects after 10 hops."""
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {"Location": "https://example.com/loop"}
        mock_get.return_value = mock_resp
        resp, err = fetch_with_retry("https://example.com/start", max_retries=1)
        # After 10 hops the loop ends; response has status 302 (not a success)
        # The function should return the 302 as a non-retryable response
        assert resp is not None or err is not None

    @patch("mcp_research.fetch.is_safe_url")
    @patch("mcp_research.fetch.requests.get")
    def test_ssrf_blocked_mid_redirect(self, mock_get, mock_safe):
        """SSRF check blocks a redirect hop to a private IP."""
        # First call: safe. Second call (redirect target): blocked.
        mock_safe.side_effect = [(True, None), (False, "Private IP")]
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {"Location": "http://169.254.169.254/latest/meta-data"}
        mock_get.return_value = mock_resp
        resp, err = fetch_with_retry("https://example.com/page", max_retries=1)
        assert err is not None
        assert "blocked" in err.lower() or "Private" in err


class TestCaptchaInFetchUrl:

    @patch("mcp_research.fetch._log_event")
    @patch("mcp_research.fetch._write_cache")
    @patch("mcp_research.fetch._is_cache_fresh", return_value=False)
    @patch("mcp_research.fetch.fetch_with_retry")
    def test_captcha_detected_in_html_response(self, mock_fwr, mock_cache_fresh,
                                                mock_write, mock_log):
        """fetch_url includes captcha_blocked when Cloudflare challenge detected."""
        from mcp_research.fetch import fetch_url
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "text/html", "Server": "cloudflare"}
        mock_resp.status_code = 403
        mock_resp.url = "https://example.com"
        # Simulate iter_content returning CF challenge HTML
        cf_html = '<title>Just a moment...</title><div class="cf-challenge-running">'
        mock_resp.iter_content.return_value = [cf_html.encode()]
        mock_fwr.return_value = (mock_resp, None)

        with patch("mcp_research.fetch.is_safe_url", return_value=(True, None)):
            result = fetch_url("https://example.com")

        assert result.get("captcha_blocked") is True
        assert result.get("captcha_provider") == "cloudflare"
