#!/usr/bin/env python3
"""Phase 5: run one robot as its own process, negotiating over real A2A.
See docs/PLAN.md §5, D15.

Without --world-url: negotiation only, fixed roles (Phase 5's original
shape) - --side a is the INITIATOR (a pure A2A client, dials the peer),
--side b is the RESPONDER (a pure A2A server, hosts
agent_executor.NegotiationExecutor).

    Terminal 1: python agent.py --scenario <id> --side b --policy always_yield --port 9001
    Terminal 2: python agent.py --scenario <id> --side a --policy stubborn --peer-url http://127.0.0.1:9001

With --world-url (Phase 6/D17, dynamic initiation D18): every robot plays
BOTH roles at once - always an A2A server (run_robot), and capable of
dialing the peer the instant its own observation says it's at the
boundary and can sense the other. --side now only picks which half of
the scenario to load (D16) and which robot name to use - it no longer
means "client-only" vs "server-only".

    Terminal 1: python world_server.py --port 9500
    Terminal 2: python agent.py --scenario <id> --side b --policy always_yield --port 9002 --peer-url http://127.0.0.1:9001 --world-url http://127.0.0.1:9500/mcp
    Terminal 3: python agent.py --scenario <id> --side a --policy stubborn  --port 9001 --peer-url http://127.0.0.1:9002 --world-url http://127.0.0.1:9500/mcp
"""

import argparse
import asyncio
import random
import sys
import time

sys.path.insert(0, "src")

from negotiation import POLICIES, Robot, check_agreement  # noqa: E402
from scenarios import BY_ID, SCENARIOS, for_side  # noqa: E402
from wire import history_to_list, message_from_dict, message_to_dict  # noqa: E402

from agent_executor import NegotiationExecutor  # noqa: E402

JITTER_SECONDS = 1.0


def build_robots(side, scenario_id, policy_name):
    """Only ever loads this robot's own situation/urgency via for_side() -
    never the paired Scenario object - so this process's own state (me,
    other) can't accidentally end up holding the other robot's secret.
    See D16."""
    if side == "a":
        situation, urgency = for_side(scenario_id, "a")
        me = Robot("Robot A", situation, urgency, POLICIES[policy_name]())
        other = Robot("Robot B", "", 0)  # placeholder only - real secret never held here
    else:
        situation, urgency = for_side(scenario_id, "b")
        me = Robot("Robot B", situation, urgency, POLICIES[policy_name]())
        other = Robot("Robot A", "", 0)
    return me, other


def is_my_turn_to_initiate(me_name, other_name):
    """The race-case tiebreak (D18): lexicographically smaller name wins -
    "Robot A" always wins a tie against "Robot B". A fixed rule both sides
    compute identically with zero coordination, same as real "glare"
    resolution in telecom signaling."""
    return me_name < other_name


def report_outcome(me, history, max_turns):
    """Prints the outcome and also returns it, so a caller driving a
    movement loop (run_initiator_with_world) can use it, not just log it."""
    outcome = check_agreement(history)
    if outcome is not None:
        print(f"{me.name}: agreed on {outcome}")
    else:
        print(f"{me.name}: no agreement after {len(history)} messages - deadlock")
    return outcome


def extract_reply(response):
    """Given a StreamResponse - live off the wire during streaming, or
    reconstructed from a webhook POST body - return (state, message_parts).
    Both consumers (run_initiator's live loop and run_initiator_webhook's
    callback loop) need the exact same extraction."""
    if response.HasField("task"):
        status = response.task.status
    elif response.HasField("status_update"):
        status = response.status_update.status
    else:
        return None, None
    parts = status.message.parts if status.HasField("message") else None
    return status.state, parts


