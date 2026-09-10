"""Tests for agent_executor.py - the responder side of a Phase 5
negotiation. Builds real a2a Task/Message protobuf objects (via a2a's own
helpers), but never a real server or network call - a small stand-in
object with just .current_task/.message covers what history_from_context
actually reads, and the real (concrete) EventQueueSource covers what
NegotiationExecutor.execute() needs to run for real. Zero API calls, same
style as every other test in this project.
"""

import asyncio
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "src")

from a2a.helpers import new_task_from_user_message  # noqa: E402
from a2a.server.events.event_queue_v2 import EventQueueSource  # noqa: E402
from a2a.types.a2a_pb2 import Role, TaskState, TaskStatus  # noqa: E402

from negotiation import AlwaysYield, Intent, Message, Robot, Stubborn  # noqa: E402
from wire import message_to_dict  # noqa: E402
from agent_executor import NegotiationExecutor, history_from_context, should_respond  # noqa: E402


class FakeContext:
    """Stands in for a2a's RequestContext - history_from_context only
    ever reads .current_task and .message, so that's all this needs."""

    def __init__(self, current_task, message):
        self.current_task = current_task
        self.message = message


def to_a2a(message, role):
    from a2a.helpers import new_data_message

    return new_data_message(message_to_dict(message), role=role)


def test_history_from_context_creates_a_task_on_the_opening_message():
    opening = Message("Robot A", Intent.PROPOSE, "Robot A", "go")
    context = FakeContext(current_task=None, message=to_a2a(opening, Role.ROLE_USER))

    task, history = history_from_context(context)

    assert history == [opening]
    assert task.id


def test_history_from_context_folds_in_the_pending_reply_and_new_message():
    # This is the case the real probe server/client run caught: a task's
    # own pending status.message and the newest incoming message are NOT
    # in task.history yet by the time execute() runs - only the framework
    # folds them in, one call later.
    opening = Message("Robot A", Intent.PROPOSE, "Robot A", "go")
    task = new_task_from_user_message(to_a2a(opening, Role.ROLE_USER))
    pending_reply = Message("Robot B", Intent.PROPOSE, "Robot B", "no, me")
    task.status.CopyFrom(
        TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED, message=to_a2a(pending_reply, Role.ROLE_AGENT))
    )
    newest = Message("Robot A", Intent.ACCEPT, "Robot B", "fine")
    context = FakeContext(current_task=task, message=to_a2a(newest, Role.ROLE_USER))

    _, history = history_from_context(context)

    assert history == [opening, pending_reply, newest]


def test_should_respond_is_true_when_the_negotiation_is_open():
    assert should_respond([], max_turns=6) is True


def test_should_respond_is_false_once_agreement_is_reached():
    history = [
        Message("Robot B", Intent.PROPOSE, "Robot B"),
        Message("Robot A", Intent.ACCEPT, "Robot B"),
    ]
    assert should_respond(history, max_turns=6) is False


def test_should_respond_is_false_once_max_turns_is_reached():
    history = [Message("Robot A", Intent.PROPOSE, "Robot A")] * 6
    assert should_respond(history, max_turns=6) is False


async def _execute(executor, context):
    """EventQueueSource's constructor itself needs a running event loop
    (it schedules a background dispatcher task), so it has to be built
    inside the same async call as execute(), not passed in from outside."""
    await executor.execute(context, EventQueueSource())


def test_on_task_started_fires_once_with_the_peers_name_on_a_new_task():
    me = Robot("Robot B", "", 0, Stubborn())
    other = Robot("Robot A", "", 0)
    started_calls = []
    executor = NegotiationExecutor(me, other, max_turns=6, on_task_started=started_calls.append)

    opening = Message("Robot A", Intent.PROPOSE, "Robot A", "go")
    context = FakeContext(current_task=None, message=to_a2a(opening, Role.ROLE_USER))

    asyncio.run(_execute(executor, context))

    assert started_calls == ["Robot A"]


def test_on_task_started_does_not_fire_again_for_a_continuing_task():
    me = Robot("Robot B", "", 0, Stubborn())
    other = Robot("Robot A", "", 0)
    started_calls = []
    executor = NegotiationExecutor(me, other, max_turns=6, on_task_started=started_calls.append)

    opening = Message("Robot A", Intent.PROPOSE, "Robot A", "go")
    task = new_task_from_user_message(to_a2a(opening, Role.ROLE_USER))
    newest = Message("Robot A", Intent.PROPOSE, "Robot A", "still me")
    context = FakeContext(current_task=task, message=to_a2a(newest, Role.ROLE_USER))

    asyncio.run(_execute(executor, context))

    assert started_calls == []


def test_message_times_reset_per_task_so_serve_episodes_dont_leak_timestamps():
    """D44 fix: one executor, reused across --serve episodes (D40). Each
    new negotiation task must start message_times empty, or the previous
    episode's timestamps stay in the list (index-parallel to a history
    rebuilt fresh each task, so a stale list is never refilled) and leak
    into this episode's trace."""
    me = Robot("Robot B", "", 0, AlwaysYield())
    other = Robot("Robot A", "", 0)
    resolved = []
    executor = NegotiationExecutor(
        me, other, max_turns=6, on_resolved=lambda outcome, history, times: resolved.append((history, times))
    )

    def run_one_episode():
        opening = Message("Robot A", Intent.PROPOSE, "Robot A", "you go first")
        asyncio.run(_execute(executor, FakeContext(current_task=None, message=to_a2a(opening, Role.ROLE_USER))))

    run_one_episode()
    run_one_episode()

    for history, times in resolved:
        assert len(times) == len(history)  # not accumulated across episodes
    # both episodes are the same 2-message shape; without the reset the
    # second would carry 3+ timestamps for a 2-message history
    assert [len(t) for _, t in resolved] == [len(resolved[0][1])] * 2
