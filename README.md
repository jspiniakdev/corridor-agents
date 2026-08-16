# Corridor Agents

Simulated robots that negotiate with each other to share physical space. Each
robot knows something private the others don't, and eventually each will be a
separate networked service.

A learning project: agent-to-agent protocols, agent design, and cloud infra —
introduced one layer at a time.

**Status: Phase 1 complete.** Two robots negotiate in one process. No world, no
network, no cloud.

## Run it

```bash
python run.py                                  # deterministic, free, instant
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

Tests need no API key and run in milliseconds:

```bash
pip install pytest && python -m pytest tests/ -q
```

## Layout

```
docs/PLAN.md         the project, the phases, the infra reasoning
docs/DECISIONS.md    why things were chosen
src/negotiation.py   messages, policies, the exchange loop
src/scenarios.py     corridor scenarios with hidden ground-truth urgency
run.py               CLI
tests/               deterministic tests, zero API calls
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

## Next: Phase 2 — make it measurable

Run every scenario × policy pairing, repeated, and record agreement rate,
correctness, and messages used. That produces the first number, and everything
after is judged against it. See `docs/PLAN.md` §5.
