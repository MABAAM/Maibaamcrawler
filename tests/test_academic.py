"""Academic module tests — identifier detection, publisher mapping, EZproxy, DOI resolution."""

from unittest.mock import patch, MagicMock

from mcp_research.academic import (
    detect_identifier, detect_publisher, is_doi, is_arxiv_id, is_pubmed_id,
    academic_lookup, resolve_doi, fetch_arxiv, resolve_pubmed,
)
from mcp_research.vault import EZProxyConfig, VaultProfile, AuthConfig, rewrite_ezproxy


class TestIdentifierDetection:

    def test_doi_bare(self):
        t, v = detect_identifier("10.1109/ACCESS.2024.1234567")
        assert t == "doi"
        assert v == "10.1109/ACCESS.2024.1234567"

    def test_doi_url(self):
        t, v = detect_identifier("https://doi.org/10.1109/ACCESS.2024.1234567")
        assert t == "doi"
        assert v == "10.1109/ACCESS.2024.1234567"

    def test_doi_dx_url(self):
        t, v = detect_identifier("https://dx.doi.org/10.1007/s123")
        assert t == "doi"

    def test_doi_prefix(self):
        t, v = detect_identifier("doi:10.1109/ACCESS.2024.1234567")
        assert t == "doi"

    def test_arxiv_bare(self):
        t, v = detect_identifier("2301.12345")
        assert t == "arxiv"
        assert v == "2301.12345"

    def test_arxiv_with_version(self):
        t, v = detect_identifier("2301.12345v2")
        assert t == "arxiv"
        assert v == "2301.12345v2"

    def test_arxiv_url(self):
        t, v = detect_identifier("https://arxiv.org/abs/2301.12345")
        assert t == "arxiv"
        assert v == "2301.12345"

    def test_arxiv_pdf_url(self):
        t, v = detect_identifier("https://arxiv.org/pdf/2301.12345.pdf")
        assert t == "arxiv"

    def test_pubmed_bare(self):
        t, v = detect_identifier("12345678")
        assert t == "pubmed"
        assert v == "12345678"

    def test_pubmed_prefix(self):
        t, v = detect_identifier("PMID:12345678")
        assert t == "pubmed"

    def test_pubmed_url(self):
        t, v = detect_identifier("https://pubmed.ncbi.nlm.nih.gov/12345678/")
        assert t == "pubmed"

    def test_url(self):
        t, v = detect_identifier("https://ieeexplore.ieee.org/document/123")
        assert t == "url"

    def test_unknown(self):
        t, v = detect_identifier("random text")
        assert t == "unknown"


class TestBooleanHelpers:

    def test_is_doi(self):
        assert is_doi("10.1109/ACCESS.2024.1234567")
        assert not is_doi("2301.12345")

    def test_is_arxiv(self):
        assert is_arxiv_id("2301.12345")
        assert not is_arxiv_id("10.1109/test")

    def test_is_pubmed(self):
        assert is_pubmed_id("12345678")
        assert not is_pubmed_id("2301.12345")


class TestPublisherDetection:

    def test_ieee(self):
        assert detect_publisher("https://ieeexplore.ieee.org/document/123") == "IEEE"

    def test_springer(self):
        assert detect_publisher("https://link.springer.com/article/10.1007/s123") == "Springer"

    def test_elsevier(self):
        assert detect_publisher("https://www.sciencedirect.com/science/article/pii/123") == "Elsevier"

    def test_acm(self):
        assert detect_publisher("https://dl.acm.org/doi/10.1145/123") == "ACM"

    def test_arxiv(self):
        assert detect_publisher("https://arxiv.org/abs/2301.12345") == "ArXiv"

    def test_nature(self):
        assert detect_publisher("https://www.nature.com/articles/s41586-024-123") == "Nature"

    def test_unknown(self):
        assert detect_publisher("https://random-site.com/page") is None


class TestEZProxyRewrite:

    def test_prefix(self):
        ez = EZProxyConfig(base_url="https://proxy.uni.edu/login?url=", mode="prefix")
        result = rewrite_ezproxy("https://ieeexplore.ieee.org/doc/123", ez)
        assert result == "https://proxy.uni.edu/login?url=https://ieeexplore.ieee.org/doc/123"

    def test_suffix(self):
        ez = EZProxyConfig(base_url="https://proxy.uni.edu/", mode="suffix")
        result = rewrite_ezproxy("https://ieeexplore.ieee.org/doc/123", ez)
        assert "ieeexplore-ieee-org" in result
        assert "proxy.uni.edu" in result


