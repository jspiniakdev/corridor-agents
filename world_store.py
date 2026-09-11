#!/usr/bin/env python3
"""Phase 10b-2 (D38): where world_server.py keeps the two robots' positions.

Two backends behind one interface. `world.py`'s WorldState / reactive_filter
/ apply are reused untouched - only the *storage* changes:

- InMemoryWorldStore: one WorldState in this process's memory. Exactly
  today's behavior (D17). The default; every single-process local run and
  the whole test suite stay on this, no Firestore, no import of the
  google-cloud-firestore SDK.

- FirestoreWorldStore: the positions live in one Firestore document,
  `world/current`. A Cloud Run Service can run several instances and
  recycle them; an in-memory singleton doesn't survive that, and two
  robots on two instances would each see a stale world - defeating the
  reactive collision check (see world.py.reactive_filter), which is the
  whole point of the layer split (PLAN.md §4). propose() is a Firestore
  transaction: read the doc, run reactive_filter/apply, write back;
  Firestore retries on contention, so a second robot's proposal
  automatically re-evaluates against the first's committed move.

The only piece not exercised by the unit tests is FirestoreWorldStore's
~10 lines of transaction wiring (needs the emulator or real Firestore);
everything that decides an outcome - _resolve, the doc<->positions
round-trip - is pure and tested directly, and InMemoryWorldStore is
asserted to behave identically to the pre-D38 code.
"""

from __future__ import annotations

import sys
import time
import uuid

sys.path.insert(0, "src")

from negotiation import Robot  # noqa: E402
from world import (  # noqa: E402
    A_BOUNDARY,
    A_DIRECTION,
    A_START,
    A_TARGET,
    B_BOUNDARY,
    B_DIRECTION,
    B_START,
    B_TARGET,
    CORRIDOR_ZONE,
    MAX_POSITION,
    MIN_POSITION,
    RobotState,
    WorldState,
    apply,
    reactive_filter,
)

COLLECTION = "world"
DOCUMENT = "current"

# D43: the world's own hard floor on how fast a robot may advance - a "move"
# accepted less than this after that side's previous accepted move is refused
# ("too fast"), nothing applied. This is THE movement speed limit (agent.py's
# POLL_INTERVAL_SECONDS is just how often a robot checks in - 0.4, below this on
# purpose). It also absorbs a re-sent propose_action (a retried MCP call, a
# second Cloud Run instance) so a network hiccup can't inject a cell of movement
# the robot never decided.
MIN_MOVE_INTERVAL_SECONDS = 0.75


def _stale_entry(side: str, action: str, a_pos: int, b_pos: int, now: float) -> dict:
    """The 'this write is from a finished episode' marker (D44) - same
    shape as a real log entry (so callers unwrap it uniformly) but never
    logged or applied."""
    return {
        "side": side,
        "action": action,
        "resolved": "wait",
        "a_position": a_pos,
        "b_position": b_pos,
        "timestamp": now,
        "reason": "stale_episode",
    }


def grid_facts() -> dict:
    """The static grid constants, in the exact shape world_server.py's
    get_map() returns. Shared (D39) so visualize_network.py's --firestore
    path - which never calls get_map over MCP - builds the identical dict."""
    return {
        "min_position": MIN_POSITION,
        "max_position": MAX_POSITION,
        "corridor_zone": sorted(CORRIDOR_ZONE),
        "a_boundary": A_BOUNDARY,
        "b_boundary": B_BOUNDARY,
        "a_target": A_TARGET,
        "b_target": B_TARGET,
    }


def state_from_positions(a_position: int, b_position: int) -> WorldState:
    """Rebuild a WorldState from just the two positions - identical to
    world_server.py's old build_state(), but the positions come from the
    store instead of the A_START/B_START constants. The wrapped Robot is a
    throwaway placeholder: reactive_filter/apply only ever read
    .position / .direction / .in_zone, never .robot."""
    a = RobotState(Robot("Robot A", "", 0), a_position, A_DIRECTION, A_BOUNDARY, A_TARGET)
    b = RobotState(Robot("Robot B", "", 0), b_position, B_DIRECTION, B_BOUNDARY, B_TARGET)
    return WorldState(a, b)


def _resolve(
    state: WorldState,
    side: str,
    action: str,
    last_move_at: dict | None = None,
    now: float | None = None,
    min_interval: float = MIN_MOVE_INTERVAL_SECONDS,
) -> dict:
    """Run one robot's proposed action through the reactive safety filter
    and apply it to `state` (mutates in place). The other robot is fixed
    as "wait" - only one robot transitions per call, so there's nothing
    for it to race against (world_server.py's D17 model, unchanged).
    Returns the log entry, which carries everything the caller needs:
    the resolved action and both post-move positions.

    D43: if `last_move_at` (a {"a": ts|None, "b": ts|None} dict the store
    owns) is given and this side moved less than `min_interval` ago, the
    "move" is refused - resolved "wait", nothing applied, entry tagged
    reason="too_fast" - and `last_move_at[side]` is updated only on an
    accepted move. `last_move_at=None` skips the check entirely (the pure
    reactive-filter path, used by the direct unit tests)."""
    now = time.time() if now is None else now
    too_fast = (
        action == "move"
        and last_move_at is not None
        and last_move_at.get(side) is not None
        and now - last_move_at[side] < min_interval
    )
    if too_fast:
        resolved = "wait"
    elif side == "a":
        resolved, _ = reactive_filter(state, action, "wait")
        apply(state, resolved, "wait")
    else:
        _, resolved = reactive_filter(state, "wait", action)
        apply(state, "wait", resolved)
    if resolved == "move" and last_move_at is not None:
        last_move_at[side] = now
    entry = {
        "side": side,
        "action": action,
        "resolved": resolved,
        "a_position": state.a.position,
        "b_position": state.b.position,
        "timestamp": now,
    }
    if too_fast:
        entry["reason"] = "too_fast"
    return entry


