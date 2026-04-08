# mcp-research

<!-- mcp-name: io.github.mabaam/mcp-research -->

A standalone MCP (Model Context Protocol) server providing web research tools. Three battle-tested tools for AI assistants: search the web, fetch & convert pages to markdown, and run compound multi-source research — all via the MCP stdio protocol.

## Tools

| Tool | Description |
|------|-------------|
| `web_search` | 3-tier search cascade: Brave API → DuckDuckGo → HTML scraper |
| `fetch_url` | Fetch any URL → clean markdown, with SSRF protection and 24h cache |
| `research` | Compound pipeline: query rewrite → search → parallel fetch → summarize → synthesize |

All tools are **read-only** — they fetch and transform public web content, never modify anything.

## Install

```bash
pip install mcp-research
```

Or run directly with `uvx` (zero-install):

```bash
uvx mcp-research
```

## Configuration

All configuration is via environment variables — no config files needed.

| Variable | Default | Description |
|----------|---------|-------------|
| `BRAVE_API_KEY` | *(empty)* | Brave Search API key. Falls back to DuckDuckGo if unset. |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint for summarization/synthesis. Set empty to disable. |
| `OLLAMA_MODEL` | `qwen2.5:14b` | Model to use for summarization and synthesis. |
| `MCP_RESEARCH_CACHE_DIR` | `~/.mcp-research/cache/` | URL fetch cache directory. |
| `MCP_RESEARCH_CACHE_TTL` | `24` | Cache TTL in hours. |
| `MCP_RESEARCH_LOG_DIR` | `~/.mcp-research/logs/` | Search log directory (NDJSON). |
| `MCP_RESEARCH_MAX_RESULTS` | `10` | Default max search results. |

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
fetch_url(url, summarize=False, max_chars=50000)
```

Fetches a URL and converts it to clean markdown:
- **SSRF protection**: Blocks localhost, private IPs, non-HTTP schemes
- **Smart retry**: Exponential backoff on 429/5xx, per-hop redirect validation
- **24h cache**: SHA-256 keyed, configurable TTL
- **Content support**: HTML → markdown, JSON → code block, binary → rejected
- **Smart truncation**: Breaks at heading/paragraph boundaries, not mid-text

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

All steps gracefully degrade without Ollama — you still get search results and raw page content.

## Development

```bash
git clone https://github.com/MABAAM/Maibaamcrawler.git
cd Maibaamcrawler
pip install -e .
python -m mcp_research
```

## License

MIT
