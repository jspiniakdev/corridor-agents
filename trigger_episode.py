#!/usr/bin/env python3
"""Phase 10b-3b: start a fresh episode by resetting the deployed world.

The deployed robots run `agent.py --serve` - they idle at their targets
until `world/current` is reset, then move again. This calls `reset_world()`
over MCP (with an OIDC token, since a deployed `world_server.py` is locked
down with --no-allow-unauthenticated like robot-b). Run it locally with ADC
that can reach the world Service, or as a one-shot Cloud Run Job:

    python trigger_episode.py --world-url https://world-<n>.<region>.run.app/mcp --auth

Local three-terminal run (world_server.py + two agent.py --serve): drop
--auth, point at http://127.0.0.1:9500/mcp.
"""

import argparse
import asyncio
import sys

sys.path.insert(0, "src")

from agent import build_world_client, mcp_call  # noqa: E402


async def main_async(world_url, auth):
    async with build_world_client(world_url, auth) as world:
        print(await mcp_call(world, "reset_world"))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--world-url", default="http://127.0.0.1:9500/mcp")
    parser.add_argument(
        "--auth",
        action="store_true",
        help="attach an OIDC token (audience = --world-url) - needed for a locked-down deployed world Service",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args.world_url, args.auth))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
