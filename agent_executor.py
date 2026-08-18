#!/usr/bin/env python3
"""Phase 5: bridges A2A's AgentExecutor interface into negotiation.py's
existing policy call - the responder side of a negotiation. See
docs/PLAN.md §5, D15.

One negotiation is one A2A task. The peer that calls in is always the
initiator (ROLE_USER); this robot only ever responds (ROLE_AGENT) - it
never opens a task itself. The framework does NOT fold a task's pending
status message or the newest incoming message into `task.history` until
the *next* call - confirmed by running a real probe server/client, not by
reading source alone (see D15) - so `history_from_context` reconstructs
the true chronological history by hand on every call.
"""

import sys

sys.path.insert(0, "src")

from a2a.helpers import get_data_parts, new_data_message, new_task_from_user_message  # noqa: E402
from a2a.server.agent_execution import AgentExecutor, RequestContext  # noqa: E402
from a2a.server.events import EventQueue  # noqa: E402
from a2a.server.tasks import TaskUpdater  # noqa: E402
from a2a.types.a2a_pb2 import Role  # noqa: E402

from negotiation import check_agreement  # noqa: E402
from wire import message_from_dict, message_to_dict  # noqa: E402


def history_from_context(context: RequestContext):
    """Rebuild the true message history for this task, including whatever
    hasn't been folded into task.history yet, and the task itself (created
    fresh if this is the opening message)."""
    task = context.current_task
    if task is None:
        task = new_task_from_user_message(context.message)
        raw_messages = list(task.history)
    else:
        raw_messages = list(task.history)
        if task.status.HasField("message"):
            raw_messages.append(task.status.message)
        raw_messages.append(context.message)
    history = [message_from_dict(get_data_parts(m.parts)[0]) for m in raw_messages]
    return task, history


def should_respond(history, max_turns):
    """Whether this robot has anything left to say - split out from the
    policy call itself so the executor can publish a streaming "working"
    heartbeat exactly between the two: after confirming there's a turn to
    take, before the (possibly slow) policy call that takes it."""
    return check_agreement(history) is None and len(history) < max_turns


class NegotiationExecutor(AgentExecutor):
    """One robot's responder role. `me`/`other` are the same Robot objects
    agent.py builds today; `other` stays a placeholder with no real secret,
    same isolation guarantee as Phase 4.

    `on_resolved`, if given, is called once with the negotiated outcome
    (a robot name, or None for a deadlock) the moment this side of the
    negotiation learns it. This robot never initiates a negotiation itself
    (Phase 6 keeps the fixed --side a convention, see D17) - it only ever
    reacts to an incoming task - so this callback is the one way its own
    world-movement loop finds out priority was decided at all."""

    def __init__(self, me, other, max_turns, on_resolved=None):
        self.me = me
        self.other = other
        self.max_turns = max_turns
        self.on_resolved = on_resolved

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task, history = history_from_context(context)
        if context.current_task is None:
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)

        if not should_respond(history, self.max_turns):
            # already agreed, or out of turns - nothing more to say
            if self.on_resolved:
                self.on_resolved(check_agreement(history))
            await updater.complete()
            return

        await updater.start_work()  # the streaming heartbeat: "still here, thinking"
        reply = self.me.policy.respond(self.me, self.other, history, self.max_turns)

        print(f"  {reply}")
        reply_message = new_data_message(
            message_to_dict(reply),
            context_id=task.context_id,
            task_id=task.id,
            role=Role.ROLE_AGENT,
        )
        outcome = check_agreement(history + [reply])
        if outcome is not None:
            if self.on_resolved:
                self.on_resolved(outcome)
            await updater.complete(message=reply_message)
        else:
            await updater.requires_input(message=reply_message)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")
