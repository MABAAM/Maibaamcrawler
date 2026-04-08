"""Configuration via environment variables — no config files."""

import os
from pathlib import Path

# ── API Keys ─────────────────────────────────────────────────────────────────
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")

# ── Ollama ───────────────────────────────────────────────────────────────────
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")

# ── Cache ────────────────────────────────────────────────────────────────────
CACHE_DIR = Path(os.environ.get("MCP_RESEARCH_CACHE_DIR", Path.home() / ".mcp-research" / "cache"))
CACHE_TTL_HOURS = int(os.environ.get("MCP_RESEARCH_CACHE_TTL", "24"))

# ── Logs ─────────────────────────────────────────────────────────────────────
LOG_DIR = Path(os.environ.get("MCP_RESEARCH_LOG_DIR", Path.home() / ".mcp-research" / "logs"))

# ── Search defaults ──────────────────────────────────────────────────────────
MAX_RESULTS = int(os.environ.get("MCP_RESEARCH_MAX_RESULTS", "10"))

# ── Fetch constants ──────────────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

FETCH_TIMEOUT = 15
FETCH_MAX_RETRIES = 3
FETCH_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
FETCH_MD_MAX_CHARS = 50_000        # ~12K tokens

# ── Ensure dirs exist (owner-only permissions) ──────────────────────────────
CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
try:
    CACHE_DIR.chmod(0o700)
    LOG_DIR.chmod(0o700)
except OSError:
    pass  # Windows doesn't enforce POSIX permissions