async def run_initiator(me, other, peer_url, max_turns):
    """The --side a client loop: send a message, read back whatever the
    responder's TaskUpdater published, and keep going until the task
    reaches a terminal state. Holds the connection open the whole time -
    see run_initiator_webhook for the alternative that doesn't. Returns
    (outcome, history) - D19 needs the full transcript, not just the
    outcome, to write a trace file."""
    from a2a.client import create_client
    from a2a.helpers import get_data_parts, new_data_message
    from a2a.types.a2a_pb2 import Role, SendMessageRequest, TaskState

    client = await create_client(peer_url)
    history = []
    task_id = None
    context_id = None

    while True:
        if check_agreement(history) is not None or len(history) >= max_turns:
            return report_outcome(me, history, max_turns), history

        my_message = me.policy.respond(me, other, history, max_turns)
        print(f"  {my_message}")
        history.append(my_message)

        a2a_message = new_data_message(
            message_to_dict(my_message),
            role=Role.ROLE_USER,
            task_id=task_id,
            context_id=context_id,
        )
        request = SendMessageRequest(message=a2a_message)

        final_state = None
        peer_reply_parts = None
        async for response in client.send_message(request):
            if response.HasField("task"):
                task_id = response.task.id
                context_id = response.task.context_id
            state, parts = extract_reply(response)
            if state == TaskState.TASK_STATE_WORKING:
                # the streaming heartbeat - peer is alive and thinking,
                # not silent because it's down
                print(f"  ({other.name} is working...)")
                continue
            if state is not None:
                final_state = state
            if parts is not None:
                peer_reply_parts = parts

        if peer_reply_parts is not None:
            peer_reply = message_from_dict(get_data_parts(peer_reply_parts)[0])
            print(f"  {peer_reply}")
            history.append(peer_reply)

        if final_state == TaskState.TASK_STATE_COMPLETED:
            return report_outcome(me, history, max_turns), history


async def run_initiator_webhook(me, other, peer_url, webhook_port, max_turns):
    """Same negotiation as run_initiator, but never holds a connection open
    waiting for the responder. Registers a callback URL and sends with
    return_immediately=True, gets control back immediately, and only
    resumes when the responder's push notification actually arrives on
    this robot's own tiny receiver. See D15 - this is the piece that makes
    Robot A a server too, not just a client."""
    import uvicorn
    from a2a.client import create_client
    from a2a.helpers import get_data_parts, new_data_message
    from a2a.types.a2a_pb2 import (
        Role,
        SendMessageConfiguration,
        SendMessageRequest,
        StreamResponse,
        TaskPushNotificationConfig,
        TaskState,
    )
    from fastapi import FastAPI, Request
    from google.protobuf.json_format import ParseDict

    incoming = asyncio.Queue()
    app = FastAPI()

    @app.post("/push")
    async def receive_push(request: Request):
        body = await request.json()
        await incoming.put(ParseDict(body, StreamResponse()))
        return {"ok": True}

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=webhook_port, log_level="warning"))
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    webhook_url = f"http://127.0.0.1:{webhook_port}/push"
    print(f"{me.name} accepting callbacks on {webhook_url}")

    client = await create_client(peer_url)
    history = []
    task_id = None
    context_id = None

    try:
        while True:
            if check_agreement(history) is not None or len(history) >= max_turns:
                return report_outcome(me, history, max_turns), history

            my_message = me.policy.respond(me, other, history, max_turns)
            print(f"  {my_message}")
            history.append(my_message)

            a2a_message = new_data_message(
                message_to_dict(my_message), role=Role.ROLE_USER, task_id=task_id, context_id=context_id
            )
            configuration = SendMessageConfiguration(
                return_immediately=True,
                task_push_notification_config=TaskPushNotificationConfig(url=webhook_url),
            )
            request = SendMessageRequest(message=a2a_message, configuration=configuration)

            async for response in client.send_message(request):
                if response.HasField("task"):
                    task_id = response.task.id
                    context_id = response.task.context_id
                break  # return_immediately - this is just the ack, not the answer

            print(f"  ({me.name} is free - waiting for {other.name}'s callback, not holding the line)")

            # the task-creation event that came back synchronously above
            # also gets pushed here again (SUBMITTED) - along with the
            # WORKING heartbeat - before the real answer; skip anything
            # that isn't actually a turn worth acting on
            not_yet_an_answer = {TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING}
            final_state = None
            peer_reply_parts = None
            while final_state is None:
                response = await incoming.get()
                state, parts = extract_reply(response)
                if state in not_yet_an_answer:
                    if state == TaskState.TASK_STATE_WORKING:
                        print(f"  ({other.name} is working...)")
                    continue
                final_state = state
                peer_reply_parts = parts

            if peer_reply_parts is not None:
                peer_reply = message_from_dict(get_data_parts(peer_reply_parts)[0])
                print(f"  {peer_reply}")
                history.append(peer_reply)

            if final_state == TaskState.TASK_STATE_COMPLETED:
                return report_outcome(me, history, max_turns), history
    finally:
        server.should_exit = True
        await server_task


