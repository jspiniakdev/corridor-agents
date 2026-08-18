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
import time

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

    `on_resolved`, if given, is called once with (outcome, history,
    message_times) - the negotiated outcome (a robot name, or None for a
    deadlock), the full message transcript, and a real time.time() per
    message (D29, parallel by index - self.message_times, extended
    lazily each call since history is rebuilt fresh from the a2a task
    every time, not accumulated locally) - the moment this side of the
    negotiation learns it. This is the one way a world-movement loop
    finds out priority was decided by an *incoming* negotiation (since
    that happens entirely inside execute(), not in the loop's own
    coroutine), and the only way it can write out a full trace file
    afterward - see D19.

    `on_task_started`, if given, is called once - with the peer's name -
    the instant a brand-new task arrives, *before* any policy call. This
    is earlier than on_resolved on purpose: it's how dynamic initiation
    (D18) detects a race (this robot also has its own outbound dial in
    flight) early enough to cancel it before wasting an LLM call, not
    after the incoming negotiation has already run its course."""

    def __init__(self, me, other, max_turns, on_resolved=None, on_task_started=None):
        self.me = me
        self.other = other
        self.max_turns = max_turns
        self.on_resolved = on_resolved
        self.on_task_started = on_task_started
        self.message_times = []  # D29 - real time.time() per history entry, index-parallel

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        is_new_task = context.current_task is None
        task, history = history_from_context(context)
        if is_new_task:
            await event_queue.enqueue_event(task)
            if self.on_task_started:
                self.on_task_started(self.other.name)

        # history is rebuilt fresh from the a2a task every call (D15's own
        # docstring above), not accumulated locally - so a message only
        # gets a timestamp the first time this process observes it. Not
        # exactly its author's send time, but real and monotonic, and the
        # only signal this side has for messages the peer authored.
        now = time.time()
        while len(self.message_times) < len(history):
            self.message_times.append(now)

        updater = TaskUpdater(event_queue, task.id, task.context_id)

        if not should_respond(history, self.max_turns):
            # already agreed, or out of turns - nothing more to say
            if self.on_resolved:
                self.on_resolved(check_agreement(history), history, list(self.message_times))
            await updater.complete()
            return

        await updater.start_work()  # the streaming heartbeat: "still here, thinking"
        reply = self.me.policy.respond(self.me, self.other, history, self.max_turns)
        self.message_times.append(time.time())

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
                self.on_resolved(outcome, history + [reply], list(self.message_times))
            await updater.complete(message=reply_message)
        else:
            await updater.requires_input(message=reply_message)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")
