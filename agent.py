#!/usr/bin/env python3
"""Phase 4: run one robot as its own process, negotiating over the comms
server instead of in-process. No third process drives this - each robot
decides its own turn and checks agreement itself, using logic that already
exists in negotiation.py unchanged. See docs/PLAN.md §5, D2.

    Terminal 1: python comms_server.py --port 8000
    Terminal 2: python agent.py --scenario <id> --side a --policy stubborn --comms-url http://localhost:8000
    Terminal 3: python agent.py --scenario <id> --side b --policy always_yield --comms-url http://localhost:8000

Both agents must be given the SAME --scenario by hand - nothing here checks
that for you. That handshake gap is exactly what Phase 5's A2A agent cards
exist to close (see D2); a deliberate rough edge here, not an oversight.

There is no timeout on waiting for a silent peer by default - that's the
point: if the other robot never starts, this will poll forever, which is
exactly what "an agent is down" looks like. Ctrl+C to stop.
"""

import argparse
import sys
import time

sys.path.insert(0, "src")

from negotiation import POLICIES, Robot, check_agreement  # noqa: E402
from scenarios import BY_ID, SCENARIOS  # noqa: E402
from wire import history_from_list, message_to_dict  # noqa: E402


def decide(me, other, history, my_turn, max_turns):
    """The whole per-turn decision, as a pure function: given the current
    shared history, either this robot's next Message (if it's this robot's
    turn and nothing has been decided yet), or None (wait and poll again).
    Separated from the loop/sleep/HTTP glue in main() so it's directly
    testable with a hand-built history list."""
    if check_agreement(history) is not None:
        return None
    if len(history) >= max_turns:
        return None
    if not my_turn(len(history)):
        return None
    return me.policy.respond(me, other, history, max_turns)


def fetch_history(comms_url):
    import httpx

    response = httpx.get(f"{comms_url}/messages")
    response.raise_for_status()
    return history_from_list(response.json())


def post_message(comms_url, message):
    import httpx

    response = httpx.post(f"{comms_url}/messages", json=message_to_dict(message))
    response.raise_for_status()


def run(me, other, my_turn, comms_url, max_turns, poll_interval):
    """Poll the comms server until this negotiation is decided (agreed or
    ran out of turns), speaking on this robot's own turns as they come up."""
    while True:
        history = fetch_history(comms_url)

        outcome = check_agreement(history)
        if outcome is not None:
            print(f"{me.name}: agreed on {outcome}")
            return
        if len(history) >= max_turns:
            print(f"{me.name}: no agreement after {max_turns} messages - deadlock")
            return

        message = decide(me, other, history, my_turn, max_turns)
        if message is not None:
            print(f"  {message}")
            post_message(comms_url, message)
        else:
            time.sleep(poll_interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default=SCENARIOS[0].id, choices=list(BY_ID))
    parser.add_argument("--side", required=True, choices=["a", "b"])
    parser.add_argument("--policy", required=True, choices=list(POLICIES))
    parser.add_argument("--comms-url", default="http://localhost:8000")
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    args = parser.parse_args()

    scenario = BY_ID[args.scenario]

    if args.side == "a":
        me = Robot("Robot A", scenario.a_situation, scenario.a_urgency, POLICIES[args.policy]())
        other = Robot("Robot B", "", 0)  # placeholder only - real secret never held here
        my_turn = lambda count: count % 2 == 0  # noqa: E731
    else:
        me = Robot("Robot B", scenario.b_situation, scenario.b_urgency, POLICIES[args.policy]())
        other = Robot("Robot A", "", 0)
        my_turn = lambda count: count % 2 == 1  # noqa: E731

    print(f"{me.name} ({args.policy}) joining {scenario.id} via {args.comms_url}")
    run(me, other, my_turn, args.comms_url, args.max_turns, args.poll_interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
