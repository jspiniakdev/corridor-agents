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
import sys
import time

sys.path.insert(0, "src")

from negotiation import POLICIES, Robot, check_agreement  # noqa: E402
from observation import compose_observation_from_dict  # noqa: E402
from scenarios import BY_ID, for_side  # noqa: E402
from wire import history_to_list, message_from_dict, message_to_dict  # noqa: E402

from agent_executor import NegotiationExecutor  # noqa: E402

POLL_INTERVAL_SECONDS = 1.0  # D30: how often a robot checks in with the world - governs movement, waiting, and negotiation-trigger cadence uniformly, not a separate "how long does moving take" model. Slept BEFORE each check-in (top of run_robot()'s loop), not after - so the first check-in is paced too, not a free instant action exempt from the interval.


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
    (outcome, history, message_times) - D19 needs the full transcript, not
    just the outcome, to write a trace file; message_times (D29) is a
    real time.time() per entry in history, parallel by index, cheap to
    capture (one call already in flight per message) and always included
    - unlike the debug-flagged robot-status log, this doesn't cost an
    extra file write, just one more field on the trace file already
    written every run."""
    from a2a.client import create_client
    from a2a.helpers import get_data_parts, new_data_message
    from a2a.types.a2a_pb2 import Role, SendMessageRequest, TaskState

    client = await create_client(peer_url)
    history = []
    message_times = []
    task_id = None
    context_id = None

    while True:
        if check_agreement(history) is not None or len(history) >= max_turns:
            return report_outcome(me, history, max_turns), history, message_times

        my_message = me.policy.respond(me, other, history, max_turns)
        print(f"  {my_message}")
        history.append(my_message)
        message_times.append(time.time())

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
            message_times.append(time.time())

        if final_state == TaskState.TASK_STATE_COMPLETED:
            return report_outcome(me, history, max_turns), history, message_times


async def run_initiator_webhook(me, other, peer_url, webhook_port, max_turns, host="0.0.0.0"):
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

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=webhook_port, log_level="warning"))
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    webhook_url = f"http://127.0.0.1:{webhook_port}/push"
    print(f"{me.name} accepting callbacks on {webhook_url}")

    client = await create_client(peer_url)
    history = []
    message_times = []
    task_id = None
    context_id = None

    try:
        while True:
            if check_agreement(history) is not None or len(history) >= max_turns:
                return report_outcome(me, history, max_turns), history, message_times

            my_message = me.policy.respond(me, other, history, max_turns)
            print(f"  {my_message}")
            history.append(my_message)
            message_times.append(time.time())

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
                message_times.append(time.time())

            if final_state == TaskState.TASK_STATE_COMPLETED:
                return report_outcome(me, history, max_turns), history, message_times
    finally:
        server.should_exit = True
        await server_task


def build_responder_app(me, other, port, max_turns, on_resolved=None, on_task_started=None, advertise_host="127.0.0.1"):
    """The A2A server app, always wired for push notifications - dormant
    unless a caller actually registers a callback URL (run_initiator
    never does; run_initiator_webhook does). `on_resolved`/
    `on_task_started` are threaded through to NegotiationExecutor - see
    D17, D18.

    advertise_host (D32) is NOT the bind address (--host, which needs to
    be 0.0.0.0 in a container so anyone can connect in) - it's the
    address this robot tells PEERS to use when they dial back, embedded
    in the AgentCard's own interface.url. a2a-sdk's create_client()
    doesn't just connect to the peer_url it's given and stop there - it
    fetches the peer's AgentCard from that URL, then builds the actual
    JSON-RPC transport from the CARD's own self-reported url (confirmed
    by reading ClientFactory.create_from_url/create directly, not
    assumed). Left hardcoded to "127.0.0.1" for a long time since it
    never mattered locally (that's genuinely how a local peer reaches
    this process) - surfaced as a real bug the first time this ran
    across separate containers (D32/Phase 7): robot-a would fetch
    robot-b's card fine, then try to connect back to "127.0.0.1", which
    inside robot-a's own container just points at itself, and dial
    forever, "all connection attempts failed", burning an LLM call every
    retry."""
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
            AgentInterface(protocol_binding="JSONRPC", url=f"http://{advertise_host}:{port}", protocol_version="1.0")
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


def run_responder(me, other, port, max_turns, host="0.0.0.0", advertise_host="127.0.0.1"):
    """The --side b server on its own: always listening, reacting to
    whatever the initiator sends. See run_responder_async for the
    concurrent-with-a-movement-loop version (--world-url)."""
    import uvicorn

    app = build_responder_app(me, other, port, max_turns, advertise_host=advertise_host)
    print(f"{me.name} listening on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


async def run_responder_async(
    me, other, port, max_turns, on_resolved=None, on_task_started=None, host="0.0.0.0", advertise_host="127.0.0.1"
):
    """Same server, started as a background task instead of blocking the
    thread - so a movement loop can run alongside it. Returns the
    (server, task) pair; caller is responsible for server.should_exit +
    awaiting task on the way out, same pattern run_initiator_webhook uses
    for its own webhook receiver."""
    import uvicorn

    app = build_responder_app(
        me, other, port, max_turns, on_resolved=on_resolved, on_task_started=on_task_started, advertise_host=advertise_host
    )
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    print(f"{me.name} listening on http://{host}:{port}")
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


def write_negotiation_trace(history, message_times, comms_established_at, resolved_at):
    """Whichever robot ends up holding the full transcript - either side,
    now that initiation is dynamic (D18) - writes it out once the
    negotiation concludes, so visualize_network.py can render it
    afterward. No file at all if an episode never negotiates (a robot
    arrived at its boundary alone). See D19.

    comms_established_at/resolved_at are real time.time() timestamps
    (D22) - the negotiation channel has no shared clock with the world
    channel (D19's own honest caveat), so without these, the visualizer
    has no way to know *when* (in world-log terms) a negotiation actually
    happened - it was guessing from an unrelated signal (a robot's own
    boundary-crossing), which broke badly once the grid became
    asymmetric (D21) and a negotiation's real winner could be arbitrarily
    far from crossing its own boundary for a long time after the
    negotiation had already concluded.

    message_times (D29) is a real time.time() per entry in history,
    parallel by index - each message's own moment, not just the
    exchange's start/end - for tools (export_timeline_csv.py) that want
    to place individual messages on a shared real-time timeline with the
    world log, not just the negotiation as one block."""
    import json
    import os

    os.makedirs(os.path.dirname(NEGOTIATION_TRACE_PATH), exist_ok=True)
    messages = history_to_list(history)
    for message, sent_at in zip(messages, message_times):
        message["timestamp"] = sent_at
    with open(NEGOTIATION_TRACE_PATH, "w") as f:
        json.dump(
            {
                "messages": messages,
                "comms_established_at": comms_established_at,
                "resolved_at": resolved_at,
            },
            f,
            indent=2,
        )
    print(f"wrote {NEGOTIATION_TRACE_PATH}")


ROBOT_STATUS_PATH_TEMPLATE = "experiments/results/robot_status_{side}.jsonl"


async def run_robot(
    me,
    other,
    side,
    port,
    peer_url,
    world_url,
    max_turns,
    webhook_port=None,
    debug_log=False,
    host="0.0.0.0",
    advertise_host="127.0.0.1",
):
    """--world-url, dynamic initiation (D18): every robot always runs its
    own A2A server AND its own movement loop AND is capable of dialing
    the peer - the fixed --side a/--side b role split from Phase 6 is
    gone. Negotiation still only fires once this robot's own observation
    says it's at the boundary and can actually sense the other (D12's
    physical logic, unchanged) - arriving alone still claims priority for
    free, zero negotiation.

    The dial itself is run as a cancellable background task (not inline -
    the loop keeps polling/proposing while a dial is in flight). Dialing
    used to be jittered - a random delay meant to reduce how often both
    sides dial at once - removed in D30: it wasn't a correctness
    mechanism (races were always resolved safely regardless, see below),
    and it added real complexity for a benefit that shrank to nothing
    once the poll interval widened (a jitter window narrower than the
    poll interval can't actually spread anything - both sides still act
    on their very next poll regardless of the random draw). Three race
    scenarios, all handled - see D18, D27:
    1. An incoming task starts while this robot is ALREADY mid-dial ->
       on_task_started cancels the losing side's outbound attempt.
    2. An incoming task is already active (started, not yet resolved)
       when this robot's own next poll would otherwise decide to dial -
       incoming_active blocks a *second*, redundant dial from ever
       starting in the first place. Caught live: without this, a slow
       LLM negotiation left enough of a window for the responding side's
       own movement loop to independently start dialing the peer it was
       already mid-negotiation with.
    3. The tiebreak *winner* still receives the loser's incoming call
       before the loser manages to cancel its own dial (on_task_started
       only cancels the *losing* side's outbound attempt, by design) - so
       the winner ends up simultaneously dialing out AND responding to an
       incoming call for the very same standoff. on_resolved cancels this
       robot's own outbound dial the moment it learns the outcome via the
       incoming path, regardless of who won the tiebreak - once a real
       standoff resolves at all, the other, now-redundant negotiation
       attempt has nothing left to decide (D27).

    me.situation is kept live (D24): updated every loop iteration from
    the current MCP observation via compose_observation_from_dict(), so
    whichever role ends up calling the policy - this robot's own dial, or
    NegotiationExecutor reacting to an incoming one, both read the same
    shared Robot object - sees real position/sensing facts, not just the
    static private text. Without this, a networked LLM negotiated
    completely blind to position and distance - a real gap since Phase 5,
    only caught by a user noticing the model reasoning as if it had no
    idea where the other robot actually was.

    debug_log (D29, off by default) writes every real decision point
    below - the same moments already printed to stdout, structured - to
    ROBOT_STATUS_PATH_TEMPLATE.format(side=side), for export_timeline_csv.py
    to merge with the world log and negotiation trace on one real
    timeline. Off by default: a normal run doesn't need this file and
    shouldn't pay for it - requested directly, to avoid instrumenting
    every run just to occasionally debug one."""
    from mcp.client import Client

    priority_holder = {"value": None}
    dial_holder = {"task": None}
    incoming_active = {"value": False}
    comms_established_at = {"value": None}
    private_situation = me.situation

    status_log_path = ROBOT_STATUS_PATH_TEMPLATE.format(side=side) if debug_log else None
    if status_log_path:
        import os

        os.makedirs(os.path.dirname(status_log_path), exist_ok=True)
        open(status_log_path, "w").close()  # truncate - one file per run, not appended across runs

    def log_status(detail):
        if status_log_path is None:
            return
        import json

        with open(status_log_path, "a") as f:
            f.write(json.dumps({"timestamp": time.time(), "side": side, "detail": detail}) + "\n")

    def on_resolved(outcome, history, message_times):
        priority_holder["value"] = outcome
        incoming_active["value"] = False
        print(f"{me.name}: negotiated outcome (incoming task): {outcome}")
        log_status(f"negotiated outcome (incoming task): {outcome}")
        write_negotiation_trace(history, message_times, comms_established_at["value"], time.time())
        dial_task = dial_holder["task"]
        if dial_task is not None and not dial_task.done():
            print(f"{me.name}: outcome already known from an incoming call - cancelling my own outbound dial")
            log_status("cancelling my own outbound dial - outcome already known from an incoming call")
            dial_task.cancel()

    def on_task_started(peer_name):
        comms_established_at["value"] = time.time()
        incoming_active["value"] = True
        log_status(f"incoming task started from {peer_name}")
        dial_task = dial_holder["task"]
        if dial_task is not None and not dial_task.done() and not is_my_turn_to_initiate(me.name, peer_name):
            print(f"{me.name}: race - {peer_name} called in while I was dialing - deferring")
            log_status(f"race - {peer_name} called in while dialing - deferring")
            dial_task.cancel()

    server, server_task = await run_responder_async(
        me,
        other,
        port,
        max_turns,
        on_resolved=on_resolved,
        on_task_started=on_task_started,
        host=host,
        advertise_host=advertise_host,
    )

    try:
        async with Client(world_url) as world:
            while True:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                obs = await mcp_call(world, "get_observation", side=side)
                me.situation = compose_observation_from_dict(obs, other.name, private_situation)
                if obs["reached_target"]:
                    print(f"{me.name}: reached target")
                    log_status("reached target")
                    return

                if priority_holder["value"] is None and obs["at_boundary"]:
                    if not obs["sensed_other"]:
                        priority_holder["value"] = me.name
                        print(f"{me.name}: at boundary alone - claiming priority")
                        log_status("at boundary alone - claiming priority")
                    elif dial_holder["task"] is None and not incoming_active["value"]:
                        print(f"{me.name}: at boundary, sensing {other.name} - dialing")
                        log_status(f"at boundary, sensing {other.name} - dialing")
                        comms_established_at["value"] = time.time()
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
                            # fresh dial get scheduled on the next poll
                            print(f"{me.name}: dial failed ({error}) - will retry")
                            log_status(f"dial failed ({error}) - will retry")
                        else:
                            priority_holder["value"], dial_history, dial_message_times = dial_task.result()
                            log_status(f"dial resolved: {priority_holder['value']}")
                            write_negotiation_trace(dial_history, dial_message_times, comms_established_at["value"], time.time())
                    dial_holder["task"] = None

                action = decide_movement(obs, priority_holder["value"], me.name, other.name)
                await mcp_call(world, "propose_action", side=side, action=action)
    finally:
        server.should_exit = True
        await server_task


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="routine_vs_medical", choices=list(BY_ID))
    parser.add_argument("--side", required=True, choices=["a", "b"])
    parser.add_argument("--policy", default="llm", choices=list(POLICIES))
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
    parser.add_argument(
        "--debug-log",
        action="store_true",
        help="D29: write experiments/results/robot_status_<side>.jsonl, a real-time log of this robot's own decision points, for export_timeline_csv.py. Off by default - only turn on when actually debugging.",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Phase 7: 0.0.0.0 (not 127.0.0.1) so other containers on the same Compose network can reach this one - still reachable via localhost for plain local runs too.",
    )
    parser.add_argument(
        "--advertise-host",
        default="127.0.0.1",
        help="Phase 7/D32: the address embedded in THIS robot's own AgentCard for peers to dial back to - not the bind address (--host). In Compose, set this to the service name (e.g. robot-a) so a2a-sdk's create_client(), which connects using the peer's self-reported card URL rather than the peer_url string alone, doesn't try to reach back through 127.0.0.1 from inside another container.",
    )
    args = parser.parse_args()

    me, other = build_robots(args.side, args.scenario, args.policy)

    if args.world_url:
        print(f"{me.name} ({args.policy}) moving via {args.world_url}, ready to negotiate {args.scenario} at the boundary")
        webhook_port = args.webhook_port if args.webhook else None
        asyncio.run(
            run_robot(
                me,
                other,
                args.side,
                args.port,
                args.peer_url,
                args.world_url,
                args.max_turns,
                webhook_port,
                args.debug_log,
                args.host,
                args.advertise_host,
            )
        )
        return 0

    if args.side == "a":
        print(f"{me.name} ({args.policy}) initiating {args.scenario} against {args.peer_url}")
        if args.webhook:
            asyncio.run(run_initiator_webhook(me, other, args.peer_url, args.webhook_port, args.max_turns, host=args.host))
        else:
            asyncio.run(run_initiator(me, other, args.peer_url, args.max_turns))
    else:
        run_responder(me, other, args.port, args.max_turns, host=args.host, advertise_host=args.advertise_host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
