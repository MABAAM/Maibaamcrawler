import asyncio
from mcp_research.server import server


def main():
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
