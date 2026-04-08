"""Fetch module tests: HTML→markdown, smart truncation, cache helpers."""

import json
import os
import tempfile

import pytest
from mcp_research.fetch import html_to_markdown, smart_truncate, _get_cache_path, _write_cache, _read_cache


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
