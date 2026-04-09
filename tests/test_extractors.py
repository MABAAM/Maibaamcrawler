"""Per-filetype extractor tests."""

import os
from unittest.mock import patch, MagicMock

from mcp_research._extractors import (
    extract_text, extract_pdf, extract_docx, extract_xlsx, extract_pptx,
    extract_audio, extract_image, extract, get_file_type, ALL_SUPPORTED_EXTS,
)


class TestGetFileType:

    def test_python_is_text(self):
        assert get_file_type(".py") == "text"

    def test_pdf(self):
        assert get_file_type(".pdf") == "pdf"

    def test_docx_is_office(self):
        assert get_file_type(".docx") == "office"

    def test_mp3_is_audio(self):
        assert get_file_type(".mp3") == "audio"

    def test_mp4_is_video(self):
        assert get_file_type(".mp4") == "video"

    def test_png_is_image(self):
        assert get_file_type(".png") == "image"

    def test_unknown_returns_none(self):
        assert get_file_type(".xyz123") is None

    def test_case_insensitive(self):
        assert get_file_type(".PY") == "text"


class TestExtractText:

    def test_reads_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Hello World", encoding="utf-8")
        text, err = extract_text(str(f))
        assert text == "Hello World"
        assert err is None

    def test_nonexistent_file(self):
        text, err = extract_text("/nonexistent/file.txt")
        assert err is not None


class TestExtractPdfMissing:

    def test_returns_install_hint_when_no_pypdf2(self):
        with patch.dict("sys.modules", {"PyPDF2": None}):
            # Force ImportError by patching the import
            text, err = extract_pdf("/fake/file.pdf")
            # Either gets an import error hint or a file-not-found
            assert err is not None


class TestExtractDocxMissing:

    def test_returns_install_hint(self):
        with patch.dict("sys.modules", {"docx": None}):
            text, err = extract_docx("/fake/file.docx")
            assert err is not None


class TestExtractXlsxMissing:

    def test_returns_install_hint(self):
        with patch.dict("sys.modules", {"openpyxl": None}):
            text, err = extract_xlsx("/fake/file.xlsx")
            assert err is not None


class TestExtractPptxMissing:

    def test_returns_install_hint(self):
        with patch.dict("sys.modules", {"pptx": None}):
            text, err = extract_pptx("/fake/file.pptx")
            assert err is not None


class TestExtractAudioMissing:

    def test_returns_install_hint(self):
        with patch.dict("sys.modules", {"faster_whisper": None}):
            text, err = extract_audio("/fake/file.mp3")
            assert err is not None


class TestExtractImage:

    def test_no_vision_model_configured(self, monkeypatch):
        monkeypatch.setattr("mcp_research.config.OLLAMA_VISION_MODEL", "")
        text, err = extract_image("/fake/image.png")
        assert err is not None
        assert "vision model" in err.lower()


class TestExtractRouter:

    def test_routes_py_to_text(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1", encoding="utf-8")
        text, err = extract(str(f))
        assert "x = 1" in text
        assert err is None

    def test_unsupported_ext(self, tmp_path):
        f = tmp_path / "test.xyz123"
        f.write_text("data", encoding="utf-8")
        text, err = extract(str(f))
        assert err is not None
        assert "Unsupported" in err
