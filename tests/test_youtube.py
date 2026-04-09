"""YouTube Essence Extractor tests."""

import json
from unittest.mock import patch

from mcp_research.youtube import youtube_essence, _parse_vtt_transcript, _extract_json_array, _YT_URL_RE


class TestURLValidation:

    def test_valid_watch_url(self):
        assert _YT_URL_RE.match("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_valid_short_url(self):
        assert _YT_URL_RE.match("https://youtu.be/dQw4w9WgXcQ")

    def test_valid_shorts_url(self):
        assert _YT_URL_RE.match("https://youtube.com/shorts/dQw4w9WgXcQ")

    def test_invalid_url(self):
        result = youtube_essence("https://example.com/video", mode="quick")
        assert "error" in result

    def test_empty_url(self):
        result = youtube_essence("", mode="quick")
        assert "error" in result


class TestVTTParsing:

    def test_basic_vtt(self, tmp_path):
        vtt = tmp_path / "test.vtt"
        vtt.write_text(
            "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nHello world\n\n"
            "2\n00:00:03.000 --> 00:00:04.000\nSecond line\n",
            encoding="utf-8",
        )
        assert _parse_vtt_transcript(str(vtt)) == "Hello world Second line"

    def test_deduplicates(self, tmp_path):
        vtt = tmp_path / "dup.vtt"
        vtt.write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n\n"
            "00:00:02.000 --> 00:00:03.000\nHello\n\n"
            "00:00:03.000 --> 00:00:04.000\nWorld\n",
            encoding="utf-8",
        )
        assert _parse_vtt_transcript(str(vtt)) == "Hello World"

    def test_strips_html_tags(self, tmp_path):
        vtt = tmp_path / "tags.vtt"
        vtt.write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<b>Bold</b> text\n",
            encoding="utf-8",
        )
        assert _parse_vtt_transcript(str(vtt)) == "Bold text"


class TestJSONExtraction:

    def test_plain_array(self):
        assert _extract_json_array('["a", "b"]') == ["a", "b"]

    def test_fenced_json(self):
        assert _extract_json_array('```json\n["a", "b"]\n```') == ["a", "b"]

    def test_non_array_returns_empty(self):
        assert _extract_json_array('{"key": "value"}') == []


class TestYtdlpMissing:

    @patch("mcp_research.youtube.shutil.which", return_value=None)
    def test_returns_install_hint(self, mock_which):
        result = youtube_essence("https://youtube.com/watch?v=dQw4w9WgXcQ", mode="quick")
        assert "error" in result
        assert "yt-dlp" in result["error"]


class TestCacheHit:

    def test_cache_hit_returns_from_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr("mcp_research.config.YOUTUBE_CACHE_DIR", tmp_path)
        cache_file = tmp_path / "dQw4w9WgXcQ.json"
        cache_data = {"title": "cached", "mode": "standard", "summary": "cached summary"}
        cache_file.write_text(json.dumps(cache_data), encoding="utf-8")

        result = youtube_essence("https://youtube.com/watch?v=dQw4w9WgXcQ", mode="quick")
        assert result.get("from_cache") is True
        assert result["title"] == "cached"
