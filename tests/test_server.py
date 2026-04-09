"""Server tool registration and annotation tests."""

from mcp_research.server import server


class TestToolRegistration:

    def test_eight_tools_registered(self):
        tools = list(server._tool_manager._tools.keys())
        assert "web_search" in tools
        assert "fetch_url" in tools
        assert "research" in tools
        assert "youtube_essence" in tools
        assert "deep_ingest" in tools
        assert "academic_lookup" in tools
        assert "twitter_extract" in tools
        assert "vault_status" in tools
        assert len(tools) == 8


class TestSafetyAnnotations:

    def test_all_tools_read_only(self):
        for name, tool in server._tool_manager._tools.items():
            ann = tool.annotations
            assert ann.readOnlyHint is True, f"{name} missing readOnlyHint"
            assert ann.destructiveHint is False, f"{name} has destructiveHint"


class TestToolsAreAsync:

    def test_web_search_is_coroutine(self):
        from mcp_research.server import web_search
        import asyncio
        assert asyncio.iscoroutinefunction(web_search)

    def test_fetch_url_is_coroutine(self):
        from mcp_research.server import fetch_url
        import asyncio
        assert asyncio.iscoroutinefunction(fetch_url)

    def test_research_is_coroutine(self):
        from mcp_research.server import research
        import asyncio
        assert asyncio.iscoroutinefunction(research)

    def test_youtube_essence_is_coroutine(self):
        from mcp_research.server import youtube_essence
        import asyncio
        assert asyncio.iscoroutinefunction(youtube_essence)

    def test_deep_ingest_is_coroutine(self):
        from mcp_research.server import deep_ingest
        import asyncio
        assert asyncio.iscoroutinefunction(deep_ingest)

    def test_academic_lookup_is_coroutine(self):
        from mcp_research.server import academic_lookup
        import asyncio
        assert asyncio.iscoroutinefunction(academic_lookup)

    def test_twitter_extract_is_coroutine(self):
        from mcp_research.server import twitter_extract
        import asyncio
        assert asyncio.iscoroutinefunction(twitter_extract)

    def test_vault_status_is_coroutine(self):
        from mcp_research.server import vault_status
        import asyncio
        assert asyncio.iscoroutinefunction(vault_status)
