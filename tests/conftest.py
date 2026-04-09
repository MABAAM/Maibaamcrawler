"""Shared test fixtures."""

import os
import tempfile

import pytest


@pytest.fixture
def tmp_cache_dir(tmp_path):
    """Temporary cache directory."""
    cache = tmp_path / "cache"
    cache.mkdir()
    return cache


@pytest.fixture
def sample_dir(tmp_path):
    """Temporary directory with sample files for ingest tests."""
    d = tmp_path / "samples"
    d.mkdir()
    (d / "hello.py").write_text("print('hello world')\n", encoding="utf-8")
    (d / "readme.md").write_text("# Test\n\nSome content here.\n", encoding="utf-8")
    (d / "data.csv").write_text("a,b,c\n1,2,3\n4,5,6\n", encoding="utf-8")
    (d / "empty.txt").write_text("", encoding="utf-8")
    (d / "binary.bin").write_bytes(b"\x00\x01\x02\x03")
    sub = d / "subdir"
    sub.mkdir()
    (sub / "nested.js").write_text("console.log('nested');\n", encoding="utf-8")
    skip = d / "node_modules"
    skip.mkdir()
    (skip / "junk.js").write_text("// should be skipped\n", encoding="utf-8")
    return d
