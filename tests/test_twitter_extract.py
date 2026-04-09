"""Twitter/X.com extraction tests — URL validation, yt-dlp mock, API mock, error paths."""

import json
from unittest.mock import patch, MagicMock

from mcp_research.twitter import (
    extract_tweet, extract_thread,
    _TWEET_URL_RE, _PROFILE_URL_RE,
    _ytdlp_extract, _api_extract, _html_extract,
    _get_cookie_path, _get_bearer_token,
)


class TestURLValidation:

    def test_valid_x_url(self):
        assert _TWEET_URL_RE.match("https://x.com/user/status/1234567890")

    def test_valid_twitter_url(self):
        assert _TWEET_URL_RE.match("https://twitter.com/user/status/1234567890")

    def test_www_prefix(self):
        assert _TWEET_URL_RE.match("https://www.x.com/user/status/1234567890")

    def test_invalid_url(self):
        result = extract_tweet("https://example.com/page")
        assert "error" in result

    def test_empty_url(self):
        result = extract_tweet("")
        assert "error" in result

    def test_profile_url_regex(self):
        assert _PROFILE_URL_RE.match("https://x.com/elonmusk")


class TestYtdlpExtraction:

    @patch("mcp_research.twitter._check_ytdlp", return_value=None)
    def test_returns_none_without_ytdlp(self, mock_which):
        result = _ytdlp_extract("https://x.com/user/status/123")
        assert result is None

    @patch("mcp_research.twitter._get_cookie_path", return_value=None)
    @patch("mcp_research.twitter._check_ytdlp", return_value="/usr/bin/yt-dlp")
    @patch("mcp_research.twitter.subprocess.run")
    def test_extracts_tweet_data(self, mock_run, mock_ytdlp, mock_cookies):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "description": "Hello world tweet",
                "uploader": "Test User",
                "uploader_id": "testuser",
                "timestamp": 1700000000,
                "like_count": 42,
                "repost_count": 5,
                "formats": [],
            }),
        )
        result = _ytdlp_extract("https://x.com/testuser/status/123")
        assert result is not None
        assert result["text"] == "Hello world tweet"
        assert result["author"] == "Test User"
        assert result["metrics"]["likes"] == 42
        assert result["access_method"] == "yt-dlp"

    @patch("mcp_research.twitter._get_cookie_path", return_value="/path/to/cookies.txt")
    @patch("mcp_research.twitter._check_ytdlp", return_value="/usr/bin/yt-dlp")
    @patch("mcp_research.twitter.subprocess.run")
    def test_passes_cookies_to_ytdlp(self, mock_run, mock_ytdlp, mock_cookies):
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        _ytdlp_extract("https://x.com/user/status/123")
        cmd = mock_run.call_args[0][0]
        assert "--cookies" in cmd
        assert "/path/to/cookies.txt" in cmd


class TestAPIExtraction:

    @patch("mcp_research.twitter._get_bearer_token", return_value=None)
    def test_returns_none_without_token(self, mock_token):
        result = _api_extract("123")
        assert result is None

    @patch("mcp_research.twitter._get_bearer_token", return_value="test-bearer")
    @patch("mcp_research.twitter.requests.get")
    def test_extracts_via_api(self, mock_get, mock_token):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "data": {
                    "text": "API tweet text",
                    "author_id": "uid1",
                    "created_at": "2024-01-01T00:00:00Z",
                    "public_metrics": {"like_count": 10, "retweet_count": 3},
                },
                "includes": {
                    "users": [{"id": "uid1", "name": "API User", "username": "apiuser"}],
                },
            },
        )
        result = _api_extract("123")
        assert result is not None
        assert result["text"] == "API tweet text"
        assert result["access_method"] == "twitter_api_v2"


class TestExtractTweet:

    @patch("mcp_research.twitter._html_extract", return_value=None)
    @patch("mcp_research.twitter._api_extract", return_value=None)
    @patch("mcp_research.twitter._ytdlp_extract", return_value=None)
    def test_all_strategies_fail(self, mock_yt, mock_api, mock_html):
        result = extract_tweet("https://x.com/user/status/123")
        assert "error" in result

    @patch("mcp_research.twitter._ytdlp_extract")
    def test_ytdlp_success(self, mock_yt):
        mock_yt.return_value = {
            "text": "Tweet content",
            "author": "User",
            "access_method": "yt-dlp",
        }
        result = extract_tweet("https://x.com/user/status/123")
        assert result["text"] == "Tweet content"
        assert result["tweet_id"] == "123"


class TestCredentialLookup:

    @patch("mcp_research.vault.match_url")
    @patch("mcp_research.vault.get_vault")
    def test_get_cookie_path_from_vault(self, mock_vault, mock_match):
        """_get_cookie_path returns path when vault has cookie_jar for x.com."""
        from mcp_research.vault import VaultProfile, AuthConfig
        mock_vault.return_value = {}
        mock_match.return_value = VaultProfile(
            name="twitter", match="*.x.com/**",
            auth=AuthConfig(type="cookie_jar", params={"path": "/home/user/.cookies/twitter.txt"}),
        )
        result = _get_cookie_path()
        assert result == "/home/user/.cookies/twitter.txt"

    def test_get_cookie_path_no_vault(self):
        """_get_cookie_path returns None when vault is unavailable."""
        with patch("mcp_research.vault.get_vault", side_effect=Exception("no vault")):
            result = _get_cookie_path()
            assert result is None

    @patch("mcp_research.vault.match_url")
    @patch("mcp_research.vault.get_vault")
    def test_get_bearer_token_from_vault(self, mock_vault, mock_match):
        """_get_bearer_token returns token from vault for api.x.com."""
        from mcp_research.vault import VaultProfile, AuthConfig
        mock_vault.return_value = {}
        mock_match.return_value = VaultProfile(
            name="twitter-api", match="api.x.com/**",
            auth=AuthConfig(type="bearer", params={"token": "test-bearer-123"}),
        )
        result = _get_bearer_token()
        assert result == "test-bearer-123"

    def test_get_bearer_token_no_vault(self):
        """_get_bearer_token returns None when vault is unavailable."""
        with patch("mcp_research.vault.get_vault", side_effect=Exception("no vault")):
            result = _get_bearer_token()
            assert result is None


class TestExtractThread:

    @patch("mcp_research.twitter.extract_tweet")
    def test_returns_single_tweet_without_api(self, mock_extract):
        mock_extract.return_value = {
            "text": "Root tweet",
            "author": "User",
        }
        result = extract_thread("https://x.com/user/status/123")
        assert len(result["thread"]) == 1
        assert "note" in result