def build_responder_app(me, other, port, max_turns, on_resolved=None, on_task_started=None):
    """The A2A server app, always wired for push notifications - dormant
    unless a caller actually registers a callback URL (run_initiator
    never does; run_initiator_webhook does). `on_resolved`/
    `on_task_started` are threaded through to NegotiationExecutor - see
    D17, D18."""
    import httpx
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
    from a2a.server.tasks import BasePushNotificationSender, InMemoryPushNotificationConfigStore, InMemoryTaskStore
    from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
    from fastapi import FastAPI

    skill = AgentSkill(
        id="negotiate",
        name="Negotiate corridor priority",
        description="Negotiates who goes first through a shared one-lane corridor.",
        tags=["negotiation"],
    )
    card = AgentCard(
        name=me.name,
        description=f"{me.name}, negotiating via the {type(me.policy).__name__} policy.",
        version="0.0.1",
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=f"http://127.0.0.1:{port}", protocol_version="1.0")
        ],
        skills=[skill],
    )
    push_config_store = InMemoryPushNotificationConfigStore()
    push_sender = BasePushNotificationSender(httpx.AsyncClient(), push_config_store)
    request_handler = DefaultRequestHandler(
        agent_executor=NegotiationExecutor(
            me, other, max_turns, on_resolved=on_resolved, on_task_started=on_task_started
        ),
        task_store=InMemoryTaskStore(),
        agent_card=card,
        push_config_store=push_config_store,
        push_sender=push_sender,
    )
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url="/"),
    )
    return app


