"""Configuration via environment variables — no config files."""

import os
from pathlib import Path

# ── API Keys ─────────────────────────────────────────────────────────────────
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")

# ── Ollama ───────────────────────────────────────────────────────────────────
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "")  # e.g. "llava:13b"

# ── Cache ────────────────────────────────────────────────────────────────────
CACHE_DIR = Path(os.environ.get("MCP_RESEARCH_CACHE_DIR", Path.home() / ".mcp-research" / "cache"))
CACHE_TTL_HOURS = int(os.environ.get("MCP_RESEARCH_CACHE_TTL", "24"))
CACHE_MAX_SIZE_MB = int(os.environ.get("MCP_RESEARCH_CACHE_MAX_MB", "500"))

# ── Logs ─────────────────────────────────────────────────────────────────────
LOG_DIR = Path(os.environ.get("MCP_RESEARCH_LOG_DIR", Path.home() / ".mcp-research" / "logs"))

# ── Search defaults ──────────────────────────────────────────────────────────
MAX_RESULTS = int(os.environ.get("MCP_RESEARCH_MAX_RESULTS", "10"))

# ── YouTube / Whisper ────────────────────────────────────────────────────────
YOUTUBE_CACHE_DIR = Path(os.environ.get("MCP_RESEARCH_YT_CACHE", Path.home() / ".mcp-research" / "youtube"))
WHISPER_MODEL = os.environ.get("MCP_RESEARCH_WHISPER_MODEL", "base")
WHISPER_DEVICE = os.environ.get("MCP_RESEARCH_WHISPER_DEVICE", "auto")

# ── Credential Vault ────────────────────────────────────────────────────────
VAULT_FILE = Path(os.environ.get("MCP_RESEARCH_VAULT_FILE", Path.home() / ".mcp-research" / "vault.yaml"))
VAULT_HOT_RELOAD = os.environ.get("MCP_RESEARCH_VAULT_HOT_RELOAD", "true").lower() == "true"
SESSION_IDLE_TTL = int(os.environ.get("MCP_RESEARCH_SESSION_TTL", "1800"))

# ── Concurrency ──────────────────────────────────────────────────────────────
MAX_CONCURRENCY = int(os.environ.get("MCP_RESEARCH_MAX_CONCURRENCY", "10"))

# ── Fetch constants ──────────────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:138.0) Gecko/20100101 Firefox/138.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) Gecko/20100101 Firefox/138.0",
]

FETCH_TIMEOUT = 15
FETCH_MAX_RETRIES = 3
FETCH_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
FETCH_MD_MAX_CHARS = 50_000        # hard ceiling — tools use lower defaults
FETCH_DEFAULT_CHARS = 15_000       # ~4K tokens — default for fetch_url
RESEARCH_PER_SOURCE_CHARS = 2_000  # ~500 tokens — per source in research tool
ACADEMIC_FULLTEXT_CHARS = 10_000   # ~2.5K tokens — academic full text cap

# ── Ensure dirs exist (owner-only permissions) ──────────────────────────────
for _d in (CACHE_DIR, LOG_DIR, YOUTUBE_CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
    try:
        _d.chmod(0o700)
    except OSError:
        pass  # Windows doesn't enforce POSIX permissions
