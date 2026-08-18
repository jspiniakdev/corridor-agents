# Corridor Agents — working notes for Claude

## Read these first

- `docs/PLAN.md` — the project, the phases, the infra reasoning. The source of truth.
- `docs/DECISIONS.md` — why things were chosen, and what would change our mind.
- `docs/SETUP.md` — the working environment, and gotchas already hit.

These were written in a long design conversation that this session did not see.
Everything decided there lives in these files; treat them as the record.

Do not propose architecture that contradicts a decision in `DECISIONS.md` without
saying which decision it contradicts and why it should be revisited.

## Current state

**Phase 3 complete.** Two robots negotiate over a real 1D grid
(`src/world.py`, run via `simulate.py`) and their decision causes actual
movement, with collision structurally impossible (the reactive layer
re-derives it independently every tick). Negotiation only fires on a genuine
standoff - a robot at its boundary that can sense the other, even before the
other arrives - everything else resolves for free with zero LLM calls.
`world_eval.py`/`experiments/world_cases.csv` batch-measure grid episodes,
alongside `eval.py`/`experiments/cases.csv` (Phase 2) for plain negotiations.

**Phase 4 complete** (superseded by Phase 5 - kept for history). Each robot
ran as its own process negotiating over a deliberately dumb message board
(`comms_server.py`). See D14.

