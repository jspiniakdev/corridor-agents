"""Tests for agent.py's pure helpers: build_robots() and extract_reply().
The actual per-turn decision logic lives in agent_executor.py now (tested
in test_agent_executor.py) - agent.py itself is mostly async network glue
(run_initiator/run_initiator_webhook/run_responder), manually verified
instead, same as every other script's main() in this project.
"""

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "src")

from a2a.helpers import new_data_message, new_task_from_user_message
from a2a.types.a2a_pb2 import Role, StreamResponse

from scenarios import SCENARIOS  # noqa: E402
from wire import message_to_dict  # noqa: E402
import asyncio  # noqa: E402

from agent import (  # noqa: E402
    MAX_LOCAL_WORLD_ATTEMPTS,
    WorldChannel,
    build_robots,
    build_world_client,
    extract_reply,
    is_my_turn_to_initiate,
)


def test_build_robots_side_a_gets_the_real_secret_and_b_is_a_placeholder():
    scenario = SCENARIOS[0]  # the test's own ground truth - a robot process never sees this
    me, other = build_robots("a", scenario.id, "stubborn")
    assert me.name == "Robot A"
    assert me.situation == scenario.a_situation
    assert me.urgency == scenario.a_urgency
    assert other.name == "Robot B"
    assert other.situation == ""
    assert other.urgency == 0


def test_build_robots_side_b_gets_the_real_secret_and_a_is_a_placeholder():
    scenario = SCENARIOS[0]
    me, other = build_robots("b", scenario.id, "always_yield")
    assert me.name == "Robot B"
    assert me.situation == scenario.b_situation
    assert me.urgency == scenario.b_urgency
    assert other.name == "Robot A"
    assert other.situation == ""
    assert other.urgency == 0


def test_extract_reply_reads_state_from_a_fresh_task_with_no_message_yet():
    from negotiation import Intent, Message

    opening = Message("Robot A", Intent.PROPOSE, "Robot A", "go")
    opening_a2a = new_data_message(message_to_dict(opening), role=Role.ROLE_USER)
    task = new_task_from_user_message(opening_a2a)

    state, parts = extract_reply(StreamResponse(task=task))

    assert state == task.status.state
    assert parts is None


def test_extract_reply_reads_state_and_message_from_a_status_update():
    from a2a.types.a2a_pb2 import TaskState, TaskStatus, TaskStatusUpdateEvent

    from negotiation import Intent, Message

    reply = Message("Robot B", Intent.ACCEPT, "Robot A", "fine")
    reply_a2a = new_data_message(message_to_dict(reply), role=Role.ROLE_AGENT)
    status_update = TaskStatusUpdateEvent(
        task_id="t1",
        context_id="c1",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED, message=reply_a2a),
    )

    state, parts = extract_reply(StreamResponse(status_update=status_update))

    assert state == TaskState.TASK_STATE_COMPLETED
    assert parts is not None


def test_extract_reply_returns_nothing_for_an_unrelated_event():
    assert extract_reply(StreamResponse()) == (None, None)


def test_is_my_turn_to_initiate_robot_a_wins_ties():
    assert is_my_turn_to_initiate("Robot A", "Robot B") is True
    assert is_my_turn_to_initiate("Robot B", "Robot A") is False


def test_build_world_client_plain_without_auth():
    """Phase 10b-3b: no --auth -> a bare Client(url), no token fetch, no
    network at construction. The --auth path (OIDC token on the MCP
    transport) needs GCP creds and is verified live, not here."""
    from mcp.client import Client

    client, aclose = build_world_client("http://127.0.0.1:9500/mcp", auth=False)
    assert isinstance(client, Client)
    assert callable(aclose)


