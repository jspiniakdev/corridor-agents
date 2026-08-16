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

**Phase 1 complete.** Two robots negotiate over a corridor in one process. No
world, no network, no cloud.

**Next: Phase 2 — make it measurable.** Run every scenario × policy pairing,
repeated, and record agreement rate, correctness, and messages used. See
`PLAN.md` §5.

## How to run things

```bash
source .venv/bin/activate        # Python 3.13; required in each new shell
python -m pytest tests/ -q       # 11 tests, no API calls, ~0.02s
python run.py                    # one negotiation, deterministic policies
python run.py --a llm --b llm    # needs: cp .env.example .env && source .env
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
  There is a test asserting this — keep it passing.
- **Latency is designed for the slow case.** Timeouts and retries assume
  LLM-speed responses even when tests use microsecond stubs.
- Prefer boring stdlib code. New dependencies need a reason.

## Pacing

This is a learning project, built one small step at a time. Prefer the smallest
change that leaves a working, runnable thing. Don't build ahead into later
phases — `PLAN.md` §7 lists what is deliberately not being built yet, and that
list is load-bearing.
