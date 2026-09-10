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
import uuid

sys.path.insert(0, "src")

import tracing  # noqa: E402 - Phase 10a, no-op unless --trace calls tracing.setup()

from negotiation import POLICIES, GeminiPolicy, LLMPolicy, Robot, check_agreement  # noqa: E402
from observation import compose_observation_from_dict  # noqa: E402
from scenarios import BY_ID, for_side  # noqa: E402
from wire import history_to_list, message_from_dict, message_to_dict  # noqa: E402

from agent_executor import NegotiationExecutor  # noqa: E402

POLL_INTERVAL_SECONDS = 1.0  # D30: how often a robot checks in with the world - governs movement, waiting, and negotiation-trigger cadence uniformly, not a separate "how long does moving take" model. Slept BEFORE each check-in (top of run_robot()'s loop), not after - so the first check-in is paced too, not a free instant action exempt from the interval. D43: this is a self-imposed politeness interval; the *hard* floor on movement speed is now the world's (world_store.MIN_MOVE_INTERVAL_SECONDS), which a robot can't poll its way past.

# WorldChannel resilience (D42 fix). The --auth OIDC token lasts ~1h; a --serve
# robot runs for days, so the world channel builds a fresh client per call and
# retries on failure rather than let a 401 - or the dead client's transport task
# group - crash the process. The token itself is cached (re-minted well before
# expiry, or on any failure) so we don't do a blocking token fetch every call.
WORLD_RETRY_BASE_SECONDS = 1.0
WORLD_RETRY_CAP_SECONDS = 30.0
WORLD_TOKEN_MAX_AGE_SECONDS = 2400  # re-mint the OIDC token after 40min - real lifetime is ~1h
MAX_LOCAL_WORLD_ATTEMPTS = 3  # without --serve, a dead world should surface after a few tries, not hang forever


def _make_policy(args):
    """Parsed CLI args -> a policy for this robot. The deterministic
    policies pass straight through as a name string (build_robots
    constructs them); llm and gemini need constructor config the
    POLICIES dict's zero-arg lookup can't carry (Phase 8 / D34, D36)."""
    if args.policy == "gemini":
        return GeminiPolicy(args.vertex_project, args.vertex_region, args.gemini_model)
    if args.policy == "llm" and args.vertex:
        return LLMPolicy(use_vertex=True, vertex_project=args.vertex_project, vertex_region=args.vertex_region)
    return args.policy


def build_robots(side, scenario_id, policy):
    """policy is a policy-name string (deterministic policies, built here)
    or an already-built instance (llm / gemini, see _make_policy). Only
    `me` gets a real policy; `other` is a placeholder with no secret and
    no policy - same isolation as D16. Only ever loads this robot's own
    situation/urgency via for_side(), never the paired Scenario object."""
    if isinstance(policy, str):
        policy = POLICIES[policy]()
    if side == "a":
        situation, urgency = for_side(scenario_id, "a")
        me = Robot("Robot A", situation, urgency, policy)
        other = Robot("Robot B", "", 0)  # placeholder only - real secret never held here
    else:
        situation, urgency = for_side(scenario_id, "b")
        me = Robot("Robot B", situation, urgency, policy)
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


def get_id_token(audience):
    """D33 (Phase 8): a real Google-signed OIDC ID token, scoped to
    `audience` (the exact URL of the Cloud Run service being called - the
    audience has to match precisely, since that's what the receiving
    service checks). Two genuinely different code paths, not one -
    confirmed live, not assumed: on Cloud Run itself, the attached
    service account resolves to compute-engine-style ADC, and the plain
    fetch_id_token() convenience function mints a token from the
    metadata server directly (the real production path, once a robot is
    deployed). Running locally via `gcloud auth application-default
    login --impersonate-service-account=...` (the only way to test this
    end-to-end before a robot is itself deployed) resolves to
    impersonated_credentials.Credentials instead - fetch_id_token()
    doesn't know what to do with that at all and raises
    DefaultCredentialsError ("neither metadata server or valid service
    account credentials are found"), so that case needs the explicit
    IDTokenCredentials wrapper instead."""
    import google.auth
    from google.auth import impersonated_credentials
    from google.auth.transport.requests import Request

    request = Request()
    credentials, _ = google.auth.default()

    if isinstance(credentials, impersonated_credentials.Credentials):
        id_credentials = impersonated_credentials.IDTokenCredentials(credentials, target_audience=audience, include_email=True)
        id_credentials.refresh(request)
        return id_credentials.token

    from google.oauth2 import id_token as google_id_token

    return google_id_token.fetch_id_token(request, audience)


