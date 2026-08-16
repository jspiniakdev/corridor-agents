# Decision log

Short entries, appended as we go. The point is to remember *why*, so that in
three months we don't undo a choice without knowing what it cost.

Format: what we decided, what we rejected, why, and what would change our mind.

---

## D1 — Build the negotiation before building the world

**Decided:** Phase 1 is two agents talking, with no grid, no movement, no
simulation.

**Rejected:** building the simulator first and adding agents to it.

**Why:** the conversation is the point of the project; the world only exists to
give the conversation stakes. Building the world first is several days of work
before seeing whether the interesting part is interesting at all.

**Would change our mind:** if negotiation turns out to be trivially easy without
physical consequences — i.e. agents always agree instantly and sensibly. Then the
world is doing more work than expected and should come sooner.

---

## D2 — Homemade messaging before A2A

**Decided:** Phase 4 uses hand-rolled HTTP between agents. A2A arrives in
Phase 5.

**Rejected:** adopting A2A from the first networked version.

**Why:** a protocol you adopt before feeling the problems it solves is
cargo-culting. Agent cards, task lifecycle, and push notifications each answer a
specific pain, and the plan is to hit those pains first so the spec reads as
obvious.

**Cost of this choice:** some throwaway work in Phase 4. Accepted deliberately —
it's tuition, not waste.

---

## D3 — Cloud Run, not Vertex AI Agent Engine

**Decided:** each agent is a Cloud Run service.

**Rejected:** Agent Engine (managed sessions and memory), GKE, one shared VM.

**Why:** Cloud Run scales to zero (~$0 idle), gives each agent its own service
account — a real identity boundary rather than a name in a prompt — and speaks
plain HTTPS/SSE, which is what A2A needs. Agent Engine bills continuously
(~$0.086/vCPU-hour, ~$60/month for one warm vCPU, plus ~$0.25 per 1,000 session
events) and hides exactly the plumbing this project exists to learn.

**Would change our mind:** if session/memory management becomes the bottleneck
at Phase 10+, Agent Engine's Memory Bank is worth revisiting for that piece
specifically.

---

## D4 — LLMs never touch the control loop

**Decided:** three layers — reactive (deterministic, fast, in the simulator),
executive (plain code running an agreed plan), deliberative (LLM agents at
0.1–1 Hz).

**Rejected:** letting an agent emit movement commands directly.

**Why:** LLM latency is 1–5s; control loops run at 10–100 Hz. Mixing them is the
most common way LLM-robotics projects fail. The simulator will be steppable so
it can pause for deliberation during development.

**Would change our mind:** nothing. This one is structural.

---

## D4b — The simulator is dumb; negotiation never routes through it

**Decided:** only robots have LLMs. The simulator is deterministic code acting
as referee — ground truth, ticks, action validation, **deciding what each robot
may observe**, and outcome detection. Robot↔world uses MCP; robot↔robot uses
A2A, directly, peer to peer.

**Rejected:** routing negotiation messages through the simulator (convenient,
since it already talks to everyone).

**Why:** a simulator that relays negotiation is a central coordinator wearing a
disguise, and centralized coordination is the thing this project exists to
avoid. Keeping the channels separate also keeps the protocols honest: a peer you
bargain with and a tool you query are different relationships.

**Note:** the observation function — deciding what each robot can see — is the
most consequential non-LLM code in the project. It *is* the information
asymmetry.

**Would change our mind:** modelling radio as a physical resource (range,
packet loss) would give the world a legitimate role in mediating comms. That's a
Phase 10 experiment, not a starting design.

---

## D5 — Anthropic API first, Vertex AI at Phase 7

**Decided:** direct Anthropic API through Phase 6; switch to Claude on Vertex AI
when deploying.

**Rejected:** starting on Vertex AI.

**Why:** one environment variable versus a GCP auth setup that buys nothing
until there's something deployed to authenticate. The migration is small once
the rest of the stack is already GCP.

---

## D7 — Policy is pluggable; build the harness with deterministic agents

**Decided:** an agent is an identity + an interface + a policy. The policy —
LLM, rule-based, scripted, human — is invisible to everything outside the agent.
Phases 3–9 get built and tested against **deterministic** policies; LLMs are
swapped in afterwards.

**Rejected:** treating "agent" and "LLM" as the same thing, and writing the
baselines as a separate non-agent code path.

**Why:** A2A is explicitly a protocol for *opaque* agentic applications, so this
is working with the grain rather than against it. The payoffs are concrete —
baselines run on identical infrastructure (removing "is the comparison fair?"
doubt), mixed fleets become a free experiment, the whole infra ladder costs zero
tokens to build, and scripted policies give debugging a control group.

**Consequences to design for now:**

- **Latency:** timeouts and retries must be built for LLM-speed responses even
  while testing with microsecond stubs, or the system will pass all tests and
  fail on first contact with a real model.
- **Message schema:** must be structured (`{intent, claim, commitment,
  free_text?}`) so non-LLM policies can participate. Free-form prose alone would
  lock out every deterministic policy — and structured messages make evals
  easier anyway.

---

## D6 — Measure before optimizing

**Decided:** Phase 2 produces a metric before the world, the network, or the
protocol exist.

**Why:** LLM non-determinism means single runs are noise. Without seeded,
repeated scenarios, every later "improvement" is unfalsifiable, and months can
go into chasing phantoms.

---

## D8 — Force structured output for LLMPolicy instead of prompt + regex parsing

**Decided:** `LLMPolicy` sends a forced tool call (`tool_choice={"type": "tool",
"name": "respond"}`) with a JSON schema, instead of asking for JSON in the
system prompt and regex/`json.loads`-parsing free text out of the reply. The
schema's `goes_first` field is an enum built per call from the two real robot
names in that negotiation, not a generic string.

**Rejected:** the original approach — `SYSTEM` prompt instructing "reply with
ONLY a JSON object," then `LLMPolicy._parse` pulling a `{...}` blob out of the
raw text with regex, `json.loads`-ing it inside a try/except, and manually
checking `goes_first` against the two known names to drop a hallucinated one.

**Why:** the schema constrains the model's output at the API level instead of
hoping the prompt is followed. Concretely: less code (no regex extraction, no
JSON-decode try/except, no free-text fallback branch), and a hallucinated
third robot name is no longer something to catch after the fact — it isn't a
valid tool call in the first place, because the enum only contains the two
robots actually in this negotiation.

**What's kept anyway:** one defensive line in `_to_message` re-checking that
`goes_first` is one of the two real names before trusting it. The schema
should make a violation impossible, but there's no hard guarantee every
constraint is enforced API-side, and the check costs nothing.

**Would change our mind:** if a future policy needs genuinely free-form
output (not a structured decision plus a `text` field alongside it), forced
tool-use would need to loosen or go away for that policy.
