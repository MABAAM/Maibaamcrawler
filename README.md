# mcp-research

<!-- mcp-name: io.github.mabaam/mcp-research -->

MCP server for web research, academic papers, Twitter/X, YouTube, and file ingestion. Eight tools for AI assistants — all via the MCP stdio protocol. Includes credential vault for institutional access, CAPTCHA detection, and token-efficient output.

## Tools

| Tool | Description |
|------|-------------|
| `web_search` | 3-tier search cascade: Brave API → DuckDuckGo → HTML scraper |
| `fetch_url` | Fetch any URL → clean markdown, with SSRF protection and 24h cache |
| `research` | Compound pipeline: query rewrite → search → parallel fetch → summarize → synthesize |
| `youtube_essence` | YouTube video → transcript, summary, key points, chapters, quotes |
| `deep_ingest` | Extract text from files: PDF, DOCX, XLSX, PPTX, audio, video, images |
| `academic_lookup` | Resolve DOI / ArXiv / PubMed → metadata + full text via institutional access |
| `twitter_extract` | Extract tweets and threads from X.com/Twitter |
| `vault_status` | Show loaded credential profiles and dependency status (never exposes secrets) |

All tools are **read-only** — they fetch and transform content, never modify anything.

## Install

```bash
pip install mcp-research
```

Or run directly with `uvx` (zero-install):

```bash
uvx mcp-research
```

Optional extras:

```bash
pip install 'mcp-research[twitter]'    # yt-dlp for Twitter extraction
pip install 'mcp-research[youtube]'    # yt-dlp + faster-whisper for YouTube
pip install 'mcp-research[academic]'   # PyPDF2 for academic PDFs
pip install 'mcp-research[ingest]'     # PDF, DOCX, XLSX, PPTX, audio support
pip install 'mcp-research[all]'        # everything
```

Check your setup:

```bash
mcp-research doctor
```

## Usage with Claude Code

Add to your Claude Code MCP config (`~/.claude/settings.json` or project `.mcp.json`):

```json
{
  "mcpServers": {
    "research": {
      "command": "uvx",
      "args": ["mcp-research"],
      "env": {
        "BRAVE_API_KEY": "BSA...",
        "OLLAMA_URL": "http://localhost:11434"
      }
    }
  }
}
```

