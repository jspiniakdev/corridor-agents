# Corridor Agents

Simulated robots that negotiate with each other to share physical space. Each
robot knows something private the others don't, and eventually each will be a
separate networked service.

A learning project: agent-to-agent protocols, agent design, and cloud infra —
introduced one layer at a time.

**Status: Phase 3 in progress.** Two robots negotiate over a real 1D grid and
their decision causes actual movement, with a structural guarantee they can
never collide. A batch harness measures plain negotiations (Phase 2); a batch
harness for grid episodes is next. No network, no cloud.

## Run it

```bash
python run.py                                  # one negotiation, deterministic, free, instant
python run.py --a never_yield --b never_yield  # watch them deadlock
```

For the LLM policy:

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
python run.py --a llm --b llm
python run.py --a llm --b never_yield          # LLM against an immovable peer
python run.py --scenario routine_vs_medical --a llm --b llm
```

To run the measurement sweep (Phase 2):

```bash
python eval.py          # every deterministic scenario x policy pairing, free, instant
python eval.py --full   # also the llm-involving cases - costs money, takes minutes
```

To run one grid episode (Phase 3):

```bash
python simulate.py                              # deliberate, deterministic policies
python simulate.py --no-deliberate               # FCFS baseline - no negotiation at all
python simulate.py --scenario routine_vs_medical --a llm --b llm
```

Tests need no API key and run in milliseconds:

```bash
pip install pytest && python -m pytest tests/ -q
```

## Layout

```
docs/PLAN.md              the project, the phases, the infra reasoning
docs/DECISIONS.md         why things were chosen
src/negotiation.py        messages, policies, the exchange loop
src/scenarios.py          corridor scenarios with hidden ground-truth urgency
src/world.py              the grid, the reactive/executive layers, the tick loop
src/observation.py        computes what a robot can see from position + sensors
run.py                    CLI for one negotiation
eval.py                   CLI for the measurement sweep (Phase 2)
simulate.py               CLI for one grid episode (Phase 3)
experiments/cases.csv     which scenario x policy pairings to run, and how many times
experiments/results/      eval.py's output, regenerable, gitignored
tests/                    deterministic tests, zero API calls
```

## What Phase 1 established

- **Policy is pluggable** (D7). `AlwaysYield`, `NeverYield`, `Stubborn` and
  `LLMPolicy` all implement the same interface, so baselines and LLM agents run
  on identical machinery and are directly comparable.
- **Messages are structured** — `{intent, goes_first, text}`. The decision lives
  in `goes_first` so rule-based policies can participate; `text` is commentary.
  This is FIPA-ACL's performative idea, rediscovered.
- **Ground truth is hidden.** Each scenario carries an `urgency` score that no
  policy ever sees. A test enforces this. It exists only so Phase 2 can ask:
  did the genuinely more urgent robot actually go first?

## What Phase 2 established

- **What to test is data, not code.** `experiments/cases.csv` lists every
  (scenario, policy_a, policy_b, repeats) combination to run; `eval.py` just
  executes whatever's in the file. Adding or changing what gets measured means
  editing a CSV, not the script.
- **"Correct" is `None`, not `False`, when no decision was made.** A tie
  scenario and a deadlock both leave `correct` blank rather than counted as
  wrong — conflating "did they even agree" with "was the agreement right"
  would make one metric move whenever the other did.
- **The first number:** `python eval.py` currently reports agreement rate,
  correctness rate (over decided episodes only), and average messages used
  across 45 deterministic episodes.

## What Phase 3 established (so far)

- **Collision is structurally impossible, not just avoided.** `reactive_filter`
  in `src/world.py` independently re-derives whether two proposed moves would
  put both robots in the corridor zone at once, regardless of what decided
  those moves — a bug upstream can cause starvation, never a collision.
- **Negotiation is the exception, not the rule.** Most of the time, whichever
  robot reaches its boundary first just claims priority and proceeds — zero
  LLM calls. The deliberative layer only fires on a genuine standoff: a robot
  at its boundary that can currently *sense* the other robot (within
  `SENSOR_RANGE`), even if the other hasn't arrived yet.
- **FCFS stays FCFS.** The sensor-based early trigger only applies when
  `deliberate=True`. `--no-deliberate` keeps the strict old rule — only an
  exact double-arrival is a real tie — since broadening it would corrupt what
  "no communication, strict arrival order" actually means.
- **The observation is computed, not fixed.** `src/observation.py` builds a
  robot's situation fresh at the moment of a standoff, from real position and
  sensor facts plus the same hand-authored private text scenarios.py always
  provided. Confirmed live: an LLM's own reasoning has directly echoed the
  computed "N cells from the corridor entrance" fact back in its argument.

## Next: batch evaluation for grid episodes

`eval.py` only measures plain negotiations (Phase 2) — it knows nothing about
`src/world.py`. A `simulate.py`-based batch sweep, producing the same kind of
agreement-rate/correctness/ticks-used numbers for grid episodes, is next.
