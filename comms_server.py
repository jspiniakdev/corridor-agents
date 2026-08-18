#!/usr/bin/env python3
"""Phase 4: the comms server. A deliberately dumb, temporary message board -
two robots read and write to it to negotiate. It has zero awareness of
turns, agreement, or content: it doesn't know what a negotiation is, only
that messages get appended and can be read back in order.

This is a stand-in for what Phase 5's real A2A protocol properly solves
(agent cards, task lifecycle, webhooks). See docs/PLAN.md §5, D2.

    python comms_server.py --port 8000
"""

import argparse

from fastapi import FastAPI

app = FastAPI()

messages: list[dict] = []


@app.get("/messages")
def get_messages():
    return messages


@app.post("/messages")
def post_message(message: dict):
    messages.append(message)
    return {"count": len(messages)}


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    print(f"comms server listening on port {args.port} (dumb - no negotiation logic here)")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