**Phase 5 complete.** Real A2A, peer to peer - `comms_server.py` is gone,
there's no third process at all anymore. Each `agent.py` process is either
a pure A2A client (`--side a`, the initiator, dials out) or a pure A2A
server (`--side b`, the responder, hosting
`agent_executor.NegotiationExecutor`). One negotiation is one A2A task;
`negotiation.py` is completely unchanged - only the transport around it
did. Three things got built this phase: plain request/response (agent
cards + task lifecycle, verified against `run.py`'s baseline exactly),
streaming (a `TASK_STATE_WORKING` heartbeat so a slow peer doesn't look
like a dead one), and webhooks (`--webhook`: the initiator registers a
callback and stops holding the connection open - the one piece that makes
Robot A a server too, not just a client). See D15.

**Phase 6 complete, including dynamic initiation and the network
visualizer** (both were deferred mid-phase, then built as follow-ons in
the same session). The world is a real MCP server (`world_server.py`) -
`get_map()`, `get_observation(side)`, `propose_action(side, action)`,
`get_log()`. `world.py`/`simulate.py` are completely untouched -
`world_server.py` reuses their `WorldState`/`reactive_filter`/`apply`
directly. `propose_action` is synchronous, no independent clock - the
world's authority narrows to exactly the one thing no single robot can
safely decide alone: is it safe to enter the shared corridor zone right
now. See D17.

**Dynamic initiation (D18, D27):** `--side a`/`--side b` no longer means
"client-only" vs "server-only" - every `agent.py --world-url` process
always runs its own A2A server *and* its own movement loop *and* is
capable of dialing the peer, the instant its own observation says it's
at the boundary and can sense the other (D12's physical logic,
unchanged). The dial is jittered and cancellable; a genuine race (both
sides dial before either sees the other's incoming call) is resolved by
a fixed name tiebreak cancelling the loser's outbound attempt. The
tiebreak *winner* also cancels its own outbound dial once `on_resolved`
tells it the outcome is already known via the loser's incoming call
(D27) - without this the winner could end up running two concurrent
negotiations for one standoff, a crash this project never observed until
D26 reverted to a grid small enough for real two-sided races to actually
happen. Verified live, repeatedly, including real caught races on both
sides of the tiebreak.

**The network visualizer (D19, D20, D22):** `visualize_network.py` is a
new post-hoc tool. Fetches the completed episode's log from
`world_server.py` (`get_log()`, real timestamp per entry), reads
`experiments/results/negotiation_trace.json` if a negotiation happened
(written by whichever robot's `agent.py` process learns the outcome, now
also carrying real `comms_established_at`/`resolved_at` timestamps), and
gets the winner from `negotiation.check_agreement()` directly rather than
inferring it from movement. `visualize_template.html` is shared with
Phase 3's `visualize.py`, not left fully untouched as first planned -
there's no shared clock anymore (D17), so "tick" would have been a lie
for network episodes; the template detects which shape it was given, and
network episodes now also show a distinct "establishing comms" marker
before the negotiation dialogue. **(D25)** consecutive idle "wait/wait"
polling rows are now collapsed into one labeled frame - `LLMPolicy`'s
synchronous API calls (D15) block the negotiating robot's whole process
for several real seconds, during which the *other* robot's process keeps
polling every ~0.2s and logging no-op rows; without collapsing, a replay
could show dozens of identical empty frames between "establishing comms"
and the actual dialogue.

**The grid was briefly widened and made asymmetric (D21), then reverted
(D26).** `MIN_POSITION`/`MAX_POSITION` are back to 1/8, `--max-ticks`
defaults back to 30 - that experiment is done. What stuck: `SENSOR_RANGE`
is no longer tuned to grid extremes at all. It's `len(CORRIDOR_ZONE) + 3`
(still 6, same value as before D21, now a physically-motivated formula
instead of a constant computed from `|A_BOUNDARY - B_START|`) - a robot
only senses (and therefore only negotiates with) the other robot once
it's close enough to the shared zone to be a real collision risk, not
from anywhere on the map. `world_eval.py`'s stats after reverting are
byte-identical to the original pre-D21 baseline.

**Timestamp-based negotiation attribution (D22) and distance-aware jitter
(D23):** the visualizer's original heuristic for "when did negotiation
happen" (first log entry where the winner moves off its own boundary)
broke on the asymmetric grid - fixed by having `world_server.py` and
`agent.py` log real `time.time()` and correlating them directly, instead
of inferring timing from movement. The same asymmetry exposed a second
issue: jitter (D18) was firing even when the other robot was 20+ steps
away and no real race was possible. `get_observation()` now reports
`other_distance_to_boundary`; `agent.py`'s `should_skip_jitter()` skips
the jitter delay entirely past `RACE_PLAUSIBLE_DISTANCE` (5) - not
safety-critical (D18's tiebreak still handles correctness regardless),
just removes a pointless delay.

**Live observation composition (D24):** a previously-hidden gap - Phase
3's `compose_observation()` (position/sensor facts, D4b's Job 4) was
never ported into the networked path at all. Every LLM negotiation since
Phase 5 ran on static private text alone, with zero awareness of its own
position or how far the other robot actually was. Caught by watching an
LLM's own stated reasoning miss something it should have known. Fixed
with `observation.py`'s `compose_observation_from_dict()` (the dict-based
equivalent, also carrying D23's `other_distance_to_boundary`), wired into
`agent.py`'s `run_robot()` so `me.situation` is recomposed from the live
MCP observation every loop iteration, using the original private text
captured once so it never compounds.

**Next: Phase 7 — containers** (Docker + Compose). See `PLAN.md` §5.

## How to run things

```bash
source .venv/bin/activate        # Python 3.13; required in each new shell
python -m pytest tests/ -q       # 98 tests, no API calls, ~0.03s
python run.py                    # one negotiation, deterministic policies
python run.py --a llm --b llm    # needs: cp .env.example .env && source .env
python eval.py                   # measurement sweep, deterministic cases only
python eval.py --full            # also runs the llm-involving cases
python simulate.py               # one grid episode, deliberate, deterministic
python simulate.py --no-deliberate --a llm --b llm  # FCFS baseline vs. negotiation
python world_eval.py             # grid-episode measurement sweep, free cases only
python world_eval.py --full      # also runs the deliberate llm-involving cases
python visualize.py              # tick-by-tick HTML replay of one Phase 3 (single-process) episode

# Phase 5: negotiation only, two terminals, same --scenario in each
python agent.py --scenario <id> --side b --policy always_yield --port 9001
python agent.py --scenario <id> --side a --policy stubborn --peer-url http://127.0.0.1:9001

# same, but the initiator doesn't hold the connection open - it registers a
# callback and waits to be called back instead:
python agent.py --scenario <id> --side a --policy stubborn --peer-url http://127.0.0.1:9001 --webhook

# Phase 6 + D18: three terminals - the world, then both robots. Both sides
# need --port/--peer-url now (every robot can dial and can be dialed):
python world_server.py --port 9500
python agent.py --scenario <id> --side b --policy always_yield --port 9002 --peer-url http://127.0.0.1:9001 --world-url http://127.0.0.1:9500/mcp
python agent.py --scenario <id> --side a --policy stubborn  --port 9001 --peer-url http://127.0.0.1:9002 --world-url http://127.0.0.1:9500/mcp

# D19: after the episode above finishes, render it (world_server.py must
# still be running - it holds the log)
python visualize_network.py --scenario <id> --a stubborn --b always_yield --world-url http://127.0.0.1:9500/mcp

# agent.py's --scenario/--policy default to routine_vs_medical/llm (the
# project's go-to demo case) - a bare `python agent.py --side a --port ...`
# now makes real Anthropic API calls and needs .env sourced; pass
# --policy stubborn explicitly for a free/deterministic smoke test.
```

## The one design principle

**Agents must have different information or different objectives.** If a change
would work just as well as a single prompt with all the context, it's wrong. The
fix is almost always to take information *away* from someone.

## Conventions that matter

- **Policy is pluggable** (D7). Anything that assumes an agent is an LLM is a
  bug. Deterministic policies must stay first-class — they're the baselines, the
  test fixtures, and the adversaries.
- **Messages are structured.** The decision lives in `goes_first`; `text` is
  prose commentary and must never carry the decision, or rule-based policies
  can't participate.
- **Ground truth stays hidden.** `Scenario.urgency` must never reach a prompt.
  There is a test asserting this — keep it passing. A networked robot process
  (`agent.py`) must never hold the full `Scenario` either — only
  `scenarios.for_side()`'s own-half slice (D16). Only the in-process tools
  (`run.py`/`eval.py`/`simulate.py`/`world_eval.py`) legitimately need both
  halves, to score correctness.
- **Latency is designed for the slow case.** Timeouts and retries assume
  LLM-speed responses even when tests use microsecond stubs.
- Prefer boring stdlib code. New dependencies need a reason.

## Pacing

This is a learning project, built one small step at a time. Prefer the smallest
change that leaves a working, runnable thing. Don't build ahead into later
phases — `PLAN.md` §7 lists what is deliberately not being built yet, and that
list is load-bearing.
