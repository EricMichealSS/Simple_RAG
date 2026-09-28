"""Throwaway verification script (Phase 1) -- not a deliverable. Connects to
docs_search_server.py as a real MCP client would, over real stdio, and
exercises all 3 tools including the error paths. Deleted/ignored after use.
"""
import asyncio
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER_PATH = str(Path(__file__).parent / "docs_search_server.py")


async def main():
    params = StdioServerParameters(command="python3", args=[SERVER_PATH])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])
            for t in tools.tools:
                print(f"  - {t.name}: {t.description[:80]!r}...")

            print("\n--- valid call: get_openapi_spec ---")
            r = await session.call_tool("get_openapi_spec", {"api_version": "2025-06-01", "endpoint": "create_issue"})
            print(r.content[0].text[:200])

            print("\n--- invalid api_version (recoverable error test) ---")
            r = await session.call_tool("get_openapi_spec", {"api_version": "v4", "endpoint": "create_issue"})
            print(r.content[0].text)

            print("\n--- check_deprecation, correct direction (newer version) ---")
            r = await session.call_tool("check_deprecation", {"api_version": "2025-06-01", "endpoint": "create_issue"})
            print(r.content[0].text[:200])

            print("\n--- check_deprecation, WRONG direction (older version) ---")
            r = await session.call_tool("check_deprecation", {"api_version": "2022-11-28", "endpoint": "create_issue"})
            print(r.content[0].text)

            print("\n--- search_docs ---")
            r = await session.call_tool("search_docs", {"query": "make_latest release"})
            print(r.content[0].text[:200])


asyncio.run(main())