def build_client_config(peer_url, auth):
    """None (a2a-sdk's own default) unless --auth is set - a plain local
    or Compose run has no GCP credentials configured at all and should
    never try to fetch a token it doesn't need. When set, hands
    create_client() an httpx.AsyncClient with the OIDC token already
    attached as a header - every request through it, including the
    initial AgentCard fetch, carries it automatically (D33)."""
    if not auth:
        return None
    import httpx
    from a2a.client import ClientConfig

    token = get_id_token(peer_url)
    return ClientConfig(httpx_client=httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}))


def world_token_audience(world_url):
    """Cloud Run validates a token's `aud` against the Service's base URL,
    not the request path - so the audience is scheme://host, even though
    the MCP endpoint itself is <base>/mcp. (D33's A2A --peer-url had no
    path, so this never came up before.)"""
    from urllib.parse import urlsplit

    parts = urlsplit(world_url)
    return f"{parts.scheme}://{parts.netloc}"


def build_world_client(world_url, auth, token=None):
    """The MCP client for the world channel, as (client, aclose). Plain
    Client(url) unless --auth (Phase 10b-3b): a deployed world_server.py
    Service is locked down with --no-allow-unauthenticated exactly like
    robot-b, so the robots' MCP calls need the same OIDC bearer token
    their A2A calls already carry. mcp's high-level Client takes a URL
    string but gives no header hook - so wrap the streamable-http
    transport in an httpx2.AsyncClient carrying the token.

    `token` lets a caller (WorldChannel) pass one it already has cached;
    left None, one is minted here. `aclose` is an async callable that
    closes any transport we own (a no-op without --auth) - the caller
    runs it after every call so a long-lived robot doesn't leak an httpx
    connection pool per call (D42)."""
    from mcp.client import Client

    async def _noop():
        pass

    if not auth:
        return Client(world_url), _noop

    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    if token is None:
        token = get_id_token(world_token_audience(world_url))
    authed = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    return Client(streamable_http_client(world_url, http_client=authed)), authed.aclose


