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

**Phase 6 complete.** The world is a real MCP server (`world_server.py`) -
`get_map()`, `get_observation(side)`, `propose_action(side, action)`.
`world.py`/`simulate.py` are completely untouched - `world_server.py`
reuses their `WorldState`/`reactive_filter`/`apply` directly rather than
duplicating the safety-critical logic. `agent.py --world-url` makes a
robot also move through the grid, negotiating only once its own
observation says it's at the boundary and can sense the other robot
(matching Phase 3's original physical logic, D12) - `--side a` stays the
fixed initiator this phase, same as Phase 5. `propose_action` is
synchronous, no independent clock - the world's authority narrows to
exactly the one thing no single robot can safely decide alone: is it
safe to enter the shared corridor zone right now. See D17.

**Next: boundary-triggered dynamic initiation** (either robot can become
the initiator based on its own observation, with jitter + a name tiebreak
for the race case) **and the visualizer rebuild** - both deliberately
deferred out of Phase 6, their own follow-on plans. After that, Phase 7 —
containers (Docker + Compose). See `PLAN.md` §5.

## How to run things

```bash
source .venv/bin/activate        # Python 3.13; required in each new shell
python -m pytest tests/ -q       # 79 tests, no API calls, ~0.03s
python run.py                    # one negotiation, deterministic policies
python run.py --a llm --b llm    # needs: cp .env.example .env && source .env
python eval.py                   # measurement sweep, deterministic cases only
python eval.py --full            # also runs the llm-involving cases
python simulate.py               # one grid episode, deliberate, deterministic
python simulate.py --no-deliberate --a llm --b llm  # FCFS baseline vs. negotiation
python world_eval.py             # grid-episode measurement sweep, free cases only
python world_eval.py --full      # also runs the deliberate llm-involving cases
python visualize.py              # tick-by-tick HTML replay of one episode (Phase 3 grid only)

# Phase 5: negotiation only, two terminals, same --scenario in each
python agent.py --scenario <id> --side b --policy always_yield --port 9001
python agent.py --scenario <id> --side a --policy stubborn --peer-url http://127.0.0.1:9001

# same, but the initiator doesn't hold the connection open - it registers a
# callback and waits to be called back instead:
python agent.py --scenario <id> --side a --policy stubborn --peer-url http://127.0.0.1:9001 --webhook

# Phase 6: three terminals - the world, then both robots, --world-url in both
python world_server.py --port 9500
python agent.py --scenario <id> --side b --policy always_yield --port 9001 --world-url http://127.0.0.1:9500/mcp
python agent.py --scenario <id> --side a --policy stubborn --peer-url http://127.0.0.1:9001 --world-url http://127.0.0.1:9500/mcp
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
