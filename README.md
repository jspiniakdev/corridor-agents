# Corridor Agents

Simulated robots that negotiate with each other to share physical space. Each
robot knows something private the others don't, and eventually each will be a
separate networked service.

A learning project: agent-to-agent protocols, agent design, and cloud infra —
introduced one layer at a time.

**Status: Phase 2 complete.** Two robots negotiate in one process, and a batch
harness runs many negotiations and measures how well they went. No world, no
network, no cloud.

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
run.py                    CLI for one negotiation
eval.py                   CLI for the measurement sweep (Phase 2)
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

## Next: Phase 3 — add the world

A grid, a corridor, robots that actually move. See `docs/PLAN.md` §5.