class _FakeWorldClient:
    """Stand-in for an mcp Client used as `async with client as session`.
    One shared `script` of outcomes and a shared `pos` cursor across every
    client the channel builds (WorldChannel makes a fresh one per call).
    An Exception outcome is raised, anything else returned."""

    def __init__(self, script, pos):
        self._script = script
        self._pos = pos

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def call_tool(self, name, args):
        i = self._pos[0]
        self._pos[0] += 1
        outcome = self._script[min(i, len(self._script) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _channel(script, *, serve):
    script = list(script)
    pos = [0]
    spy = {"connects": 0, "transport_closed": 0, "tokens_minted": 0}

    def connect(url, auth, token):
        spy["connects"] += 1

        async def aclose():
            spy["transport_closed"] += 1

        return _FakeWorldClient(script, pos), aclose

    def fetch_token(audience):
        spy["tokens_minted"] += 1
        return f"tok-{spy['tokens_minted']}"

    async def sleep(_seconds):
        pass

    ch = WorldChannel(
        "http://world/mcp", auth=True, serve=serve, connect=connect, sleep=sleep, fetch_token=fetch_token
    )
    return ch, spy


def test_world_channel_recovers_from_transient_failures():
    """Two failures then success -> call_tool returns the value, having
    built a fresh (freshly-tokened) client for each attempt."""
    ch, spy = _channel([RuntimeError("boom"), RuntimeError("boom"), "ok"], serve=True)

    async def go():
        async with ch as world:
            result = await world.call_tool("get_observation", {"side": "a"})
            return result, spy["connects"]

    result, connects = asyncio.run(go())
    assert result == "ok"
    assert connects == 3  # one connect per attempt


def test_world_channel_fresh_client_per_call_cached_token():
    """Every call builds and closes its own client (no poisoned long-lived
    client), but the OIDC token is minted once and reused - not a blocking
    fetch every call."""
    ch, spy = _channel(["ok", "ok", "ok"], serve=True)

    async def go():
        async with ch as world:
            for _ in range(3):
                await world.call_tool("get_observation", {"side": "a"})
            return spy["connects"], spy["transport_closed"], spy["tokens_minted"]

    connects, closed, minted = asyncio.run(go())
    assert connects == 3
    assert closed == 3  # transport closed after each call, no leak
    assert minted == 1  # token cached across calls


def test_world_channel_remints_token_after_failure():
    """A failed call clears the cached token so the retry re-mints one -
    covers the case where the failure was a bad/expired token."""
    ch, spy = _channel([RuntimeError("401"), "ok"], serve=True)

    async def go():
        async with ch as world:
            await world.call_tool("get_observation", {"side": "a"})
            return spy["tokens_minted"]

    assert asyncio.run(go()) == 2  # one for the first attempt, a fresh one for the retry


def test_world_channel_local_run_gives_up():
    """Without --serve, a world that never answers raises after
    MAX_LOCAL_WORLD_ATTEMPTS instead of hanging."""
    ch, spy = _channel([RuntimeError("down")] * 20, serve=False)

    async def go():
        async with ch as world:
            await world.call_tool("get_observation", {"side": "a"})

    try:
        asyncio.run(go())
        assert False, "expected the call to raise"
    except RuntimeError as err:
        assert "down" in str(err)
    assert spy["connects"] == MAX_LOCAL_WORLD_ATTEMPTS


def test_world_channel_serve_run_never_gives_up():
    """With --serve, more consecutive failures than the local cap still
    recovers."""
    fails = [RuntimeError("down")] * (MAX_LOCAL_WORLD_ATTEMPTS + 5)
    ch, spy = _channel(fails + ["ok"], serve=True)

    async def go():
        async with ch as world:
            return await world.call_tool("propose_action", {"side": "a", "action": "wait"})

    assert asyncio.run(go()) == "ok"
    assert spy["connects"] == len(fails) + 1


def test_world_channel_retries_mid_call_cancellation():
    """A dead connection surfaces as a bare CancelledError from the mcp
    client's transport task group (not an MCPError). When our own task
    isn't being cancelled, that's a transport failure -> retry, don't
    crash. This is the exact shape that was crash-looping the deployed
    robots."""
    ch, spy = _channel([asyncio.CancelledError(), "ok"], serve=True)

    async def go():
        async with ch as world:
            return await world.call_tool("get_observation", {"side": "a"}), spy["connects"]

    result, connects = asyncio.run(go())
    assert result == "ok"
    assert connects == 2


def test_world_channel_retry_false_tries_once_then_raises():
    """D43: propose_action is called with retry=False so a re-sent 'move'
    can't apply twice - one attempt, then the error surfaces (the caller's
    poll loop re-proposes)."""
    ch, spy = _channel([RuntimeError("lost"), "ok"], serve=True)

    async def go():
        async with ch as world:
            await world.call_tool("propose_action", {"side": "a", "action": "move"}, retry=False)

    try:
        asyncio.run(go())
        assert False, "expected the call to raise"
    except RuntimeError as err:
        assert "lost" in str(err)
    assert spy["connects"] == 1  # tried once, no retry


def test_world_channel_backoff_is_capped_and_nondecreasing():
    from agent import WORLD_RETRY_CAP_SECONDS

    ch, _ = _channel(["ok"], serve=True)
    delays = [ch._backoff(i) for i in range(0, 12)]
    assert delays == sorted(delays)
    assert 0 <= min(delays)
    assert max(delays) <= WORLD_RETRY_CAP_SECONDS