class WorldChannel:
    """Makes every world MCP call ride out a token expiry, a world
    cold-start, or a dropped connection instead of letting the exception
    kill the process. Deployed robots (D41) were crash-looping once an
    hour on the ~1h --auth OIDC token expiring -> Cloud Run 401 until D42.

    A fresh client per call (with a cached token, re-minted well before
    the ~1h expiry or on any failure): a dead connection is just a failed
    connect on the next call - not a poisoned long-lived client whose
    internal transport task group drags our own task down with it when it
    dies (which is how the crash actually surfaced: a bare CancelledError,
    not an MCPError). serve=True retries forever with capped exponential
    backoff; serve=False raises after MAX_LOCAL_WORLD_ATTEMPTS so a local
    one-shot run surfaces a dead world instead of hanging.

    Duck-types Client.call_tool(name, args), so mcp_call and its call
    sites don't change. `connect` / `sleep` / `now` / `fetch_token` are
    injectable for tests."""

    def __init__(self, world_url, auth, *, serve, connect=build_world_client, sleep=None, now=None, fetch_token=None):
        self._world_url = world_url
        self._auth = auth
        self._serve = serve
        self._connect = connect
        self._sleep = sleep or asyncio.sleep
        self._now = now or time.monotonic
        self._fetch_token = fetch_token or get_id_token
        self._token = None
        self._token_at = None

    def _backoff(self, attempt):
        return min(WORLD_RETRY_BASE_SECONDS * (2 ** attempt), WORLD_RETRY_CAP_SECONDS)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def _current_token(self):
        """The cached OIDC token, re-minted when stale (or after a failure
        cleared it). Fetched off the event loop - get_id_token does
        blocking HTTPS (metadata server, or the IAM API for impersonated
        creds)."""
        if not self._auth:
            return None
        if self._token is None or self._now() - self._token_at > WORLD_TOKEN_MAX_AGE_SECONDS:
            self._token = await asyncio.to_thread(self._fetch_token, world_token_audience(self._world_url))
            self._token_at = self._now()
        return self._token

    async def _call_once(self, name, args):
        client, aclose = self._connect(self._world_url, self._auth, await self._current_token())
        try:
            async with client as session:
                return await session.call_tool(name, args)
        finally:
            await aclose()

    async def call_tool(self, name, args, *, retry=True):
        """retry=False (D43): try once, then raise. For propose_action -
        a re-sent "move" whose first response was merely lost would apply
        a second cell of movement; the caller's poll loop re-proposes from
        fresh ground truth anyway, so a failed propose is safe to just
        drop."""
        attempt = 0
        last_exc = None
        while True:
            # Run the call as its own task: if the mcp client's transport
            # task group dies mid-call it cancels *that* task, and awaiting
            # a cancelled task raises CancelledError here as a plain
            # exception without cancelling us - so we can tell it apart
            # from a real shutdown (which increments our own cancelling()).
            call = asyncio.ensure_future(self._call_once(name, args))
            try:
                return await call
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling() > 0:
                    call.cancel()
                    try:
                        await call
                    except BaseException:
                        pass
                    raise
                last_exc = ConnectionError("world connection dropped mid-call")
            except Exception as err:  # any world / transport / auth failure is retryable here
                last_exc = err
            self._token = None  # a failure might be a bad/expired token - re-mint on the next attempt
            attempt += 1
            if not retry or (not self._serve and attempt >= MAX_LOCAL_WORLD_ATTEMPTS):
                raise last_exc
            print(f"world call {name!r} failed ({last_exc!r}) - retrying, attempt {attempt}", flush=True)
            await self._sleep(self._backoff(attempt))


async def run_initiator(me, other, peer_url, max_turns, auth=False):
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

    client = await create_client(peer_url, client_config=build_client_config(peer_url, auth))
    history = []
    message_times = []
    task_id = None
    context_id = None

    with tracing.span("negotiation.episode", role="initiator", robot=me.name, peer=peer_url):
        while True:
            if check_agreement(history) is not None or len(history) >= max_turns:
                return report_outcome(me, history, max_turns), history, message_times

            with tracing.span("negotiation.turn", turn=len(history)):
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


async def run_initiator_webhook(me, other, peer_url, webhook_port, max_turns, host="0.0.0.0", auth=False):
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

    client = await create_client(peer_url, client_config=build_client_config(peer_url, auth))
    history = []
    message_times = []
    task_id = None
    context_id = None

    try:
        with tracing.span("negotiation.episode", role="initiator", robot=me.name, peer=peer_url, webhook=True):
            while True:
                if check_agreement(history) is not None or len(history) >= max_turns:
                    return report_outcome(me, history, max_turns), history, message_times

                with tracing.span("negotiation.turn", turn=len(history)):
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