## Usage with Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "research": {
      "command": "uvx",
      "args": ["mcp-research"],
      "env": {
        "BRAVE_API_KEY": "BSA..."
      }
    }
  }
}
```

## Configuration

All configuration is via environment variables — no config files needed (except the optional vault).

| Variable | Default | Description |
|----------|---------|-------------|
| `BRAVE_API_KEY` | *(empty)* | Brave Search API key. Falls back to DuckDuckGo if unset. |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint for summarization/synthesis. Set empty to disable. |
| `OLLAMA_MODEL` | `qwen2.5:14b` | Model to use for summarization and synthesis. |
| `MCP_RESEARCH_CACHE_DIR` | `~/.mcp-research/cache/` | URL fetch cache directory. |
| `MCP_RESEARCH_CACHE_TTL` | `24` | Cache TTL in hours. |
| `MCP_RESEARCH_LOG_DIR` | `~/.mcp-research/logs/` | Search log directory (NDJSON). |
| `MCP_RESEARCH_MAX_RESULTS` | `10` | Default max search results. |
| `MCP_RESEARCH_VAULT_FILE` | `~/.mcp-research/vault.yaml` | Credential vault file path. |
| `MCP_RESEARCH_VAULT_HOT_RELOAD` | `true` | Auto-reload vault when file changes. |
| `MCP_RESEARCH_SESSION_TTL` | `1800` | Session idle timeout in seconds. |

## Tool Details

### `web_search`

```
web_search(query, max_results=5, summarize=False, auto_fetch_top=False)
```

Searches the web using a 3-tier cascade for maximum reliability:
1. **Brave Search API** — fast, high quality (requires `BRAVE_API_KEY`)
2. **DuckDuckGo library** — no API key needed, retries on rate limit
3. **DuckDuckGo HTML scraper** — last-resort fallback

Options:
- `summarize`: Use Ollama to summarize results (requires running Ollama)
- `auto_fetch_top`: Also fetch and return the full content of the top result

### `fetch_url`

```
fetch_url(url, summarize=False, max_chars=15000)
```

Fetches a URL and converts it to clean markdown:
- **SSRF protection**: Blocks localhost, private IPs, non-HTTP schemes
- **Smart retry**: Exponential backoff on 429/5xx, per-hop redirect validation
- **24h cache**: SHA-256 keyed, configurable TTL
- **Content support**: HTML → markdown, JSON → code block, binary → rejected
- **Smart truncation**: Breaks at heading/paragraph boundaries, not mid-text
- **CAPTCHA detection**: Flags Cloudflare, hCaptcha, reCAPTCHA, Akamai walls
- **Token-efficient**: Default 15K chars (~4K tokens), adjustable via `max_chars`

### `research`

```
research(query, depth="standard", context="")
```

Compound research pipeline:
1. **Query rewrite** — Ollama optimizes your question into search keywords
2. **Web search** — finds relevant pages (with zero-result retry expansion)
3. **Parallel fetch** — fetches top N pages concurrently
4. **Summarize** — Ollama summarizes each page
5. **Synthesize** — Ollama produces a final cited answer

Depth levels:
| Depth | Pages | Synthesis |
|-------|-------|-----------|
| `quick` | 2 | No |
| `standard` | 5 | Yes |
| `deep` | 10 | Yes |

All steps gracefully degrade without Ollama — you still get search results and page content.

### `youtube_essence`

```
youtube_essence(url, mode="standard")
```

Extracts structured content from YouTube videos:
- **Transcript**: Auto-subtitles or Whisper transcription (local, private)
- **Summary**: AI summary via Ollama
- **Key points**: Bullet-point takeaways
- **Chapters**: Timestamped segments
- **Quotes**: Notable quotations (deep mode)

Modes: `quick` (TL;DR), `standard` (+ chapters), `deep` (+ quotes)

Requires `yt-dlp`. Optional: `faster-whisper` for audio-only videos, `ffmpeg` for media extraction.

### `deep_ingest`

```
deep_ingest(path, include_types="", max_files=200, summarize=False)
```

Extracts text from files in a directory or single file:
- **Text files**: `.txt`, `.md`, `.json`, `.csv`, source code, etc.
- **PDF**: Via PyPDF2 (optional dependency)
- **Office**: `.docx`, `.xlsx`, `.pptx` (optional dependencies)
- **Audio/Video**: Whisper transcription (optional)
- **Images**: OCR via Ollama vision model (optional)

Type filter: `text`, `pdf`, `audio`, `video`, `image`, `office`

### `academic_lookup`

```
academic_lookup(identifier, fetch_fulltext=True)
```

Resolves academic papers from multiple identifier types:
- **DOI**: `10.xxxx/...` → Crossref metadata + publisher redirect
- **ArXiv**: `2301.12345` → abstract + PDF
- **PubMed**: PMID → E-utilities metadata → DOI chain
- **URL**: Publisher page detection

Full text access via credential vault:
- EZproxy rewriting (prefix and suffix modes)
- Bearer token, API key, basic auth, cookie jar
- Automatic publisher detection (IEEE, Springer, Elsevier, ACM, Wiley, Nature, JSTOR, etc.)

### `twitter_extract`

```
twitter_extract(url, include_thread=False)
```

Extracts tweets and threads from X.com/Twitter using a strategy cascade:
1. **yt-dlp** (primary) — works with cookie jar for authenticated access
2. **Twitter API v2** — if bearer token configured in vault
3. **HTML fetch** — cookie-based last resort

Returns: text, author, timestamp, metrics (likes, retweets, replies), media URLs.

### `vault_status`

```
vault_status()
```

Shows loaded credential profiles, match patterns, and auth types — **never exposes secrets**. Also checks availability of optional dependencies.

## Credential Vault

Create `~/.mcp-research/vault.yaml` to configure authentication for protected sources:

```yaml
version: 1
profiles:
  # University EZproxy for IEEE
  ieee-university:
    match: "*.ieee.org/**"
    ezproxy:
      base_url: "https://ezproxy.myuniversity.edu/login?url="
      mode: prefix

  # Springer via API key
  springer:
    match: "*.springer.com/**"
    auth:
      type: api_key
      header: "X-ApiKey"
      value: "${SPRINGER_API_KEY}"

  # X.com via browser cookies
  twitter:
    match: "*.x.com/**"
    auth:
      type: cookie_jar
      path: "${HOME}/.mcp-research/cookies/twitter.txt"