class TestAcademicLookup:

    def test_unknown_identifier(self):
        result = academic_lookup("gibberish text")
        assert "error" in result

    def test_url_with_publisher(self):
        result = academic_lookup("https://ieeexplore.ieee.org/document/123")
        assert result.get("publisher") == "IEEE"

    def test_url_without_publisher(self):
        result = academic_lookup("https://random-site.com/page")
        assert "error" in result

    @patch("mcp_research.academic._crossref_metadata")
    def test_doi_metadata_only(self, mock_crossref):
        mock_crossref.return_value = {
            "doi": "10.1109/TEST.2024.123",
            "title": "Test Paper",
            "authors": ["Alice", "Bob"],
            "journal": "Test Journal",
            "year": "2024",
            "abstract": "Test abstract",
            "publisher": "IEEE",
            "url": "",
            "type": "journal-article",
        }
        result = academic_lookup("10.1109/TEST.2024.123", fetch_fulltext=False)
        assert result["title"] == "Test Paper"
        assert result["authors"] == ["Alice", "Bob"]
        assert "error" not in result

    @patch("mcp_research.academic._crossref_metadata", return_value=None)
    def test_doi_not_found(self, mock_crossref):
        result = academic_lookup("10.9999/FAKE.2024.000")
        assert "error" in result


class TestResolveDOIFullText:

    @patch("mcp_research.academic._resolve_doi_url", return_value="https://ieeexplore.ieee.org/doc/123")
    @patch("mcp_research.academic._crossref_metadata")
    def test_fulltext_html_via_vault(self, mock_crossref, mock_resolve):
        """resolve_doi fetches full text HTML via vault session."""
        mock_crossref.return_value = {
            "doi": "10.1109/TEST.2024.1", "title": "Test", "authors": ["Alice"],
            "journal": "J", "year": "2024", "abstract": "", "publisher": "IEEE",
            "url": "", "type": "journal-article",
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.text = "<html><body><p>Full paper content here.</p></body></html>"

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_pool = MagicMock()
        mock_pool.get_session.return_value = mock_session

        with patch("mcp_research.sessions.get_pool", return_value=mock_pool), \
             patch("mcp_research.vault.get_vault", return_value={}), \
             patch("mcp_research.vault.match_url", return_value=None):
            result = resolve_doi("10.1109/TEST.2024.1", fetch_fulltext=True)

        assert "full_text_md" in result
        assert "Full paper content" in result["full_text_md"]

    @patch("mcp_research.academic._resolve_doi_url", return_value="https://ieeexplore.ieee.org/doc/123")
    @patch("mcp_research.academic._crossref_metadata")
    def test_fulltext_access_denied(self, mock_crossref, mock_resolve):
        """resolve_doi reports access_error on 403."""
        mock_crossref.return_value = {
            "doi": "10.1109/TEST.2024.1", "title": "Test", "authors": [],
            "journal": "J", "year": "2024", "abstract": "", "publisher": "IEEE",
            "url": "", "type": "journal-article",
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.headers = {"Content-Type": "text/html"}

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_pool = MagicMock()
        mock_pool.get_session.return_value = mock_session

        with patch("mcp_research.sessions.get_pool", return_value=mock_pool), \
             patch("mcp_research.vault.get_vault", return_value={}), \
             patch("mcp_research.vault.match_url", return_value=None):
            result = resolve_doi("10.1109/TEST.2024.1", fetch_fulltext=True)

        assert "access_error" in result
        assert "403" in result["access_error"]

    @patch("mcp_research.academic._resolve_doi_url", return_value=None)
    @patch("mcp_research.academic._crossref_metadata")
    def test_no_publisher_url_returns_metadata_only(self, mock_crossref, mock_resolve):
        """resolve_doi returns metadata_only when no publisher URL found."""
        mock_crossref.return_value = {
            "doi": "10.1109/TEST.2024.1", "title": "Test", "authors": [],
            "journal": "J", "year": "2024", "abstract": "", "publisher": "",
            "url": "", "type": "journal-article",
        }
        result = resolve_doi("10.1109/TEST.2024.1", fetch_fulltext=True)
        assert result["access_method"] == "metadata_only"
        assert "full_text_md" not in result

    @patch("mcp_research.academic._resolve_doi_url", return_value="https://ieeexplore.ieee.org/doc/123")
    @patch("mcp_research.academic._crossref_metadata")
    def test_fulltext_with_ezproxy_rewrite(self, mock_crossref, mock_resolve):
        """resolve_doi rewrites URL via EZproxy when vault profile matches."""
        mock_crossref.return_value = {
            "doi": "10.1109/TEST.2024.1", "title": "Test", "authors": [],
            "journal": "J", "year": "2024", "abstract": "", "publisher": "IEEE",
            "url": "", "type": "journal-article",
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.text = "<p>Paper via proxy</p>"

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_pool = MagicMock()
        mock_pool.get_session.return_value = mock_session

        ez_profile = VaultProfile(
            name="uni-ieee", match="*.ieee.org/**",
            ezproxy=EZProxyConfig(base_url="https://ezproxy.uni.edu/login?url=", mode="prefix"),
        )

        with patch("mcp_research.sessions.get_pool", return_value=mock_pool), \
             patch("mcp_research.vault.get_vault", return_value={"uni-ieee": ez_profile}), \
             patch("mcp_research.vault.match_url", return_value=ez_profile):
            result = resolve_doi("10.1109/TEST.2024.1", fetch_fulltext=True)

        assert "ezproxy" in result["access_method"]
        # Verify session.get was called with the rewritten URL
        call_url = mock_session.get.call_args[0][0]
        assert "ezproxy.uni.edu" in call_url