def build_responder_app(me, other, port, max_turns, on_resolved=None, on_task_started=None, advertise_url=None):
    """The A2A server app, always wired for push notifications - dormant
    unless a caller actually registers a callback URL (run_initiator
    never does; run_initiator_webhook does). `on_resolved`/
    `on_task_started` are threaded through to NegotiationExecutor - see
    D17, D18.

    advertise_url (D32, D33) is NOT the bind address (--host, which needs
    to be 0.0.0.0 in a container so anyone can connect in) - it's the
    full URL this robot tells PEERS to use when they dial back, embedded
    in the AgentCard's own interface.url. a2a-sdk's create_client()
    doesn't just connect to the peer_url it's given and stop there - it
    fetches the peer's AgentCard from that URL, then builds the actual
    JSON-RPC transport from the CARD's own self-reported url (confirmed
    by reading ClientFactory.create_from_url/create directly, not
    assumed). Left hardcoded to "http://127.0.0.1:{port}" for a long
    time since it never mattered locally (that's genuinely how a local
    peer reaches this process) - surfaced as a real bug the first time
    this ran across separate containers (D32/Phase 7): robot-a would
    fetch robot-b's card fine, then try to connect back to "127.0.0.1",
    which inside robot-a's own container just points at itself, and dial
    forever, "all connection attempts failed", burning an LLM call every
    retry. A single hostname (D32's original --advertise-host) wasn't
    enough once Cloud Run entered the picture (D33/Phase 8): its URLs
    are HTTPS with no port at all (e.g.
    "https://robot-b-<project-number>.<region>.run.app"), which
    "http://{host}:{port}" can't represent - so this takes the whole URL
    now, defaulting to "http://127.0.0.1:{port}" (identical to today's
    behavior) when not given."""
    import httpx
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
    from a2a.server.tasks import BasePushNotificationSender, InMemoryPushNotificationConfigStore, InMemoryTaskStore
    from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
    from fastapi import FastAPI

    if advertise_url is None:
        advertise_url = f"http://127.0.0.1:{port}"

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
            AgentInterface(protocol_binding="JSONRPC", url=advertise_url, protocol_version="1.0")
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
    tracing.instrument_fastapi(app)  # Phase 10a - extracts the caller's traceparent; no-op unless --trace ran
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url="/"),
    )
    return app