```

- `${VAR}` resolved from environment variables — secrets never stored in plain text
- First matching profile wins (order matters)
- Auth types: `bearer`, `basic`, `api_key`, `cookie_jar`, `headers`
- EZproxy modes: `prefix` (prepend base URL) or `suffix` (domain rewriting)
- Hot-reload: vault file changes are picked up automatically

## Token Efficiency

All tools produce compact output by default to avoid wasting AI context window tokens:

| Tool | Default output | Override |
|------|---------------|----------|
| `fetch_url` | ~15K chars (~4K tokens) | `max_chars` parameter |
| `research` | ~500 tokens per source | Prefers summaries over raw content |
| `academic_lookup` | ~10K chars full text | Truncates with notice |
| `deep_ingest` | 15 files, 300 char excerpts | `max_files` parameter |
| `youtube_essence` | 3K char transcript excerpt | Full transcript in result object |

## Safety & Robustness

- **SSRF protection**: Blocks localhost, private IPs, link-local, non-HTTP schemes on every hop
- **CAPTCHA detection**: Identifies Cloudflare, hCaptcha, reCAPTCHA, Akamai, DDoS-Guard walls
- **Input validation**: Size limits, URL validation, safe redirect following
- **No eval/exec**: No dynamic code execution
- **Vault security**: Secrets resolved from env vars, `repr()` redacts all auth values
- **Cache isolation**: Owner-only directory permissions (0o700)
- **Graceful degradation**: Missing optional deps don't crash — features degrade with clear messages

## CLI

```bash
mcp-research serve                          # Run MCP stdio server (default)
mcp-research search "query"                 # Search the web
mcp-research fetch https://example.com      # Fetch URL to markdown
mcp-research youtube https://youtu.be/...   # Extract YouTube video
mcp-research ingest ./docs/                 # Extract text from files
mcp-research academic "10.1109/..."         # Resolve academic paper
mcp-research tweet https://x.com/.../123    # Extract tweet
mcp-research vault                          # Show vault profiles
mcp-research doctor                         # Check dependencies
```

## Development

```bash
git clone https://github.com/MABAAM/Maibaamcrawler.git
cd Maibaamcrawler
pip install -e ".[all]"
pytest tests/ -v
python -m mcp_research
```

## Changelog

### v0.3.0

- **Credential vault**: YAML config at `~/.mcp-research/vault.yaml` with env var interpolation, glob URL matching, EZproxy rewriting, hot-reload
- **Session pooling**: Per-domain sessions with vault auth injection, cookie jar support, idle eviction
- **CAPTCHA detection**: Identifies Cloudflare, hCaptcha, reCAPTCHA, Akamai, DDoS-Guard, generic bot walls
- **Academic lookup**: DOI/ArXiv/PubMed resolution, Crossref metadata, institutional full text access via vault
- **Twitter/X extraction**: yt-dlp, API v2, and cookie-based access with thread support
- **Token efficiency**: Default output caps (~4K tokens for fetch, ~500 per research source) to preserve AI context
- **Doctor command**: `mcp-research doctor` checks all dependencies and configuration
- **Windows encoding fix**: UTF-8 stdout/stderr wrapper prevents cp1252 crashes

### v0.2.0

- **YouTube essence**: Transcript extraction, AI summary, key points, chapters, quotes
- **Deep ingest**: PDF, DOCX, XLSX, PPTX, audio, video, image text extraction
- **Ollama integration**: Query rewriting, summarization, synthesis, vision OCR
- **Search logging**: NDJSON event log for all operations
- **Brave Search**: Primary search tier with API key support

### v0.1.0

- Initial release: 3 tools (web_search, fetch_url, research), SSRF protection, caching

## License

MIT
