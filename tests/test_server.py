"""Server tool registration and annotation tests."""

from mcp_research.server import server


class TestToolRegistration:

    def test_three_tools_registered(self):
        tools = list(server._tool_manager._tools.keys())
        assert "web_search" in tools
        assert "fetch_url" in tools
        assert "research" in tools
        assert len(tools) == 3


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
