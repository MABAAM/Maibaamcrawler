"""Deep Ingest tests."""

import os

from mcp_research.ingest import deep_ingest


class TestDirectoryIngest:

    def test_ingests_text_files(self, sample_dir):
        result = deep_ingest(str(sample_dir), include_types="text", max_files=50)
        assert result["files_processed"] >= 3  # hello.py, readme.md, data.csv, nested.js
        assert "text" in result["by_type"]

    def test_skips_node_modules(self, sample_dir):
        result = deep_ingest(str(sample_dir), include_types="text", max_files=50)
        files = [c["file"] for c in result["content"]]
        assert not any("node_modules" in f for f in files)

    def test_max_files_respected(self, sample_dir):
        result = deep_ingest(str(sample_dir), include_types="text", max_files=2)
        assert result["files_processed"] <= 2

    def test_empty_files_skipped(self, sample_dir):
        result = deep_ingest(str(sample_dir), include_types="text", max_files=50)
        files = [c["file"] for c in result["content"]]
        assert not any("empty" in f for f in files)

    def test_unsupported_files_skipped(self, sample_dir):
        result = deep_ingest(str(sample_dir), max_files=50)
        files = [c["file"] for c in result["content"]]
        assert not any("binary.bin" in f for f in files)


class TestSingleFileIngest:

    def test_single_text_file(self, sample_dir):
        result = deep_ingest(str(sample_dir / "hello.py"))
        assert result["files_processed"] == 1
        assert result["content"][0]["type"] == "text"
        assert "hello" in result["content"][0]["text"]

    def test_single_nonexistent_file(self):
        result = deep_ingest("/nonexistent/file.txt")
        assert "error" in result


class TestIncludeTypes:

    def test_filter_to_text_only(self, sample_dir):
        # Create a dummy .pdf (won't extract, but should be counted)
        (sample_dir / "test.pdf").write_bytes(b"%PDF-fake")
        result = deep_ingest(str(sample_dir), include_types="text", max_files=50)
        for c in result["content"]:
            assert c["type"] == "text"


class TestSymlinkEscape:

    def test_symlink_outside_root_skipped(self, sample_dir, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret data", encoding="utf-8")
        link = sample_dir / "escape"
        try:
            os.symlink(str(outside), str(link))
        except OSError:
            return  # Skip on Windows without symlink privileges
        result = deep_ingest(str(sample_dir), include_types="text", max_files=50)
        files = [c["file"] for c in result["content"]]
        assert not any("secret" in f for f in files)