def run_responder(me, other, port, max_turns, host="0.0.0.0", advertise_url=None):
    """The --side b server on its own: always listening, reacting to
    whatever the initiator sends. See run_responder_async for the
    concurrent-with-a-movement-loop version (--world-url)."""
    import uvicorn

    app = build_responder_app(me, other, port, max_turns, advertise_url=advertise_url)
    print(f"{me.name} listening on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


async def run_responder_async(
    me, other, port, max_turns, on_resolved=None, on_task_started=None, host="0.0.0.0", advertise_url=None
):
    """Same server, started as a background task instead of blocking the
    thread - so a movement loop can run alongside it. Returns the
    (server, task) pair; caller is responsible for server.should_exit +
    awaiting task on the way out, same pattern run_initiator_webhook uses
    for its own webhook receiver."""
    import uvicorn

    app = build_responder_app(
        me, other, port, max_turns, on_resolved=on_resolved, on_task_started=on_task_started, advertise_url=advertise_url
    )
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    print(f"{me.name} listening on http://{host}:{port}")
    return server, task


async def mcp_call(client, name, **args):
    """Call an MCP tool and unwrap its JSON payload - every world_server.py
    tool returns a dict serialized into the one text content block. The
    mcp.<name> span (10b-1) is the client end of the world channel; the
    httpx POST and world_server's own world.<name> span nest under it via
    traceparent."""
    import json

    scalars = {k: v for k, v in args.items() if isinstance(v, (str, int, float, bool))}
    with tracing.span(f"mcp.{name}", **scalars):  # record_negotiation's `messages` list isn't a valid span attr
        # propose_action isn't safe to blind-retry (D43): a re-sent "move"
        # could apply twice. The movement loop re-proposes next poll anyway.
        result = await client.call_tool(name, args, retry=(name != "propose_action"))
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


async def negotiate_as_initiator(me, other, peer_url, max_turns, webhook_port, auth=False):
    if webhook_port:
        return await run_initiator_webhook(me, other, peer_url, webhook_port, max_turns, auth=auth)
    return await run_initiator(me, other, peer_url, max_turns, auth=auth)


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
    payload = negotiation_trace_payload(history, message_times, comms_established_at, resolved_at)
    with open(NEGOTIATION_TRACE_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"wrote {NEGOTIATION_TRACE_PATH}")


def negotiation_trace_payload(history, message_times, comms_established_at, resolved_at):
    """The transcript in the shape both write_negotiation_trace (local
    file) and run_robot's record_negotiation MCP call (D39, so
    visualize_network.py can fetch it from the world) hand around:
    {"messages": [<wire dict + "timestamp">, ...], "comms_established_at",
    "resolved_at"}."""
    messages = history_to_list(history)
    for message, sent_at in zip(messages, message_times):
        message["timestamp"] = sent_at
    return {"messages": messages, "comms_established_at": comms_established_at, "resolved_at": resolved_at}


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
    advertise_url=None,
    auth=False,
    serve=False,
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
    every run just to occasionally debug one.

    serve (Phase 10b-3b, off by default) keeps the process alive after
    reaching target: a Cloud Run Service can't just exit. It idle-polls
    the world until reset_world() puts this robot back before its target
    (a fresh episode), clears the per-episode state, and runs the next
    one. Without --serve (a local run), the loop still returns on
    reached_target exactly as before."""
    priority_holder = {"value": None}
    dial_holder = {"task": None}
    incoming_active = {"value": False}
    comms_established_at = {"value": None}
    pending_trace = {"value": None}  # D39: a negotiation payload waiting to be handed to the world over MCP - the loop drains it (on_resolved is a sync callback and can't await)
    episode_holder = {"id": None}  # D44: the world's episode_id, captured from the first observation each episode; robot→world writes carry it, a change means reset_world() ran
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
        pending_trace["value"] = negotiation_trace_payload(history, message_times, comms_established_at["value"], time.time())
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
        advertise_url=advertise_url,
    )

    async def one_episode(world):
        """One robot's episode: poll the world, negotiate at the boundary
        if needed, move, return once this robot reaches its target."""
        with tracing.span("world.episode", robot=me.name, side=side):
            while True:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                with tracing.span("world.tick") as tick:
                    obs = await mcp_call(world, "get_observation", side=side)
                    me.situation = compose_observation_from_dict(obs, other.name, private_situation)
                    if tick is not None:
                        tick.set_attribute("world.position", obs["position"])
                        tick.set_attribute("world.at_boundary", obs["at_boundary"])

                    obs_episode = obs.get("episode_id")  # D44
                    if episode_holder["id"] is None:
                        episode_holder["id"] = obs_episode
                    elif obs_episode is not None and obs_episode != episode_holder["id"]:
                        # reset_world() ran under us (a re-trigger mid-episode) - bail;
                        # the --serve loop's wait_for_reset picks up the new episode cleanly
                        print(f"{me.name}: episode changed mid-run - restarting")
                        log_status("episode changed mid-run - restarting")
                        return

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
                                negotiate_as_initiator(me, other, peer_url, max_turns, webhook_port, auth=auth)
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
                                pending_trace["value"] = negotiation_trace_payload(
                                    dial_history, dial_message_times, comms_established_at["value"], time.time()
                                )
                        dial_holder["task"] = None

                    if pending_trace["value"] is not None:
                        # D39: hand the transcript to the world so
                        # visualize_network.py can fetch it (from Firestore,
                        # or over MCP) instead of a local file the deployed
                        # robot's container would never share. D44: tagged
                        # with the episode so a slow negotiation that
                        # resolved into the next episode can't clobber it.
                        await mcp_call(world, "record_negotiation", episode_id=episode_holder["id"], **pending_trace["value"])
                        pending_trace["value"] = None

                    action = decide_movement(obs, priority_holder["value"], me.name, other.name)
                    if tick is not None:
                        tick.set_attribute("world.action", action)
                    try:
                        # D43: not retried - a re-sent "move" could apply twice.
                        # D44: nonce makes an infra-level re-send idempotent;
                        # episode_id rejects a stray write from a finished episode.
                        await mcp_call(
                            world, "propose_action", side=side, action=action,
                            episode_id=episode_holder["id"], nonce=uuid.uuid4().hex,
                        )
                    except Exception as err:
                        # re-propose from fresh ground truth on the next poll
                        print(f"{me.name}: propose_action failed ({err!r}) - re-proposing next poll", flush=True)
                        log_status(f"propose_action failed ({err!r})")

    async def wait_for_reset(world):
        """--serve: idle-poll until reset_world() puts this robot back
        before its target, then clear the per-episode state so the next
        one starts clean."""
        print(f"{me.name}: idle - waiting for reset_world")
        log_status("idle - waiting for reset")
        while (await mcp_call(world, "get_observation", side=side))["reached_target"]:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        dt = dial_holder["task"]
        if dt is not None and not dt.done():
            dt.cancel()
        priority_holder["value"] = None
        dial_holder["task"] = None
        incoming_active["value"] = False
        comms_established_at["value"] = None
        pending_trace["value"] = None
        episode_holder["id"] = None  # D44: re-captured from the first observation of the new episode
        me.situation = private_situation
        print(f"{me.name}: world reset - new episode")
        log_status("new episode")

    try:
        async with WorldChannel(world_url, auth, serve=serve) as world:
            while True:
                await one_episode(world)
                if not serve:
                    return
                await wait_for_reset(world)
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
        "--advertise-url",
        default=None,
        help="Phase 7/D32, Phase 8/D33: the full URL embedded in THIS robot's own AgentCard for peers to dial back to - not the bind address (--host). Defaults to http://127.0.0.1:<port>, correct for plain local runs. In Compose, set to the service's own URL (e.g. http://robot-a:9001); on Cloud Run, its real HTTPS URL (no port) - a2a-sdk's create_client() connects using the peer's self-reported card URL, not the peer_url string alone, so this has to be the address that's actually reachable from wherever the peer runs.",
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Phase 8/D33, Phase 10b-3b: fetch a real OIDC ID token (audience = the target URL) and attach it as Authorization: Bearer on every outbound call - A2A to the peer, and (with --world-url) MCP to the world. Needed to reach a Cloud Run service locked down with --no-allow-unauthenticated. Off by default - a plain local or Compose run has no GCP credentials to fetch a token with and doesn't need one.",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Phase 10b-3b: with --world-url, keep running after reaching target - idle until reset_world() starts a fresh episode, then run it. For a Cloud Run Service (which can't just exit). Off by default: a local run still ends when the robot reaches its target.",
    )
    parser.add_argument(
        "--vertex",
        action="store_true",
        help="Phase 8/D34: with --policy llm, call Claude via Vertex AI instead of the direct Anthropic API - authenticates as this process's own GCP identity (ADC/impersonation), no API key at all. Off by default - local venv/Compose keep using ANTHROPIC_API_KEY unchanged. Requires --vertex-project.",
    )
    parser.add_argument(
        "--vertex-project",
        default=None,
        help="GCP project for Vertex-backed policies - --vertex (Claude on Vertex) and --policy gemini. E.g. corridor-agents.",
    )
    parser.add_argument(
        "--vertex-region",
        default="global",
        help="Vertex AI region/location for --vertex and --policy gemini (default: global, Vertex's own recommended region - not necessarily the region Cloud Run itself runs in)",
    )
    parser.add_argument(
        "--gemini-model",
        default="gemini-2.5-flash",
        help="Gemini model id for --policy gemini (Phase 8/D36). Default gemini-2.5-flash; verify against Vertex's live catalog.",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Phase 10a/D35: emit OpenTelemetry spans for the negotiation - episode, per-turn, per-LLM-call, and (on the responder) per handled message, linked into one trace across both robot processes. Off by default. Exporter from OTEL_TRACES_EXPORTER: console (default) or gcp (Cloud Trace).",
    )
    args = parser.parse_args()

    if args.vertex and not args.vertex_project:
        parser.error("--vertex requires --vertex-project")
    if args.policy == "gemini" and not args.vertex_project:
        parser.error("--policy gemini requires --vertex-project (Gemini is Vertex-hosted here)")

    if args.trace:
        tracing.setup(f"robot-{args.side}", args.scenario)

    try:
        return _run(args)
    finally:
        if args.trace:
            tracing.shutdown()


def _run(args):
    me, other = build_robots(args.side, args.scenario, _make_policy(args))

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
                args.advertise_url,
                args.auth,
                args.serve,
            )
        )
        return 0

    if args.side == "a":
        print(f"{me.name} ({args.policy}) initiating {args.scenario} against {args.peer_url}")
        if args.webhook:
            asyncio.run(
                run_initiator_webhook(
                    me, other, args.peer_url, args.webhook_port, args.max_turns, host=args.host, auth=args.auth
                )
            )
        else:
            asyncio.run(run_initiator(me, other, args.peer_url, args.max_turns, auth=args.auth))
    else:
        run_responder(me, other, args.port, args.max_turns, host=args.host, advertise_url=args.advertise_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
