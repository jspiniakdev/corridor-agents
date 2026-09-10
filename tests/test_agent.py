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
from agent import build_robots, build_world_client, extract_reply, is_my_turn_to_initiate  # noqa: E402


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

    client = build_world_client("http://127.0.0.1:9500/mcp", auth=False)
    assert isinstance(client, Client)
