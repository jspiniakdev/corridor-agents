#!/usr/bin/env python3
"""D29: a raw, unfiltered ground-truth export of one networked episode, for
debugging without going through visualize_network.py's HTML replay at all -
requested directly, after repeated confusion about what the rendered
replay was actually showing (D25, D28 both came from exactly that kind of
confusion). No interpretation here: one row per real event, sorted by real
timestamp, nothing collapsed, nothing forward-filled, nothing guessed.

Merges three sources, all already real-timestamped:
  - world_server.py's get_log() - one row per propose_action call (D17/D20)
  - experiments/results/negotiation_trace.json - one row per message, each
    with its own real timestamp now (D29), not just the exchange's
    start/end (D22)
  - experiments/results/robot_status_<side>.jsonl, if present - only
    exists when a run used agent.py's --debug-log flag (D29, off by
    default); a real-time log of each robot's own decision points. The
    CSV works fine without these - status_detail just stays blank.

Run this AFTER a live networked episode has finished, same as
visualize_network.py:

    python export_timeline_csv.py --world-url http://127.0.0.1:9500/mcp
"""

import argparse
import asyncio
import csv
import json
import os
import sys

sys.path.insert(0, "src")

from agent import NEGOTIATION_TRACE_PATH, ROBOT_STATUS_PATH_TEMPLATE  # noqa: E402

OUTPUT_PATH = "experiments/results/episode_timeline.csv"

FIELDNAMES = [
    "timestamp",
    "elapsed_s",
    "event_type",
    "side_or_speaker",
    "a_position",
    "b_position",
    "world_action",
    "world_resolved",
    "message_intent",
    "message_goes_first",
    "message_text",
    "status_detail",
]


async def mcp_call(client, name, **args):
    result = await client.call_tool(name, args)
    return json.loads(result.content[0].text)


def load_messages():
    """[] if the episode never negotiated - no trace file was ever
    written (a robot arrived at its boundary alone)."""
    if not os.path.exists(NEGOTIATION_TRACE_PATH):
        return []
    with open(NEGOTIATION_TRACE_PATH) as f:
        return json.load(f)["messages"]


def load_status_entries(side):
    """[] if this side's run didn't use --debug-log - not an error, just
    nothing to add for that side."""
    path = ROBOT_STATUS_PATH_TEMPLATE.format(side=side)
    if not os.path.exists(path):
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def build_timeline_rows(world_entries, messages, status_entries, grid=None):
    """Pure merge - no network, no file I/O - so this is the part that's
    actually unit-testable (same split every other script in this project
    uses). Each source keeps only the columns it actually has; everything
    else on that row stays blank rather than guessed or forward-filled.

    world_server.py's log has one row per propose_action call, not one
    row per moment in time - there's no "episode began, here's where
    everyone started" row, since a robot's very first poll is already a
    real move if it isn't at its boundary yet. That leaves two things
    silently unstated: a robot's true starting position (implicit in the
    static grid, D17's get_map(), never logged), and - just as easy to
    misread - the fact that a robot's *first* logged row has no earlier
    row of its own to measure a real per-robot duration against, so its
    timestamp only means "this many seconds after whichever robot's
    first action happened to log first," not "this robot's first move
    took this long." If grid is given (min_position/max_position from
    get_map()), a synthetic "start" row makes the true starting positions
    explicit, timestamped fractionally before the earliest real event so
    it always sorts first without claiming a real duration for anything."""
    rows = []

    if grid is not None and world_entries:
        earliest = min(entry["timestamp"] for entry in world_entries)
        rows.append(
            {
                "timestamp": earliest - 0.001,
                "event_type": "start",
                "side_or_speaker": "",
                "a_position": grid["min_position"],
                "b_position": grid["max_position"],
                "world_action": "",
                "world_resolved": "",
                "message_intent": "",
                "message_goes_first": "",
                "message_text": "",
                "status_detail": "episode start - true starting positions, before any propose_action call",
            }
        )

    for entry in world_entries:
        rows.append(
            {
                "timestamp": entry["timestamp"],
                "event_type": "world",
                "side_or_speaker": entry["side"],
                "a_position": entry["a_position"],
                "b_position": entry["b_position"],
                "world_action": entry["action"],
                "world_resolved": entry["resolved"],
                "message_intent": "",
                "message_goes_first": "",
                "message_text": "",
                "status_detail": "",
            }
        )

    for message in messages:
        rows.append(
            {
                "timestamp": message["timestamp"],
                "event_type": "message",
                "side_or_speaker": message["speaker"],
                "a_position": "",
                "b_position": "",
                "world_action": "",
                "world_resolved": "",
                "message_intent": message["intent"],
                "message_goes_first": message.get("goes_first") or "",
                "message_text": message.get("text", ""),
                "status_detail": "",
            }
        )

    for status in status_entries:
        rows.append(
            {
                "timestamp": status["timestamp"],
                "event_type": "status",
                "side_or_speaker": status["side"],
                "a_position": "",
                "b_position": "",
                "world_action": "",
                "world_resolved": "",
                "message_intent": "",
                "message_goes_first": "",
                "message_text": "",
                "status_detail": status["detail"],
            }
        )

    rows.sort(key=lambda row: row["timestamp"])
    start = rows[0]["timestamp"] if rows else 0.0
    for row in rows:
        row["elapsed_s"] = round(row["timestamp"] - start, 3)

    return rows


def write_csv(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


async def main_async(args):
    from mcp.client import Client

    async with Client(args.world_url) as world:
        grid = await mcp_call(world, "get_map")
        log_result = await mcp_call(world, "get_log")

    world_entries = log_result["entries"]
    messages = load_messages()
    status_entries = load_status_entries("a") + load_status_entries("b")

    rows = build_timeline_rows(world_entries, messages, status_entries, grid)
    write_csv(rows, OUTPUT_PATH)
    print(f"wrote {OUTPUT_PATH} ({len(rows)} rows)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--world-url", default="http://127.0.0.1:9500/mcp")
    args = parser.parse_args()
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