class InMemoryWorldStore:
    """Today's behavior: one WorldState in this process's memory.

    min_move_interval / now (D43): the "too fast" floor and its clock,
    both injectable - tests pass min_move_interval=0 for the pure path or
    a fake `now` to exercise the throttle deterministically."""

    def __init__(self, min_move_interval: float = MIN_MOVE_INTERVAL_SECONDS, now=None):
        self._min_move_interval = min_move_interval
        self._now = now or time.time
        self.reset()

    def get_state(self) -> WorldState:
        return self._state

    def current_episode_id(self) -> str:
        return self._episode_id

    def propose(self, side: str, action: str, episode_id: str | None = None, nonce: str | None = None) -> dict:
        if episode_id is not None and episode_id != self._episode_id:
            return _stale_entry(side, action, self._state.a.position, self._state.b.position, self._now())
        if nonce is not None and self._nonces.get(side) == nonce:
            return self._last_result[side]  # a duplicate delivery - the original outcome, nothing re-applied (D44)
        entry = _resolve(
            self._state, side, action, self._last_move_at, self._now(), self._min_move_interval
        )
        self._state.log.append(entry)
        if nonce is not None:
            self._nonces[side] = nonce
            self._last_result[side] = entry
        return entry

    def get_log(self) -> list[dict]:
        return list(self._state.log)

    def set_negotiation(self, data: dict | None, episode_id: str | None = None) -> bool:
        if episode_id is not None and episode_id != self._episode_id:
            return False
        self._negotiation = data
        return True

    def get_negotiation(self) -> dict | None:
        return self._negotiation

    def reset(self) -> None:
        self._state = state_from_positions(A_START, B_START)
        self._negotiation = None
        self._last_move_at = {"a": None, "b": None}
        self._episode_id = uuid.uuid4().hex
        self._nonces = {"a": None, "b": None}
        self._last_result = {"a": None, "b": None}


class FirestoreWorldStore:
    """Positions in `world/current`; propose() is a transaction.

    project=None lets the SDK resolve it (ADC / GOOGLE_CLOUD_PROJECT on
    Cloud Run, any string plus FIRESTORE_EMULATOR_HOST locally). The doc
    is lazily created at start positions on first access."""

    def __init__(
        self,
        project: str | None = None,
        collection: str = COLLECTION,
        document: str = DOCUMENT,
        min_move_interval: float = MIN_MOVE_INTERVAL_SECONDS,
    ):
        from google.cloud import firestore

        self._firestore = firestore
        self._db = firestore.Client(project=project)
        self._doc = self._db.collection(collection).document(document)
        self._min_move_interval = min_move_interval

    def _ensure(self) -> None:
        if not self._doc.get().exists:
            self.reset()

    def get_state(self) -> WorldState:
        self._ensure()
        d = self._doc.get().to_dict()
        return state_from_positions(d["a_position"], d["b_position"])

    def current_episode_id(self) -> str | None:
        self._ensure()
        return self._doc.get().to_dict().get("episode_id")

    def propose(self, side: str, action: str, episode_id: str | None = None, nonce: str | None = None) -> dict:
        self._ensure()
        firestore = self._firestore
        doc = self._doc

        min_interval = self._min_move_interval

        @firestore.transactional
        def run(txn):
            snap = doc.get(transaction=txn).to_dict()
            if episode_id is not None and episode_id != snap.get("episode_id"):
                return _stale_entry(side, action, snap["a_position"], snap["b_position"], time.time())
            cached = snap.get(f"last_result_{side}")
            if nonce is not None and snap.get(f"nonce_{side}") == nonce and cached is not None:
                return cached  # duplicate delivery - the original outcome, nothing re-applied (D44)
            state = state_from_positions(snap["a_position"], snap["b_position"])
            last_move_at = {"a": snap.get("last_move_a"), "b": snap.get("last_move_b")}
            entry = _resolve(state, side, action, last_move_at, min_interval=min_interval)
            update = {
                "a_position": state.a.position,
                "b_position": state.b.position,
                "last_move_a": last_move_at["a"],
                "last_move_b": last_move_at["b"],
                "log": firestore.ArrayUnion([entry]),
            }
            if nonce is not None:
                update[f"nonce_{side}"] = nonce
                update[f"last_result_{side}"] = entry
            txn.update(doc, update)
            return entry

        return run(self._db.transaction())

    def get_log(self) -> list[dict]:
        self._ensure()
        return self._doc.get().to_dict().get("log", [])

    def set_negotiation(self, data: dict | None, episode_id: str | None = None) -> bool:
        self._ensure()
        if episode_id is not None and episode_id != self._doc.get().to_dict().get("episode_id"):
            return False
        self._doc.update({"negotiation": data})
        return True

    def get_negotiation(self) -> dict | None:
        self._ensure()
        return self._doc.get().to_dict().get("negotiation")

    def reset(self) -> None:
        self._doc.set(
            {
                "a_position": A_START,
                "b_position": B_START,
                "log": [],
                "negotiation": None,
                "last_move_a": None,
                "last_move_b": None,
                "episode_id": uuid.uuid4().hex,
                "nonce_a": None,
                "nonce_b": None,
                "last_result_a": None,
                "last_result_b": None,
                "reset_at": self._firestore.SERVER_TIMESTAMP,
            }
        )