def run_responder(me, other, port, max_turns):
    """The --side b server on its own: always listening, reacting to
    whatever the initiator sends. See run_responder_async for the
    concurrent-with-a-movement-loop version (--world-url)."""
    import uvicorn

    app = build_responder_app(me, other, port, max_turns)
    print(f"{me.name} listening on http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port)


async def run_responder_async(me, other, port, max_turns, on_resolved=None, on_task_started=None):
    """Same server, started as a background task instead of blocking the
    thread - so a movement loop can run alongside it. Returns the
    (server, task) pair; caller is responsible for server.should_exit +
    awaiting task on the way out, same pattern run_initiator_webhook uses
    for its own webhook receiver."""
    import uvicorn

    app = build_responder_app(me, other, port, max_turns, on_resolved=on_resolved, on_task_started=on_task_started)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    print(f"{me.name} listening on http://127.0.0.1:{port}")
    return server, task


async def mcp_call(client, name, **args):
    """Call an MCP tool and unwrap its JSON payload - every world_server.py
    tool returns a dict serialized into the one text content block."""
    import json

    result = await client.call_tool(name, args)
    return json.loads(result.content[0].text)


def decide_movement(obs, priority, my_name, other_name):
    """Ported from world.py's _decide_one - world_server.py no longer has
    an executive layer at all (see D17), so this is now agent.py's own
    call: given this robot's current observation and the negotiated
    priority (None if not yet known), what should it propose next?"""
    if obs["reached_target"]:
        return "wait"
    if not obs["at_boundary"]:
        return "move"
    if priority == my_name:
        return "move"
    if priority == other_name:
        return "move" if obs["other_cleared_zone"] else "wait"
    return "wait"  # priority not yet known - hold at the boundary


async def negotiate_as_initiator(me, other, peer_url, max_turns, webhook_port):
    if webhook_port:
        return await run_initiator_webhook(me, other, peer_url, webhook_port, max_turns)
    return await run_initiator(me, other, peer_url, max_turns)


NEGOTIATION_TRACE_PATH = "experiments/results/negotiation_trace.json"


def write_negotiation_trace(history):
    """Whichever robot ends up holding the full transcript - either side,
    now that initiation is dynamic (D18) - writes it out once the
    negotiation concludes, so visualize_network.py can render it
    afterward. No file at all if an episode never negotiates (a robot
    arrived at its boundary alone). See D19."""
    import json
    import os

    os.makedirs(os.path.dirname(NEGOTIATION_TRACE_PATH), exist_ok=True)
    with open(NEGOTIATION_TRACE_PATH, "w") as f:
        json.dump(history_to_list(history), f, indent=2)
    print(f"wrote {NEGOTIATION_TRACE_PATH}")


async def run_robot(me, other, side, port, peer_url, world_url, max_turns, webhook_port=None):
    """--world-url, dynamic initiation (D18): every robot always runs its
    own A2A server AND its own movement loop AND is capable of dialing
    the peer - the fixed --side a/--side b role split from Phase 6 is
    gone. Negotiation still only fires once this robot's own observation
    says it's at the boundary and can actually sense the other (D12's
    physical logic, unchanged) - arriving alone still claims priority for
    free, zero negotiation.

    The dial itself is jittered (a random delay before dialing, not
    inline - the loop keeps polling/proposing while it waits) and run as
    a cancellable background task. Two race scenarios, both handled -
    see D18:
    1. An incoming task starts while this robot is ALREADY mid-dial ->
       on_task_started cancels the losing side's outbound attempt.
    2. An incoming task is already active (started, not yet resolved)
       when this robot's own jitter deadline would otherwise fire -
       incoming_active blocks a *second*, redundant dial from ever
       starting in the first place. Caught live: without this, a slow
       LLM negotiation left enough of a window for the responding side's
       own movement loop to independently start dialing the peer it was
       already mid-negotiation with."""
    from mcp.client import Client

    priority_holder = {"value": None}
    dial_holder = {"task": None}
    jitter_deadline = {"value": None}
    incoming_active = {"value": False}

    def on_resolved(outcome, history):
        priority_holder["value"] = outcome
        incoming_active["value"] = False
        print(f"{me.name}: negotiated outcome (incoming task): {outcome}")
        write_negotiation_trace(history)

    def on_task_started(peer_name):
        incoming_active["value"] = True
        dial_task = dial_holder["task"]
        if dial_task is not None and not dial_task.done() and not is_my_turn_to_initiate(me.name, peer_name):
            print(f"{me.name}: race - {peer_name} called in while I was dialing - deferring")
            dial_task.cancel()

    server, server_task = await run_responder_async(
        me, other, port, max_turns, on_resolved=on_resolved, on_task_started=on_task_started
    )

    try:
        async with Client(world_url) as world:
            while True:
                obs = await mcp_call(world, "get_observation", side=side)
                if obs["reached_target"]:
                    print(f"{me.name}: reached target")
                    return

                if priority_holder["value"] is None and obs["at_boundary"]:
                    if not obs["sensed_other"]:
                        priority_holder["value"] = me.name
                        print(f"{me.name}: at boundary alone - claiming priority")
                    elif dial_holder["task"] is None and not incoming_active["value"]:
                        if jitter_deadline["value"] is None:
                            jitter_deadline["value"] = time.monotonic() + random.uniform(0, JITTER_SECONDS)
                        elif time.monotonic() >= jitter_deadline["value"]:
                            print(f"{me.name}: at boundary, sensing {other.name} - dialing")
                            dial_holder["task"] = asyncio.create_task(
                                negotiate_as_initiator(me, other, peer_url, max_turns, webhook_port)
                            )

                dial_task = dial_holder["task"]
                if dial_task is not None and dial_task.done():
                    if not dial_task.cancelled():
                        error = dial_task.exception()
                        if error is not None:
                            # a real robot doesn't die because it called a
                            # peer a moment too early - log it and let a
                            # fresh dial get scheduled on a later iteration
                            print(f"{me.name}: dial failed ({error}) - will retry")
                            jitter_deadline["value"] = None
                        else:
                            priority_holder["value"], dial_history = dial_task.result()
                            write_negotiation_trace(dial_history)
                    dial_holder["task"] = None

                action = decide_movement(obs, priority_holder["value"], me.name, other.name)
                await mcp_call(world, "propose_action", side=side, action=action)
                await asyncio.sleep(0.2)
    finally:
        server.should_exit = True
        await server_task


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default=SCENARIOS[0].id, choices=list(BY_ID))
    parser.add_argument("--side", required=True, choices=["a", "b"])
    parser.add_argument("--policy", required=True, choices=list(POLICIES))
    parser.add_argument("--port", type=int, default=9001, help="this robot's own A2A server port (without --world-url: side b only)")
    parser.add_argument("--peer-url", default="http://127.0.0.1:9001", help="the peer's URL (without --world-url: side a only)")
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument(
        "--webhook",
        action="store_true",
        help="when dialing, don't hold the connection open - register a callback and wait to be called back",
    )
    parser.add_argument("--webhook-port", type=int, default=9002, help="this robot's own callback port, with --webhook")
    parser.add_argument("--world-url", default=None, help="Phase 6/D18: also move through world_server.py's grid, negotiating dynamically at the boundary")
    args = parser.parse_args()

    me, other = build_robots(args.side, args.scenario, args.policy)

    if args.world_url:
        print(f"{me.name} ({args.policy}) moving via {args.world_url}, ready to negotiate {args.scenario} at the boundary")
        webhook_port = args.webhook_port if args.webhook else None
        asyncio.run(run_robot(me, other, args.side, args.port, args.peer_url, args.world_url, args.max_turns, webhook_port))
        return 0

    if args.side == "a":
        print(f"{me.name} ({args.policy}) initiating {args.scenario} against {args.peer_url}")
        if args.webhook:
            asyncio.run(run_initiator_webhook(me, other, args.peer_url, args.webhook_port, args.max_turns))
        else:
            asyncio.run(run_initiator(me, other, args.peer_url, args.max_turns))
    else:
        run_responder(me, other, args.port, args.max_turns)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
