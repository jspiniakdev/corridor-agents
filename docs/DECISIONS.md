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

---

## D9 — What to measure is data (`cases.csv`), not code

**Decided:** `experiments/cases.csv` lists every (scenario, policy_a, policy_b,
repeats) combination `eval.py` should run. `eval.py` just reads the file,
skips any row touching the `llm` policy unless `--full` was passed, and runs
the rest. The file's initial ~80 rows were generated once by a throwaway
script (every deterministic pairing × every scenario, plus every
llm-touching pairing × every scenario × 3 repeats) and then committed as a
plain static file — nothing in the codebase regenerates it automatically.

**Rejected:** generating the combinations inside `eval.py` itself (e.g. an
`itertools.product` over policies and scenarios at runtime).

**Why:** what gets measured should be editable without touching code. Adding
one weird pairing, dropping a scenario from the sweep, or bumping an LLM
pairing's repeat count is a spreadsheet edit, not a Python change. It also
means the file can be inspected on its own to see exactly what "the sweep"
currently covers.

**Would change our mind:** if the case list needed to be generated
dynamically from something that changes often (e.g. scenarios themselves
being generated per run rather than hand-authored), a static file would stop
making sense.

---

## D10 — `correct` is `None`, not `False`, when no decision was reached

**Decided:** in `eval.py`'s per-episode result row, `correct` is `None`
whenever `should_go_first` is `None` (a genuine tie — no ground truth to be
right or wrong about) **or** the robots never agreed at all (a deadlock).
Only an actual `agreed_on` decision gets scored `True`/`False` against
`should_go_first`.

**Rejected:** scoring a deadlock as `correct=False` on the reasoning that
"the truly urgent robot didn't get priority."

**Why:** PLAN.md §9 tracks agreement rate and correctness as separate
metrics on purpose. Folding deadlocks into `correct=False` would make the
correctness rate move every time the agreement rate moves, for no new
information — it's already captured by "did they agree" and shouldn't also
silently degrade "was the decision right," which should only be about
episodes where a decision actually happened.

**Would change our mind:** if a future phase specifically wants a metric like
"did the truly urgent robot end up going first, counting deadlock as a loss"
— that would be a new, explicitly named metric, not a redefinition of
`correct`.

---

## D12 — Phase 3: collision prevented structurally, negotiation only on genuine standoff

**Decided:** `src/world.py`'s reactive layer (`reactive_filter`) independently
re-derives whether two proposed moves would put both robots in the corridor
zone at once, regardless of what decided those moves — so collision is
structurally impossible, not merely avoided by trusting the layer above it.
Separately, the deliberative layer (real negotiation) only fires when a robot
at its boundary can currently *sense* the other robot (within
`SENSOR_RANGE`), not only on an exact simultaneous double-arrival. Once
either robot reaches its boundary alone with nothing else nearby, it simply
claims priority and proceeds — zero LLM calls, matching `PLAN.md` §4.3.

**Rejected:** two things.
1. Trusting the executive layer's move/wait decision without independent
   verification — a bug there could otherwise cause a real collision, not
   just a wrong-but-safe outcome.
2. Requiring an exact simultaneous double-arrival to trigger negotiation.
   With the real start positions (A=1, B=8) and `CORRIDOR_ZONE={3,4,5}`, A
   structurally reaches its boundary one tick before B always does — under
   the exact-arrival rule, negotiation *never* fired in a real episode, only
   when both robots were manually placed at their boundaries for a test.

**Why:** the reactive layer's guarantee needs to hold even if the executive
layer is wrong, later, for reasons nobody anticipated - re-deriving
independently from actual positions is what makes it structural rather than
trusted. The sensor-range trigger fixes a real, confirmed bug (via a live
`simulate.py` run) without needing to hand-tune start positions for
artificial symmetry - it generalizes to any starting configuration where the
robots come within sensing range of each other, not just a perfectly
synchronized one.

**Consequence:** `deliberate=False` (FCFS) explicitly does NOT get the
sensor-based broadening - it keeps the old strict rule (only an exact
double-arrival is a real tie). Applying the same broadening there would
corrupt what FCFS means: "no communication, strict arrival order." A robot
that genuinely arrived first should stay the winner under FCFS, not get
overridden by a tie-break rule it didn't actually tie for.

**Would change our mind:** if a future scenario needs robots to reason about
conflicts from much further away than one sensor-range's worth of lead time
— that would need a genuinely different mechanism (e.g. broadcasting intent),
not just a larger constant.

---

## D11 — Drop `REJECT`; disagreeing is just proposing again

**Decided:** `Intent` has three values now, not four - `inform`, `propose`,
`accept`. There is no `reject`. A robot that disagrees with the standing
proposal simply sends a new `propose` naming itself (or whoever it thinks
should go first) instead. `goes_first` is also now a *required* field in
`LLMPolicy`'s tool schema, not just `intent` and `text`.

**Rejected:** the original four-intent design, where disagreeing meant
sending `reject` (with `goes_first` as unstructured flavor, not a real
standing position).

**Why:** `standing_proposal()` only ever tracked `PROPOSE` messages. Under
the old design, a robot that "insisted on itself" via `REJECT`
(`NeverYield`, `Stubborn`, and - naturally, since the prompt offered it as
an option - `LLMPolicy` too) never actually put a new position on the
table. A later `ACCEPT` of that position had nothing valid to match against,
so it silently failed to register as agreement and the negotiation burned
through the remaining turns instead. This was caught directly: `Stubborn`
vs `Stubborn` "agreed" after 6 messages, but on inspection that agreement
was Robot A matching its own six-message-stale first proposal, not
Robot B's actual (rejected-and-reasserted) position - a bug wearing the
costume of a resolved negotiation. The fix removes the second, untracked
way of expressing a position, so there's only one - `PROPOSE` - and
`_check_agreement` (unchanged) always has something real to match against.

**Consequence:** deterministic baselines changed behavior. `Stubborn` vs
`Stubborn` now resolves in 5 messages (was 6) and agrees on Robot B (was
Robot A, incorrectly). The Phase 2 default sweep's numbers moved too:
agreement rate 78% → 89%, since several pairings that used to falsely
deadlock now resolve correctly.

**Would change our mind:** nothing foreseen - `REJECT` added no real
information `PROPOSE` couldn't already carry, since `goes_first` was always
the field that mattered.

---

## D13 — Build a visualizer now, ahead of PLAN.md §7's own schedule

**Decided:** add a single-episode HTML replay (`visualize.py` +
`visualize_template.html`) that animates a grid episode tick by tick,
pausing to show the negotiation dialog when one happens.

**Rejected:** waiting, per `PLAN.md` §7's explicit "not doing yet" list:
`❌ A web UI or visualizer — this is a reward, not a prerequisite`.

**Why we're crossing that line now anyway:** that line was written when
there was nothing worth looking at - Phase 1/2 had no world, no movement,
nothing spatial to animate. Phase 3 changed that: there's now real per-tick
grid state and, when a genuine standoff happens, a full negotiation
transcript. And it costs nothing infrastructurally - a pre-computed replay
(chosen deliberately over a live server) is one self-contained HTML file
with the episode data inlined, no server process, no new dependency,
consistent with every other phase's "no infra until it's earned" pattern.
This isn't the web UI/dashboard §7 was deferring (multi-episode, persistent,
a real frontend stack) - it's a single-episode debugging aid built the same
way everything else in this project has been: boring, additive, zero new
infrastructure.

**Would change our mind:** if this grows into needing a live server, a
build step, or a JS framework to stay useful - at that point it would
actually be the thing §7 deferred, and should wait for whatever phase
Cloud Run/A2A get established, rather than becoming a second, ungoverned
infra track.

---

## D14 — Phase 4: a dumb message board, not a referee that drives negotiation

**Decided:** `comms_server.py` is a stateless, negotiation-unaware append-only
message board (`GET`/`POST /messages`) — it doesn't know what a turn is or
when an agreement happened. Each robot runs as its own process (`agent.py`)
that polls the board and independently computes its own turn from
`len(history) % 2` (the same alternation `negotiate()` already used), checks
`check_agreement()` itself, and speaks using its own local, real policy. No
third party ever makes a negotiation decision.

**Rejected:** the first design that was actually built partway through
planning - a `RemotePolicy` wrapping an HTTP call, plus a `run_networked.py`
driver script that called both robots' endpoints in turn, held the canonical
conversation, and decided when they'd agreed.

**Why:** working through that design conversationally surfaced that the
external driver was a central coordinator wearing a disguise - the exact
thing D4b already named for the world channel, just recreated on the
negotiation channel instead. If these were real robots, nothing would play
that role; each robot has to decide its own turn and its own agreement
independently. The fix that makes independent polling safe with zero
coordination machinery: since a message gets appended to the shared history
on every turn, `len(history)` at any moment tells both robots whose turn is
next (even → A, odd → B) - no locks, no race conditions, nothing to
coordinate, because both robots compute the same number from the same shared
state.

**One small, honest exception to "zero diff to negotiation.py":** each
robot's loop needs to call the agreement check directly, since there's no
external driver left to alternate turns and call it centrally. That function
existed as `_check_agreement` - a leading-underscore "internal" name, at the
time only called inside `negotiate()` and imported directly by one test.
Once real runtime code (`agent.py`) depends on it too, the underscore was
actively misleading, so it was renamed to `check_agreement`. One-line rename
plus its one call site and one test import; behavior unchanged.

**Consequence:** `RemotePolicy` and the passive `agent_server.py` design
never shipped - `agent.py` calls `me.policy.respond(...)` directly, the same
as every in-process script. `negotiate()` itself is untouched and still
drives every in-process script (`run.py`, `eval.py`, `simulate.py`,
`world_eval.py`) unchanged; the networked case reuses its smaller building
blocks (`check_agreement`) directly instead of being driven by it.

**Would change our mind:** if a future phase needs a real handshake before
negotiation starts (agreeing on which scenario, confirming both sides are
up) - that's explicitly deferred to Phase 5's A2A agent cards, not something
to bolt onto this dumb board.

---

## D15 — Phase 5: real A2A, staged (core swap, then streaming, then webhooks)

**Decided:** `comms_server.py` is deleted, not adapted - A2A has no
message-board concept, so there's no longer a third process at all. Each
robot is its own process (`agent.py`) that's *either* a pure A2A client
(`--side a`, the initiator) *or* a pure A2A server (`--side b`, the
responder, hosting `agent_executor.NegotiationExecutor`) for a given
negotiation. One negotiation is one A2A task; the initiator convention
reuses Phase 4's `--side` split unchanged - side `a` always dials, exactly
like `len(history) % 2 == 0` already meant "A goes first." Built in three
stages, matching how Phase 4 was actually built (incrementally, not all at
once): plain request/response first, verified against `run.py`'s baseline;
then streaming; then webhooks - all three ended up built this phase.

**Rejected:** the original Phase 4 alternative of routing negotiation
through a relay-like third process - already rejected once for the
homemade board (D14) and doesn't reappear here; A2A's peer-to-peer model
makes that mistake structurally unavailable this time; there's no shared
process to accidentally lean on.

**A real finding that changed the design mid-build:** research into
`a2a-sdk`'s actual API (v1.1.2, protobuf-based - `a2a.types.a2a_pb2`, not
the plain-dataclass shape some older blog posts show) turned out
insufficient on its own, the same way reading `negotiation.py` alone
wouldn't have caught D11's `REJECT` bug. A live probe server/client caught
something the source didn't make obvious: a task's own pending
`status.message` (this robot's last reply) and the newest incoming
`context.message` are **not** folded into `task.history` until the
*following* call - only the framework's internal bookkeeping does that,
one call late. `agent_executor.history_from_context()` reconstructs the
true chronological history by hand every call
(`task.history + [status.message if present] + [context.message]`) rather
than trusting `task.history` alone. Confirmed with three live calls in a
row, watching the real object state change each time - not inferred from
documentation.

**Streaming:** the responder publishes one `TASK_STATE_WORKING` heartbeat
(`updater.start_work()`) before calling the (possibly slow) policy, so the
initiator can distinguish "peer is thinking" from "peer is down" - both
looked identical under Phase 4's plain request/response. Deliberately
narrow: this streams task *lifecycle* events, not the LLM's partial
output token-by-token - a coarse liveness pulse, not a content-streaming
feature. `should_respond()` was split out of what used to be
`agent_executor.decide()` specifically so the executor can publish that
heartbeat at the exact point between "confirmed there's a turn to take"
and "the policy call that takes it."

**Webhooks:** the initiator (`--side a --webhook`) registers a callback
URL and sends with `configuration.return_immediately=True`, gets back only
the task's creation ack, and does not hold the connection open - it awaits
its own tiny inbound receiver instead. This is the one piece that makes
Robot A a server too, not just a client, which is a real architectural
cost the plan flagged before building it (see the "webhook scope"
check-in). The responder (`run_responder`) is unconditionally wired with a
`PushNotificationConfigStore`/`PushNotificationSender` - dormant unless a
caller actually registers a config, so `run_initiator` (no `--webhook`)
is unaffected.

**A second live-caught bug, this time in webhook mode specifically:** the
very first attempt sent a duplicate message into an already-completed
task. Cause: the task-creation event that already came back synchronously
as the `return_immediately` ack *also* gets pushed to the webhook again
(state `SUBMITTED`), ahead of the real answer. The initiator's callback
loop was only filtering out `WORKING`, so it treated that duplicate
`SUBMITTED` push as if it were the final answer, decided nothing had been
resolved, and sent another message - which the server correctly rejected
("Task is already completed"). Fixed by filtering out both `SUBMITTED`
and `WORKING` as "not yet an answer," not just `WORKING`. Caught by
actually running two live processes against each other, not by reasoning
about the SDK's event model in the abstract - same discipline as the
`history_from_context` finding above, applied a second time in the same
phase.

**New dependencies:** `a2a-sdk` pulls in a meaningfully heavier chain than
anything else in this project - `protobuf`, `google-api-core`,
`google-auth`, `cryptography` - plus `sse-starlette`, an undeclared
transitive dependency its JSON-RPC routes need directly (import fails
without it; added to `requirements.txt` explicitly rather than left
implicit). This is a real, deliberate departure from D7/"boring stdlib
code, new dependencies need a reason" - the reason here is simply that
this is what adopting the *real* protocol costs; it was flagged, not
absorbed silently.

**Would change our mind:** if a genuinely long-running policy shows up
(the thing webhooks are actually for), worth re-testing this exact path
against it rather than trusting today's fast-policy-only verification
generalizes; today's negotiations all resolve in well under a second, so
the webhook path has only been proven mechanically correct, not proven
under real long-wait conditions.

---

## D16 — A networked robot process only ever loads its own half of a scenario

**Decided:** `scenarios.py` gets one new accessor, `for_side(scenario_id,
side) -> (situation, urgency)`. `agent.py`'s `build_robots()` calls it
instead of taking a `Scenario` object at all - `main()` no longer does
`BY_ID[args.scenario]` and never holds a reference to the full paired
scenario. Only `run.py`/`eval.py`/`simulate.py`/`world_eval.py` still load
the full `Scenario` (both halves + `should_go_first`), because they
legitimately need it - they're the omniscient single-process harness that
scores correctness, not a robot.

**Rejected:** leaving `agent.py` to keep loading the full `Scenario` and
trusting `build_robots()` to only read its own half - which is exactly
what it already did, correctly, before this decision. The gap wasn't a
bug; it was the same kind of discipline-not-structure gap the `other =
Robot(name, "", 0)` placeholder (Phase 4 v1's "Correction 1") already
closed for the *other robot's* `Robot` object. This closes the matching
gap one level up, for where `me`'s own data comes from.

**Why:** raised directly by the user while reviewing Phase 5 - since the
robots are already separate OS processes (Phase 4), a scenario should
define what *one* robot sees, not a pair, the same way `PLAN.md` §4.1
already frames a robot's situation as private. Before this, nothing
*read* the other robot's secret in `agent.py`, but the full pair sat in
that process's own working memory (`scenario.a_situation` and
`scenario.b_situation` both reachable from one local variable) for the
whole run - a future change (a stray debug print, a refactor that grabs
the wrong field) could leak it with no test catching it. Same reasoning
as D12's "the reactive layer's guarantee needs to hold even if the
executive layer is wrong, later, for reasons nobody anticipated," applied
to secrecy instead of collision safety.

**An honest limit on how far this goes:** `scenarios.py` itself is one
shared, statically-defined module (`SCENARIOS` embeds both halves of
every scenario in source) that both `--side a` and `--side b` processes
import - so the *data* for both halves technically still loads into both
processes' memory the moment `agent.py` does `from scenarios import
...`, same as `negotiation.py`'s `LLMPolicy` class being importable by
both sides. What changed is that neither process's own *working state*
(`me`, `other`, or any local variable in `agent.py`/`agent_executor.py`)
ever holds a reference to the other side's values anymore - confirmed by
grepping both files for `scenario.` and `BY_ID[` after the change: zero
hits outside `scenarios.py` itself. This is "shared code, not shared
state," the same distinction already established for why `negotiation.py`
being one file both processes import is fine. Going further - genuinely
splitting the source data per side, e.g. two files or a per-side lookup
service - would be the next step if this project ever needed to defend
against something reading the *module*, not just something reading
`agent.py`'s own variables; not needed for a learning project with no
adversarial attacker model.

**Would change our mind:** if Phase 8+ (three or more robots, real
services) ever puts scenario data behind something other robots could
plausibly query (a shared config service, a mounted file) - at that point
"shared code" stops being an accurate description and the harder
per-side-file split from the paragraph above would be worth doing for
real.

---

## D17 — Phase 6: the world as a real MCP server, and who executes vs. who validates

**Decided:** `world_server.py` is a new MCP server exposing three tools -
`get_map()`, `get_observation(side)`, `propose_action(side, action)`. It
holds the only copy of ground-truth position (`WorldState`, reused
directly from `world.py`, unchanged). `agent.py` gets a `--world-url`
flag; when given one, it also becomes an MCP client and runs a movement
loop alongside its existing A2A negotiation role. Negotiation stays
exactly as Phase 5 built it (`--side a` is still the fixed initiator, per
the plan's explicit staging) - the only thing that changed this phase is
*when* it fires: gated on a robot's own `get_observation()` saying it's
at the boundary and can sense the other, matching Phase 3's original
physical logic (D12), now driven by a real MCP call instead of an
in-process simulator check. Arriving at the boundary alone still claims
priority for free, zero negotiation, exactly as before.

`world.py`/`simulate.py` are **completely untouched** - not "split," the
original plan's wording. `simulate.py` still needs the full
`executive_decide`/`_resolve_priority` chain for its own single-process
path, so nothing could be deleted from `world.py` without breaking it.
`world_server.py` instead directly reuses `WorldState`/`RobotState`/
`reactive_filter`/`apply` - the genuinely `Robot`-agnostic, safety-critical
pieces - rather than moving or reimplementing anything.

**What actually shipped is not what was first designed, in two ways -
both caught only by building and testing, not by planning ahead:**

**1. `propose_action`, not `propose_move`, and no background clock at
all.** The original design (matching the approved plan) had the world
run its own independent tick loop, applying whatever each robot had most
recently proposed on a fixed schedule, with `propose_move()` reporting
`last_applied` - the outcome of the *previous* tick, one full round
behind whatever was just proposed. Working through this live surfaced a
real problem: `last_applied` is only meaningful if a robot's own polling
rate happens to line up with the server's independent tick rate, which
nothing guarantees - poll faster and you see stale repeats, poll slower
and you silently miss intermediate ticks. Worse, it also drifted from
what a real robot actually is: a real robot commands its own motors and
knows immediately whether it moved, it doesn't get told after the fact
by an external clock.

The resolution, reached collaboratively rather than designed upfront: the
robot still decides and commits its own action, but the world's
authority narrows to exactly the one thing no single robot can safely
decide alone - whether it's safe to enter the shared corridor zone right
now. `propose_action(side, action)` resolves **synchronously**, checked
against `reactive_filter` and the world's *current* state (the other
robot's action held fixed as `"wait"`, since only one robot transitions
per call - there's no such thing as "simultaneous" once actions commit
one at a time, so the both-enter-at-once case Phase 3 worried about
structurally can't arise here), and returns the real, authoritative
outcome immediately: `accepted` and `actual_position`. No independent
clock, no staleness, no lag - and less code than the version with the
background tick loop, not more.

**Why position still lives in the world, not the robot:** raised
directly in the same conversation - a position number only means
anything relative to a shared coordinate system (the corridor, the
boundaries, the zone) that the *world* defines, not something a robot can
have an opinion about in isolation. So the robot still asks
`get_observation()` for the current truth and still treats its own
belief as provisional until confirmed - it just now also *drives* that
truth via its own committed actions, instead of passively receiving
whatever an independent clock decided.

**The real-world analogy that resolved the disagreement:** the world
retaining veto power over zone entry isn't "the world doing the robot's
job" - it's the same thing rail interlocking and air traffic control do
for a genuinely shared, contested resource. A train drives itself; it
still doesn't enter a single-track section without permission from a
signaling authority that tracks occupancy of *that specific resource*.
Nobody considers that the signal box driving the train.

**2. `NegotiationExecutor` needed a callback it didn't have.** Under the
fixed-initiator convention, `--side b` never calls out - its own
negotiated outcome only ever becomes known *inside*
`NegotiationExecutor.execute()`, reacting to whatever `--side a` sends
whenever it gets around to it. `--side b`'s own movement loop has no way
to `await` that. Added `on_resolved`, an optional callback invoked at
both points `execute()` learns `check_agreement()` is decided - the one
way the movement loop learns priority was ever set at all.

**Consequence:** `run_responder` (the plain, `--side b`-only path) is
unchanged; a new `run_responder_async` (built on the same
`uvicorn.Server(...).serve()`-as-a-background-task pattern
`run_initiator_webhook` already established in Phase 5) runs the A2A
server concurrently with the movement loop when `--world-url` is given.
`run_initiator`/`run_initiator_webhook` now return the negotiated outcome
instead of only printing it, so `run_initiator_with_world` can use it
directly.

**Verified end to end:** `world_server.py` + two `--world-url` `agent.py`
processes, deterministic policies, `dying_battery_vs_fragile_cargo` -
final positions (A=8, B=1) and outcome (agreed on Robot A) matched
`simulate.py`'s in-process baseline exactly. Collision safety re-verified
live across the real process boundary under the new synchronous design,
same as the first version.

**Explicitly deferred, unchanged from the plan:** boundary-triggered
*dynamic* initiation (either robot can become the initiator, with jitter
+ a name tiebreak for the race case) and the visualizer rebuild - both
still their own follow-on plans.

**Would change our mind:** if a future phase needs multiple robots
transitioning in true lockstep (a synchronized global tick actually
mattering, not just each robot's own local decision) - that would justify
reintroducing something like the independent clock this decision removed,
but nothing in this project currently needs that.

---

## D18 — Boundary-triggered dynamic initiation: every robot can dial, races resolved by jitter + tiebreak + cancellation

**Decided:** `agent.py`'s `--side a`/`--side b` split no longer means
"client-only" vs "server-only." `run_initiator_with_world`/
`run_responder_with_world` collapse into one `run_robot()` - every robot
always runs its own A2A server (`run_responder_async`) *and* its own
movement loop, and is capable of dialing the peer the instant its own
`get_observation()` says it's at the boundary and can sense the other.
`--side` now only picks which half of the scenario to load (D16) and
which robot name to use.

**Mechanism:** the dial is jittered - a one-time random deadline
(`now + random.uniform(0, 1.0)`) computed the moment the trigger
condition first becomes true, checked against `time.monotonic()` each
loop iteration rather than slept inline, so the loop keeps
polling/proposing the whole time. When the deadline passes, the dial
runs as a cancellable `asyncio.Task`, not an inline `await`.
`NegotiationExecutor` gained `on_task_started` (fires once, with the
peer's name, the instant a brand-new incoming task arrives - earlier
than the existing `on_resolved`, which only fires once a negotiation
*concludes*). The race case (both robots dial before either sees the
other's incoming call) is resolved with full cancellation: whichever
robot loses a fixed, deterministic tiebreak (lexicographically smaller
name wins - "Robot A" always wins a tie) cancels its own outbound
`asyncio.Task` the instant it learns of the incoming one - before
wasting a policy call, not after. Verified live, repeatedly: the same
scenario correctly resolves to the same ground-truth-correct outcome
regardless of which side actually ends up dialing, and a genuinely
forced race shows the exact `"race - {peer} called in while I was
dialing - deferring"` cancellation line, with the negotiation still
concluding correctly through the surviving task.

**Two real bugs, both found only by running this live - the plan
anticipated the race but not either of these:**

1. **A dial that fails (e.g. the peer's server socket isn't bound yet)
   crashed the entire robot process.** `dial_task.result()` re-raises
   whatever exception the task ended with; nothing caught it. Fixed by
   checking `dial_task.exception()` first - a failed dial now logs a
   warning and resets the jitter deadline so a fresh attempt gets
   scheduled on a later loop iteration, instead of taking the whole
   process down. A real robot doesn't die because it called a peer a
   moment too early.

2. **A robot could start a *second*, redundant dial while it was
   already the responder on an active incoming negotiation.**
   `on_task_started` only cancels an outbound dial that's *already in
   flight* - it does nothing to stop a *new* one from starting later,
   because `priority_holder` (what the movement loop checks before
   deciding to dial) only gets set once a negotiation *concludes*
   (`on_resolved`), not when one *starts*. With a slow LLM call widening
   the window, the responding side's own movement loop reached its
   jitter deadline mid-negotiation and dialed the peer it was already
   talking to - two simultaneous negotiations between the same two
   robots. Fixed with a third piece of state, `incoming_active`, set
   `True` in `on_task_started` and cleared in `on_resolved` - the
   movement loop now checks it before ever starting a new dial, not just
   before letting an in-flight one continue. This is the more important
   of the two fixes: the in-flight-cancellation mechanism the plan
   designed for handles simultaneous starts, but says nothing about
   preventing a start *during* an already-active negotiation - a gap the
   plan's design section didn't anticipate because it was reasoning about
   the moment two dials begin, not the whole duration one stays open.

**A pre-existing limitation, unchanged:** `LLMPolicy.respond()` is still
a synchronous call inside an `async def` (D15) - a slow LLM call blocks
the entire process, including its own race-detection and world-polling,
for the duration of that call. Not fixed here, as planned.

**Would change our mind:** if `JITTER_SECONDS=1.0` turns out to still
produce races often enough in practice to be annoying (rather than the
rare, correctly-handled case observed in testing) - worth widening it,
or reconsidering whether the tiebreak should be primary rather than a
fallback, if evidence suggests otherwise.

---

## D19 — The network visualizer reconstructs priority after the fact; the template didn't change at all

**Decided:** `visualize_network.py` is a new, small post-hoc tool -
`visualize_template.html` is reused completely unchanged, same split
Phase 3 already used (D13): only the data-builder is new. It fetches a
completed episode's movement log from `world_server.py` over MCP
(`get_map` + the new `get_log` tool) and reads
`experiments/results/negotiation_trace.json` if a negotiation happened -
written by whichever robot's `agent.py` process ends up holding the full
transcript when a negotiation concludes (`write_negotiation_trace()`,
called from both the dial-success path and `on_resolved`, since either
side can now be the one who learns the outcome first - D18 made this
genuinely either-or, not fixed to one side).

**Why priority has to be reconstructed, not read:** `world_server.py`'s
log has no concept of priority at all, by design (D17) - the world only
ever sees move/wait proposals, never who negotiated what or why. So
`visualize_network.py` infers it after the fact from the simplest signal
the log actually contains: a robot "has priority" from the first log
entry where it successfully moves off its own boundary position -
whether that was negotiated or claimed for free, that's the moment "who
goes first" became real, and it's derivable from `a_position`/`b_position`
alone with no reference to *why* the world allowed it. That same index
also anchors where the negotiation dialogue gets attached in the replay,
if one happened - not a claim of matching some real "tick" the way Phase
3's single-process design had one, since the world channel and the
negotiation channel are now genuinely asynchronous and don't share a
clock.

**Rejected:** trying to correlate world-log timing with A2A message
timing precisely (e.g. threading a shared timestamp or tick counter
through both channels). Not worth the complexity for a debugging
visualization - D4b already establishes that the world and negotiation
channels are deliberately separate and shouldn't be coupled; adding
timing correlation between them would be exactly the kind of coupling
that principle warns against, just for cosmetic replay accuracy.

**Verified:** a real live episode's generated `episode_data` was
inspected directly (not just assumed correct from the code) - the log
correctly shows both robots holding at their boundaries while the A2A
negotiation happens off in its own channel (multiple consecutive
`wait`/`wait` rows with `priority: null`), priority flips to the winner
at exactly the row where that robot's position first moves past its
boundary, `negotiation.tick` lands on that same row, and the messages
match the real negotiation transcript. The "no negotiation, arrived
alone" code path was verified separately with hand-built data, since
(same as Phase 3) the project's fixed grid geometry and `SENSOR_RANGE`
mean every real scenario run always senses a conflict by the boundary -
that path was never naturally reachable through a live run in Phase 3
either.

**Would change our mind:** if a future phase makes the grid/sensor
geometry configurable enough that "arrived alone" becomes a real,
reachable outcome - worth adding a live end-to-end check for it then,
not just the hand-built one.

---

## D20 — "Tick" was a lie in the network visualizer; real timestamps replace it

**Decided:** `world_server.py`'s log entries now carry a real
`time.time()` timestamp per `propose_action` call, returned by
`get_log()`. `visualize_network.py` drops the word "tick" entirely -
`entry.tick` becomes `entry.step` (a plain sequence index, no claim of
synchronization) and each row carries `elapsed_ms`, the real time since
the previous entry. `visualize_template.html` - genuinely shared between
Phase 3's `visualize.py` and this - detects which shape it was given
(`log[0].step !== undefined`) and behaves accordingly: Phase 3 data still
gets "tick" labels and the fixed `TICK_DELAY_MS` pacing unchanged; step
data gets "step" labels and per-row delay from real `elapsed_ms`, clamped
to [300ms, 3000ms] so a near-instant real gap stays visible and a long
one doesn't force a multi-second wait in autoplay.

**Why:** raised directly by the user reviewing the visualizer - Phase
3's "tick" was real (one process, one `step()` call, both robots decided
and applied together). Once the world stopped having a shared clock
(D17), each log entry became one robot's independent, serialized commit,
arriving whenever its own async loop happened to call in. Labeling these
"tick 5," "tick 6" as if they were synchronized steps was quietly
reintroducing a false synchronization the system doesn't have - it hid
two real distortions: genuinely near-simultaneous events (two robots
proposing within milliseconds of each other) got serialized into
strictly-ordered ticks as if one meaningfully preceded the other, and a
genuine multi-second gap (e.g., a slow LLM negotiation) was invisible,
since consecutive step numbers always increment by 1 regardless of how
much real time passed.

**Rejected:** leaving the label as "tick" and treating it as a cosmetic
issue. It wasn't cosmetic - the whole point of the visualizer (D13) is to
build an accurate mental model of what actually happened, and a fake
synchronized clock actively works against that for the one thing this
phase's architecture most needs explaining: that the world and
negotiation channels are genuinely asynchronous now.

**Scope boundary, deliberate:** only movement/step pacing uses real
elapsed time. Negotiation dialogue reveal still uses the fixed
`MESSAGE_DELAY_MS`/`DECISION_DELAY_MS` - those are about giving a human
reader time to read each line, not about reproducing real LLM latency
(nobody wants to actually sit through a real multi-second API call while
watching a replay), and the negotiation trace file doesn't carry
per-message timestamps to do that with anyway.

**Verified:** live episode inspection shows realistic elapsed_ms values
(mostly ~0-215ms, matching the movement loop's ~0.2s poll interval) at
every step including around the negotiation step, and confirmed Phase
3's `visualize.py` output is byte-for-byte the same shape as before (no
`step`/`elapsed_ms` fields at all) - the template's shape-detection
correctly falls back to the old tick-based behavior for it, unchanged.

**Would change our mind:** if the negotiation trace ever gains real
per-message timestamps (e.g., to show LLM thinking time in the replay
the same way the live streaming heartbeat does) - worth extending the
dialogue reveal to use real elapsed time too, at that point.

---

## D21 — The grid is now deliberately asymmetric (MIN_POSITION..MAX_POSITION, 1..24)

**Decided:** `world.py` gained real `MIN_POSITION`/`MAX_POSITION`
constants (1 and 24, replacing three separate hardcoded "1"/"8" literals
in `world_server.py`, `visualize.py`, and `visualize_network.py`).
`A_START`/`A_BOUNDARY` are unchanged (A is still 1 step from its
boundary); `B_START`/`B_TARGET` moved out to the new far end, so B is now
~22 steps from its boundary instead of 2. `SENSOR_RANGE` was recomputed
(6 → 22) to preserve the exact invariant D12 established and
`tests/test_world.py` already encoded: the max possible gap from A's
boundary is exactly `SENSOR_RANGE`, so a real standoff is always sensed
by the time A arrives, never missed.

**Why:** requested directly, to finally exercise something this project
could never observe live before - Phase 3's symmetric grid guaranteed
both robots reached their boundaries closely enough in time that a real
async negotiation with meaningfully different arrival timing never
naturally occurred. Widening (rather than narrowing) `SENSOR_RANGE` was
a deliberate choice, not the only option - keeping it narrow instead
would have let A claim priority for free the instant it arrives (B not
yet sensed), the "arrived alone" path D19 already flagged as never
naturally reachable. Both are legitimate experiments; this one preserves
today's "always negotiate on a real conflict" behavior and is what got
built.

**Verified:** `world_eval.py`'s completion rate (45/50), negotiation rate
(45/50), and correctness (13/27) are byte-for-byte identical to the
pre-change baseline - only `avg_ticks_used` grew (10.2 → 31.4), confirming
the grid-length change is fully orthogonal to negotiation correctness, as
intended. `simulate.py`/`visualize.py`'s default `--max-ticks` (30 → 90)
and `world.py`'s own `run_episode` default needed bumping too, or a
default run no longer completes within budget - a real, easy-to-miss
consequence of a longer grid, caught by actually running it rather than
assumed.

**Would change our mind:** if a future experiment wants the *narrow*
sensor range instead (to finally exercise the "arrived alone, zero
negotiation" path) - that's a one-line `SENSOR_RANGE` change away, not a
reason to revert this decision, just a different experiment to run on
top of it.

---

## D22 — Real timestamps replace a broken movement-based heuristic for "when did the negotiation happen"

**Decided:** `agent.py` now records two real `time.time()` timestamps per
negotiation - `comms_established_at` (when the task actually opened -
either when a robot starts dialing, captured right before
`asyncio.create_task`, or when `on_task_started` fires on the responder
side) and `resolved_at` (when `write_negotiation_trace` runs) - both
written into `negotiation_trace.json` alongside the messages.
`visualize_network.py` correlates both against `world_server.py`'s own
per-entry timestamps (D20) via `find_step_at_or_after()` to find the
right world-log step for each, and gets the negotiation's *winner*
directly from `negotiation.check_agreement()` - the same function that
decided it live - rather than inferring either fact from movement.

**Rejected, having actually shipped it first:** D19's original heuristic
- "priority becomes known at the first log entry where the winner moves
off its own boundary." Caught directly by the user reviewing a real
replay on the new asymmetric grid (D21): Robot B won a `routine_vs_medical`
negotiation that a live console log showed resolving within the first
several seconds (Robot A dialing almost immediately upon reaching its
own boundary), but the visualizer displayed the negotiation dialogue at
step 42 of 72 - because B, the winner, doesn't reach *its own* boundary
until it finishes an unrelated 18-step walk from `B_START`. The heuristic
conflated "when B is finally allowed to cross" with "when the negotiation
happened" - true by coincidence on the old symmetric grid, false as soon
as the winner could be arbitrarily far from its own boundary when it won.

**Why the fix is real timestamps, not a smarter heuristic:** there's no
way to correctly infer negotiation timing from movement alone once the
winner and "who's about to cross a boundary" can be different robots
entirely. The two channels (world, negotiation) already don't share a
clock (D19's own caveat) - the actual fix is to stop pretending movement
can substitute for that and instead give each channel its own real clock
reading, then correlate the readings directly.

**New in the replay, from the same fix:** an "establishing comms" marker
(reusing the existing priority-banner element, no new DOM/CSS) now shows
at `comms_step`, separately from the dialogue-reveal at the resolution
step - visible proof the negotiation is bracketed by two distinct real
moments, not one guessed one. Skipped when both land on the same step
(a fast negotiation with nothing to show in between).

**Verified:** the exact scenario that exposed the bug now shows
`comms_step: 10` (matching the live console log's near-immediate dial)
and a resolution step 33 steps later (matching a real multi-turn LLM
exchange's actual latency) - both numbers now tell a story consistent
with what the live run actually printed, instead of contradicting it.

**Would change our mind:** if clock skew ever became a real concern (a
truly distributed deployment across machines, not one laptop) -
`time.time()` correlation between processes would need NTP-level care it
doesn't have today. Not a concern for this project's current scope.

---

## D23 — Jitter only when a race is actually plausible

**Decided:** `world_server.py`'s `get_observation()` gained
`other_distance_to_boundary` - how far the other robot currently is from
its own boundary, computable only when it's sensed at all. `agent.py`'s
dial-scheduling logic (`should_skip_jitter()`) now skips the jitter delay
entirely and dials immediately whenever that distance exceeds
`RACE_PLAUSIBLE_DISTANCE` (5 steps) - jitter still applies exactly as
before (D18) whenever the other robot is close enough that it might
plausibly also be about to reach its own trigger condition.

**Why:** raised directly, from watching a real replay - D21's asymmetric
grid meant `SENSOR_RANGE` now senses the other robot from up to 22 steps
away, but the jitter delay (D18) was still applying unconditionally
every time, even though there was no realistic chance the other robot
was anywhere near also deciding to dial. Jitter's entire purpose is
reducing the odds of a genuine simultaneous-dial race; applying it when
that race can't actually happen just adds a pointless ~0-1s delay for no
safety benefit.

**Is this new information, or a violation of "agents must have different
information"?** No - the shared map (both boundaries) is already public
via `get_map()`, and a robot that senses the other's real position could
derive this itself by arithmetic (own position + gap + which side the
other robot is on). `other_distance_to_boundary` just does that
arithmetic once, robustly, in `world_server.py` (the one place that
actually holds both real positions) rather than have every caller
re-derive it. Nothing private (situation, urgency, policy) crosses this
line - it's the same category of already-derived-from-sensing fact
`gap_if_sensed`/`other_cleared_zone` already were.

**Explicitly not safety-critical:** this is a heuristic that only affects
*whether a robot waits before dialing*, never whether a race is resolved
correctly once one happens. If `RACE_PLAUSIBLE_DISTANCE` is ever wrong in
either direction - too small (skips jitter when a race was actually
possible) or too large (jitters when it didn't need to) - D18's static
tiebreak and `incoming_active` guard still fully handle the actual
correctness guarantee regardless. This can be tuned freely without
touching safety.

**Verified live:** the same scenario that motivated D21/D22 now dials
immediately (`comms_step: 2`, down from `10`) with the console explicitly
printing why ("sensing Robot B but it's far from its own boundary -
dialing immediately, no jitter"), while the underlying jitter/race
mechanism itself is unchanged for the case it actually protects.

**Would change our mind:** `RACE_PLAUSIBLE_DISTANCE = 5` is a rough
guess from observed step timing (~0.2-0.25s/step, jitter up to 1s), not
measured against real race frequency at different thresholds - worth
revisiting with real data if races start feeling too frequent or too
rare relative to what the grid's geometry would suggest.

---

## D24 — A networked LLM negotiated completely blind to position, since Phase 5

**Decided:** `observation.py` gained `compose_observation_from_dict()` -
the same Job 4 composition `compose_observation()` always did (D4b),
built from `world_server.py`'s `get_observation()` dict instead of raw
positions, and extended with `other_distance_to_boundary` (D23) so a
robot can tell a real conflict apart from something merely sensed from
far off. `agent.py`'s `run_robot()` now keeps `me.situation` live -
recomposed from the current MCP observation every loop iteration, using
the *original* private text captured once before the loop (so it never
compounds) - so whichever role ends up calling the policy (this robot's
own dial, or `NegotiationExecutor` reacting to an incoming task, both
read the same shared `Robot` object) sees real position and sensing
facts, not just the static hand-authored situation.

**What was actually true before this, stated plainly:** `compose_observation()`
was never called anywhere in `agent.py`/`agent_executor.py` - grepping
both files for it returned nothing. Every networked negotiation since
Phase 5, including every LLM-vs-LLM run shown earlier in this project,
happened with the model working from *only* the static private text
("empty pallet return, no deadline") - zero awareness of its own
position, whether it was at a boundary, whether it sensed the other
robot, or how far away that robot actually was. Phase 3's `world.py`
composed this correctly every time (`_negotiate_priority()`); the step
never got carried over when negotiation moved off the in-process world.

**Why this stayed hidden so long:** the deterministic policies
(`Stubborn`, `AlwaysYield`, etc.) never read `situation` at all, so
nothing about their behavior would ever expose the gap - only an LLM
policy, actually reasoning from the text it's given, could reveal it,
and only by a human reading its stated reasoning and noticing something
was missing. Caught directly: the user watched Robot A immediately offer
to yield in a case where B was still 20+ steps away, asked why A
"missed" that B was far off, and the honest answer turned out to be that
A was never told at all - not a reasoning failure, an information
failure.

**Verified live:** the same `routine_vs_medical` case that exposed the
gap now has Robot A open with *"I'm at corridor entrance, 1 cell away.
You're 17 cells from your boundary. I go first, minimal wait for you"* -
a real, grounded, spatially-aware proposal that didn't exist before this
fix. B still wins (correctly, matching ground truth) on the strength of
its actual medical urgency, but now the negotiation is a real exchange
of claims instead of one side reflexively yielding with no facts to
reason from.

**A related quality note, not itself fixed here:** the LLM's stated
numbers in its replies don't always exactly match the injected facts
(one run had B claim "you're 7" when the real gap was different) - the
model appears to paraphrase/estimate rather than quote precisely. Worth
watching if this project ever needs to score claims against ground
truth automatically (D10's `correct` scoring doesn't currently do this),
but the negotiation *decisions* observed so far have stayed correct
despite the imprecise phrasing.

**Would change our mind:** if a future policy needs the *exact* Job 4
text shape Phase 3 uses (not the dict-derived version) - unlikely, since
`world_server.py` doesn't hold raw positions in a form `compose_observation()`
could consume directly without the same fragile arithmetic this design
deliberately avoided (see D23's identical reasoning for
`other_distance_to_boundary` itself).

## D25 — The network visualizer collapses idle polling rows

**Decided:** `visualize_network.py` gained `collapse_idle_runs()`,
applied to the raw world log before it becomes the replay's `log` list.
Consecutive rows where neither robot moved and priority didn't change get
merged into one displayed row, summing their `elapsed_ms` and carrying an
`idle_polls_collapsed` count so the merge is visible, not hidden -
`visualize_template.html` appends "(N idle polls collapsed)" to that
row's label. `negotiation.step`/`comms_step` (computed against the raw,
uncollapsed rows via `find_step_at_or_after`, D22) get remapped through
the same collapse to still point at the right row.

**Why:** every `propose_action` call logs a row, including no-op "wait"
polls, and each robot polls on its own ~0.2s loop independent of the
other. `LLMPolicy.respond()` is a synchronous call inside an `async def`
(a pre-existing limitation, D15) - it blocks that robot's entire process
for the duration of an API call. So while one robot is mid-negotiation
(several sequential LLM turns, several real seconds), the other keeps
polling and logging identical rows the whole time. Caught live: the user
watched a replay where the negotiation dialogue appeared at step 49/50
of a 79-row log, asked if something was "messed up." It wasn't a
correctness bug - D22's timestamp correlation was landing on the right
row - but 29 duplicate "wait/wait" frames between "establishing comms"
and the actual dialogue made the replay look broken.

**Verified live:** re-ran the same scenario after the fix - a ~6-second,
3-turn negotiation that previously spanned 29 empty frames now shows as
one frame ("step 20 - 29 idle polls collapsed, ~6.0s") immediately
followed by the negotiation dialogue. Confirmed this wasn't one unlucky
run: a second re-run had a similar-magnitude real negotiation delay,
consistent with the synchronous-LLM-call explanation, not a fluke.

**Would change our mind:** if `LLMPolicy` ever becomes genuinely async
(the D15 limitation gets fixed) - the idle stretches would mostly
disappear on their own, and this collapsing would just rarely trigger,
not need reverting.

## D26 — Sensor range is physically motivated, not tuned to grid extremes; the asymmetric grid experiment is over

**Decided:** `SENSOR_RANGE` is now `len(CORRIDOR_ZONE) + 3` (currently
6, unchanged in value from before D21, but now a formula instead of a
constant tuned to the grid's endpoints) instead of D21's `22`, which was
specifically computed to preserve "always sensed by the boundary" on the
widened, asymmetric grid. `MIN_POSITION`/`MAX_POSITION` are back to `1`/`8`
(from `1`/`24`), and `--max-ticks` defaults are back to `30` (from `90`)
in `simulate.py`/`visualize.py`/`world.py`'s `run_episode` - D21's
asymmetric-grid experiment is over.

**Why:** requested directly - a sensor range computed to guarantee
detection from clear across an arbitrarily long map was never meant to
be permanent; it was D21's way of forcing an asymmetric-arrival case to
finally happen live. In reality, a sensor detects an approaching robot
once it's close enough to the shared zone to matter - corridor length
plus a small margin - not from anywhere on the grid. That naturally means
a robot only negotiates when there's a real collision risk; one that
reaches its own boundary with nothing nearby just proceeds, free, same
as Phase 3 always allowed for. Decoupling the formula from grid extremes
also means a future grid-size change won't need `SENSOR_RANGE`
recomputed by hand the way D21 did - it was the same underlying coupling
that made D21's own math (`|A_BOUNDARY - B_START|`) necessary in the
first place.

**Verified:** `world_eval.py`'s completion rate (45/50), negotiation rate
(45/50), correctness (13/27), and `avg_ticks_used` (10.2) are all
byte-for-byte identical to the original pre-D21 baseline cited in D21's
own decision text - confirming the revert is exact, not approximate.
Live network run on the narrow sensor range against the *still-widened*
grid (before reverting grid size) showed Robot A claiming priority alone
and finishing before B ever came into range - correct behavior, no real
collision risk existed in that case. A second live run, after reverting
to the size-8 grid, showed `Robot A: at boundary, sensing Robot B -
dialing` - a real negotiation, naturally, on the original grid size,
without needing D21's artificial widening to produce it.

**Would change our mind:** if a future experiment wants to force a
genuinely wide sensor range again on purpose (D21's original goal, now
achievable without touching grid size at all) - a one-line
`SENSOR_RANGE` override, not a reason to revert this decision.

## D27 — The race tiebreak *winner* must also cancel its own dial once it learns the outcome another way

**Decided:** `agent.py`'s `on_resolved` callback now cancels this robot's
own in-flight outbound `dial_holder["task"]`, if any, the moment a
negotiation concludes via an *incoming* task. Previously only
`on_task_started`'s tiebreak logic ever cancelled an outbound dial, and
only for the *losing* side (D18's `is_my_turn_to_initiate` check). The
winning side had no reason built in to ever cancel its own dial, because
D18 was verified against the widened, asymmetric grid (D21), where a
genuine two-sided race was structurally rare - A almost always finished
long before B ever got close enough to also be dialing.

**Why:** surfaced immediately on reverting to the symmetric grid (D26) -
both robots now regularly reach their boundaries close enough in real
time to both dial at once. When the loser's own outbound call reaches
the winner's server *before* the loser manages to cancel it, the winner
receives a real incoming task and resolves it as a responder, while its
own outbound dial (never cancelled - it's the tiebreak winner) keeps
running as a second, fully independent negotiation for the very same
standoff. Live evidence: `routine_vs_medical`, LLM-vs-LLM, size-8 grid -
Robot A's responder side accepted an incoming proposal from B and wrote
the trace, then its still-running outbound dial made an entirely
separate proposal to B moments later, which timed out against a peer
that had already moved on, throwing an unhandled `ClientDisconnect` in
the A2A server routing layer. Not a data-correctness bug (the first,
real negotiation's outcome was already correct and already committed) -
a crash risk and duplicate LLM spend on top of it.

**Why `on_resolved`, not `on_task_started`:** the tiebreak in
`on_task_started` still has a real job - deciding which side's dial
*proceeds* when both are racing to start. But by the time `on_resolved`
fires, the standoff is over, full stop, regardless of which side's
attempt actually produced the answer. Cancelling there doesn't touch the
tiebreak logic at all; it just recognizes that once the real-world
conflict is resolved, any other still-running attempt to resolve the
same conflict is now provably redundant.

**Verified live:** 3 runs with deterministic (`stubborn`) policies (fast
enough that a full two-sided race rarely stays open long) - no crashes,
matching `simulate.py`'s baseline outcomes. 3 runs with `llm`-vs-`llm`
(slow enough that the race window regularly stays open) - all 3
completed cleanly; 2 of them printed the new cancellation line and
showed the fix actually engaging, with the winning side's redundant dial
cleanly cancelled instead of erroring out.

**Would change our mind:** if a future design lets two robots
legitimately hold two *independent* negotiations at once (not the case
here - there are only ever two robots and one shared corridor, so any
resolved standoff is necessarily the same standoff any other in-flight
attempt was also trying to resolve).

## D28 — The network visualizer no longer shows "wait" for a robot that simply hasn't started yet

**Decided:** `visualize_network.py`'s `build_episode_data()` now tracks,
per side, whether that side has logged its own first `propose_action`
row yet. Before it does, that side's `a_action`/`b_action` is `null`, not
`"wait"` - `visualize_template.html` renders it as "not started" instead
of a decision. `collapse_idle_runs` (D25) is unaffected - `null` doesn't
match its `"wait"` check, so these rows are never folded into an idle
run, which is correct: there's nothing to collapse, they're not real
idle polls.

**Why:** the world log has one row per `propose_action` call, from
whichever side made it (D17/D20) - a robot that hasn't logged a call yet
has no row at all, not a "wait" row. `build_episode_data()` previously
filled in `"wait"` for whichever side *didn't* act on a given row,
regardless of whether that side had ever acted at all - indistinguishable
from a robot that reached its boundary and genuinely chose to hold.
Caught directly: the user read a replay and asked why Robot A looked
"stuck" doing nothing for the first couple of rows when nothing in
`decide_movement()` should block it (A wasn't even at its boundary yet).
The honest answer: A's process is started slightly after B's in every
documented run command (the world, then B, then A) - two separate OS
processes never start at literally the same instant - so B logs one or
two of its own early polls before A's process has made its very first
call. `decide_movement()` was never broken; A's actual first decision,
once made, was `"move"`, immediately.

**Verified:** a fresh run's log shows rows 0-1 as `a_action: null` (B
moving alone, matching B's own two early polls) and row 2 as A's real
first decision (`"move"`, correctly - A wasn't at its boundary). Full
suite (98 tests) and `simulate.py`/`world_eval.py` regression unaffected.

**Would change our mind:** if a future run command ever starts both
processes genuinely simultaneously (unlikely, and not something this
project has a reason to pursue) - the gap would just shrink to zero rows
most of the time, not need reverting.

## D29 — Per-message timestamps, an opt-in robot-status log, and a raw CSV timeline export

**Decided:** three additions, all in service of debugging a networked
episode without going through `visualize_network.py`'s rendered replay at
all:

1. `run_initiator`/`run_initiator_webhook` (`agent.py`) and
   `NegotiationExecutor` (`agent_executor.py`) now capture a real
   `time.time()` per message, index-parallel to `history`
   (`message_times`). `write_negotiation_trace()` embeds it as a
   `"timestamp"` field per message in the trace JSON. Always on - cheap
   (one more field on a file already written every run), no new I/O.
   On the responder side, a message only gets a timestamp the first time
   `NegotiationExecutor` observes it (`history` is rebuilt fresh from the
   a2a task every call, D15's own docstring - not accumulated locally),
   which approximates "when this process first learned about it" rather
   than the original author's exact send time - close enough on
   localhost, and the only signal this side actually has for messages
   the peer authored.
2. `agent.py` gained `--debug-log` (off by default). When set,
   `run_robot()` writes every real decision point it already prints to
   stdout - dialing, claiming priority alone, a race deferring, cancelling
   a redundant dial (D27), a failed dial retrying, reaching target - to
   `experiments/results/robot_status_<side>.jsonl`, structured
   (`{"timestamp", "side", "detail"}`), one line per event. Not every
   poll - only real state changes, the same moments already worth a
   `print()` - logging every 0.2s (now 1s, D30) poll would just recreate
   the flood-of-duplicates problem D25 already fixed in the visualizer.
3. New `export_timeline_csv.py` - a standalone, post-hoc tool (same shape
   as `visualize_network.py`) that merges `world_server.py`'s `get_log()`
   (already real-timestamped, D20), the negotiation trace's per-message
   timestamps, and the optional robot-status JSONL files into one
   `experiments/results/episode_timeline.csv`, sorted by real timestamp.
   Deliberately raw: one row per real event, nothing collapsed, nothing
   forward-filled, nothing guessed - every column not relevant to a given
   row's event type is just left blank. `build_timeline_rows()` (the
   actual merge) is pure and unit-tested; fetching from MCP and writing
   the file are manually verified, same split as everywhere else in this
   project.

**Why:** the user kept hitting cases where the *rendered replay* was the
thing confusing them (D22's misattributed timing, D25's flood of idle
frames, D28's "stuck" robot) - each one took a real investigation to
confirm it wasn't a decision-logic bug, just a display artifact. A raw,
unfiltered CSV sidesteps that whole category of confusion: nothing here
is interpreted or re-timed for watchability, so there's nothing left to
misread. The robot-status log specifically had to be opt-in - the user
was explicit that normal runs shouldn't pay for data nobody's currently
debugging with.

**Verified live:** a full `routine_vs_medical` LLM-vs-LLM run with
`--debug-log` on both sides produced a 39-row CSV; spot-checked with
Python's own `csv.DictReader` (not just a naive comma-split terminal
view, which mis-renders quoted commas inside message text) - message
rows, world rows, and status rows all present, correctly timestamped and
sorted, real quoting intact around commas in message text.

**Would change our mind:** if a future need calls for *live* streaming
export (tailing an in-progress episode) rather than post-hoc - this is
deliberately a batch tool, reading a finished episode's already-written
sources, same as `visualize_network.py`.

## D30 — Movement paced to ~1s/cell; jitter removed

**Decided:** `agent.py`'s hardcoded `asyncio.sleep(0.2)` (the bottom of
`run_robot()`'s loop) is now `asyncio.sleep(POLL_INTERVAL_SECONDS)` with
`POLL_INTERVAL_SECONDS = 1.0`. This is still the same single mechanism as
before - how often a robot checks in with the world - not a new, separate
"how long does moving one cell take" model; it governs movement, waiting,
and negotiation-trigger cadence uniformly, just recalibrated from ~5
checks/second to 1.

Separately, jitter (D18: a random 0-1s delay before dialing, meant to
reduce how often both robots dial at once) is removed entirely -
`JITTER_SECONDS`, `jitter_deadline`, and D23's `RACE_PLAUSIBLE_DISTANCE`/
`should_skip_jitter()` are all gone. The dial-trigger logic collapses to:
at boundary, sensing the other, not already dialing or responding ->
dial immediately. D18's tiebreak (`on_task_started`) and D27's redundant-
dial cancellation (`on_resolved`) are completely unchanged - they're the
actual correctness mechanism, and neither one ever depended on jitter
existing.

**Why the pacing change:** requested directly - at ~0.2s/cell, a robot's
own movement was fast enough to look instantaneous next to a multi-second
LLM negotiation, which made the negotiation delay look like the anomaly
rather than the normal case. Slowing movement to ~1s/cell puts both on a
more comparable, intuitive scale.

**Why remove jitter instead of just retuning it:** raised directly by the
user, and correct on inspection. Two things converged:
1. Once the poll interval widened to 1s, a jitter window narrower than
   that (e.g. 0.3s, floated first) can't actually spread anything - both
   sides still act on their very next poll regardless of the random draw,
   since the deadline is only ever checked once per poll. Below the poll
   interval, jitter stops doing its one job.
2. Jitter was never load-bearing for correctness in the first place -
   D18's tiebreak + D27's cancellation already guarantee a race resolves
   to exactly one outcome, cleanly, no matter the timing. Jitter only
   reduced how *often* a race happened, and even that benefit was
   smaller than it looked: `LLMPolicy.respond()` is a synchronous call
   inside an `async def` (D15's own flagged limitation) - `.cancel()`
   can't interrupt it mid-flight, so a race landing *during* that
   blocking call wastes the LLM call regardless of whether jitter delayed
   the dial that led to it. Given the real safety net was elsewhere
   already, removing jitter is a straightforward complexity reduction,
   not a risk trade-off.

**Verified live:** a fresh `routine_vs_medical` LLM-vs-LLM run (with
`--debug-log`, feeding directly into D29's CSV) shows consecutive
same-side world-log rows ~1.0-1.03s apart, a genuine two-sided race still
occurring and resolving cleanly (`Robot B: race - Robot A called in while
I was dialing - deferring`, then `Robot A: outcome already known from an
incoming call - cancelling my own outbound dial` - D27's mechanism firing
exactly as designed, with no jitter involved at all), and the correct
final outcome. Full suite: 99 passed (95 after removing the 3
`should_skip_jitter` tests, +4 new `export_timeline_csv.py` tests, D29).
`simulate.py`/`world_eval.py`/`run.py` regression unaffected (none of
them touch `agent.py`).

**Would change our mind:** if two-sided races become frequent enough in
practice to cost meaningfully more in wasted LLM spend than the
complexity jitter added was worth - the fix then would likely be
something more effective than jitter ever was (e.g. narrowing the actual
vulnerable window inside `LLMPolicy.respond()`, D15's real limitation)
rather than reintroducing a delay that couldn't reliably prevent the cost
it was meant to avoid.

## D31 — `--side a` launches first by convention now, not `--side b`

**Decided:** the documented three-terminal Phase 6/D18 run order in
`CLAUDE.md` now launches `--side a` before `--side b` (was the reverse,
unchanged since D17 first introduced the three-terminal form). No code
changed - `agent.py`'s dynamic initiation (D18) is fully symmetric
between the two sides; this is purely which line comes first in the
documented commands (and in every ad hoc debugging run this session).
Phase 5's fixed-role two-terminal form (no `--world-url`) is untouched -
there, `--side b` genuinely must start first, since it's the A2A server
`--side a` dials into; that's a real requirement, not a convention.

**Why:** `POLL_INTERVAL_SECONDS`'s move to the top of `run_robot()`'s
loop (D30) made every robot's pacing uniform and correct - which then
made it *more* visible, not less, that whichever robot's OS process
happens to launch first always gets a small, real (~0.2-0.3s) head
start purely from process/MCP-connection startup timing, every single
time, regardless of which side. Chasing this down consumed real
debugging time across two separate false leads (a boundary-distance
explanation, then a "these are B's positions" misread of a shared
column) before the user's own hypothesis - "is it just launch order?" -
turned out to be the actual answer, confirmed by swapping the order live
and watching the head start flip from B to A. Naming and fixing the
convention doesn't remove the head start (nothing can, short of true
simultaneous process launch, which the shell doesn't offer) - it just
stops it from being an unlabeled, silently-inconsistent variable in
every future debugging session.

**Verified live:** swapped launch order twice (B-first, then A-first),
confirmed the head start moved with whichever side launched first both
times, via `export_timeline_csv.py`'s output.

**Would change our mind:** if a future need calls for genuinely
simultaneous launch (e.g. a wrapper script that starts both processes
from the same parent at once) - worth building if this head start ever
matters for something more than readability, but not needed today.

## D32 — Phase 7: containers

**Decided:** one shared `Dockerfile` (Python 3.13, `requirements.txt`,
the whole repo copied in) - the same image runs all three fleet members,
`docker-compose.yml` just picks the command per service. Three services:
`world` (`world_server.py`), `robot-a`, `robot-b` (`agent.py --side a`/
`--side b`), on Compose's default network with built-in service-name
DNS. `robot-a`/`robot-b` mount `./experiments:/app/experiments`, so
`negotiation_trace.json` and (with `--debug-log`) the robot-status logs
land on the host - `visualize_network.py`/`export_timeline_csv.py`
deliberately stay outside any container (dev/debug tools, not fleet
agents) and read the mounted output afterward, same as they already do
against a plain venv run. `docker-compose up` defaults to
`--policy llm --scenario routine_vs_medical` (the project's own go-to
demo case) - needs `source .env` first, same habit the venv workflow
already requires.

**Two real bugs found and fixed along the way, not assumed away:**

1. `world_server.py`/`agent.py` bound their servers to `host="127.0.0.1"`
   - loopback-only, unreachable from another container's network
   namespace. Both gained a `--host` flag, default `0.0.0.0` (still
   reachable via localhost for plain local runs - verified live, zero
   behavior change). Non-negotiable for containers to work at all, not a
   judgment call.
2. A much subtler one, only found by actually running the containers
   together, not by code review alone: every dial failed forever
   (`All connection attempts failed`, retried indefinitely, burning an
   LLM call each time) even though both robots could reach `world` fine.
   Traced directly into `a2a-sdk`'s source
   (`ClientFactory.create_from_url`/`.create`) to confirm the actual
   mechanism: `create_client(peer_url)` fetches the peer's AgentCard from
   `peer_url`, but then builds the real JSON-RPC transport from the
   *card's own* `supported_interfaces[i].url` - not from `peer_url`
   itself. `build_responder_app` had always hardcoded that field to
   `"127.0.0.1"`, harmless on a single machine (that genuinely was how a
   local peer reached it) but fatal across containers - robot-a would
   fetch robot-b's card fine, then try to dial back through
   `127.0.0.1`, which inside robot-a's own container just points at
   itself. Fixed with a second, distinct flag - `--advertise-host`
   (default `127.0.0.1`, unchanged for local runs) - kept deliberately
   separate from `--host`: one is "what do I bind to," the other is
   "what address do I tell peers to use," and conflating them would have
   been wrong in both directions (`0.0.0.0` isn't dialable, `127.0.0.1`
   isn't reachable cross-container). Compose passes
   `--advertise-host robot-a`/`robot-b` explicitly.

**Why the second bug matters beyond just fixing it:** this had been a
live, latent bug since Phase 5 (D15) - just never observable, because
every negotiation until now ran on one machine, where `127.0.0.1` always
happened to be correct by coincidence. Containers didn't introduce the
bug; they were the first environment honest enough to expose it.

**Verified live, end-to-end, real containers (not simulated):** `docker
compose build` (all three images), `docker compose up` with real
Anthropic API calls - both robots negotiated correctly (`Robot B` wins,
matching ground truth), both exited cleanly (code 0), `world` stayed up
and reported healthy. Confirmed the volume mount by reading
`negotiation_trace.json` back from the host filesystem afterward, then
ran `visualize_network.py` and `export_timeline_csv.py` *locally*
(outside any container) against the still-running `world` container's
published port (`9500:9500`) and got correct output both times - the
full intended workflow (containerized fleet, host-side debug tooling)
confirmed working together, not just each half in isolation. Full test
suite (102) unaffected throughout.

**Would change our mind:** if Phase 9's multi-robot discovery (Pub/Sub)
makes per-robot self-advertised addressing obsolete - `--advertise-host`
would likely get replaced by whatever that phase's discovery mechanism
provides, not layered on top of it.

## D33 — Phase 8: real Cloud Run deployment, real OIDC identity, one agent as a Service, the other as a Job

**Decided:** `robot-b` runs as a Cloud Run **Service** (`--no-allow-unauthenticated`, its own service account, `--advertise-url` set to its real HTTPS URL) - a long-lived, always-listening responder, matching what Cloud Run Services are actually for. `robot-a` runs as a Cloud Run **Job**, not a Service - `--side a` without `--world-url` is a genuine one-shot process (dial, negotiate, exit; it never binds a port at all), which is exactly what Cloud Run Jobs are for (run-to-completion, same container, no listener) and exactly wrong for what Cloud Run Services expect (an always-listening port, or the platform kills the deployment as "failed to start"). No code changes were needed to make `--side a` job-shaped - it already was; the only gap was deploying it as the right *kind* of Cloud Run resource. Both share one image (`Dockerfile` from Phase 7, unchanged) - same pattern as Compose, just `gcloud run deploy`/`gcloud run jobs create` picking the command per resource instead of `docker-compose.yml`.

Two generalized flags make this work, both still fully backward compatible with plain local runs (defaults unchanged, verified live after each change):
- `--advertise-url` (generalized from D32's `--advertise-host`) - a full URL now, not just a hostname, since Cloud Run's URLs are HTTPS with no port at all (`https://robot-b-<project-number>.<region>.run.app`), which `http://{host}:{port}` can't represent.
- `--auth` (new) - fetches a real OIDC ID token (`get_id_token()`) scoped to `peer_url` and attaches it as `Authorization: Bearer` via `a2a-sdk`'s `ClientConfig.httpx_client` (every request through that client carries it automatically, including the AgentCard fetch - confirmed by inspecting `ClientFactory.__init__` directly). Off by default; a plain local or Compose run has no GCP credentials to fetch a token with and shouldn't try.

`get_id_token()` has two genuinely different code paths, not one, both verified live: real Cloud Run resources (the Job, using the metadata server via `robot-a`'s attached service account) resolve to compute-engine-style ADC and use `google.oauth2.id_token.fetch_id_token()` directly; local testing via `gcloud auth application-default login --impersonate-service-account=...` resolves to `impersonated_credentials.Credentials` instead, which `fetch_id_token()` doesn't understand at all (raises `DefaultCredentialsError`) - that case needs the explicit `impersonated_credentials.IDTokenCredentials` wrapper. Found this the hard way (a real crash, not anticipated) and fixed it before either path was assumed correct.

**Why:** this is the actual point of Phase 8 per the plan - "agent identity stops being a name in a prompt and becomes an actual security boundary." Locking `robot-b` down to `--no-allow-unauthenticated` and verifying it *rejects* an unauthenticated `curl` (a real `403`) before verifying it *accepts* an authenticated negotiation was deliberate - proving the boundary actually holds, not just that the happy path works.

**A messy, honest account of what fresh-project setup actually took** (kept here rather than smoothed over, since it's real signal for next time): a brand-new GCP project under an organization is missing several IAM grants Google used to hand out automatically - `storage.objects.get`, `logging.logWriter`, and `artifactregistry.writer` on the default compute service account all had to be granted by hand, one at a time, each discovered only by hitting the actual failure (twice compounded by Cloud Run's `--source` deploy path returning a generic "Build failed; check build logs" with the real logs written somewhere `gcloud builds log` couldn't find, `CLOUD_LOGGING_ONLY` mode - switching to the classic `gcloud builds submit` path finally surfaced the real, specific permission errors). Separately, `--allow-unauthenticated` used on the very first deploy was suspected (wrongly) to leave a persistent `allUsers` binding even after a later `--no-allow-unauthenticated` deploy - `gcloud run services get-iam-policy` showed that was never actually true here (no such binding existed), and the earlier `200` was almost certainly a revision-propagation timing artifact, not a real security gap - worth being honest that this specific worry turned out to be unfounded, confirmed by checking rather than assumed. And twice, an already-built container image went stale relative to the code (the `--advertise-url` rename, then the `--auth` addition) because `gcloud run deploy --image ...`/`gcloud run jobs execute` happily redeploy an old image with no warning that the source has moved on - a real, easy-to-repeat mistake with no guard rail from the tooling itself.

**Verified live, twice, for real - not simulated:**
1. `robot-a` running **locally** (impersonated credentials) against `robot-b` on **Cloud Run** (locked down) - real negotiation, correct outcome, `Robot B` wins on medical urgency.
2. `robot-a` running as a **Cloud Run Job** (real metadata-server credentials, no impersonation at all) against `robot-b` on **Cloud Run** - real negotiation, correct outcome, entirely cloud-to-cloud with zero involvement from the laptop beyond triggering the job.

Also verified directly: an unauthenticated `curl` to `robot-b` returns a genuine `403` before any of the above worked - the security boundary was checked to actually hold, not assumed.

**Not yet done** (deliberately out of scope for this pass, not forgotten): switching model calls to Claude on Vertex AI (still the direct Anthropic API today), and API keys through Secret Manager (still `.env`/`source .env`). Both are the next slice of Phase 8, not blocking what's already working.

**Would change our mind:** if `--side a` ever needs to react to *unprompted* incoming calls while deployed (not just dial out once and exit) - at that point it stops being Job-shaped and would need the same Service treatment as `robot-b`, which is exactly the dynamic-initiation (`--world-url`, D18) shape already built - this decision doesn't preclude that, it just correctly matches today's actual (one-shot) behavior to today's actual (Job) infrastructure.

## D34 — Phase 8: Claude on Vertex AI, flag-gated; Secret Manager dropped

**Decided:** `LLMPolicy` (`src/negotiation.py`) grows `use_vertex`/`vertex_project`/`vertex_region` constructor args; when set, `.client` returns `anthropic.AnthropicVertex(project_id=vertex_project, region=vertex_region)` instead of the plain `anthropic.Anthropic()`. `agent.py` exposes this as `--vertex`/`--vertex-project`/`--vertex-region`, off by default - a plain local venv or Compose run is completely unaffected, same as `--auth`'s precedent (D33). `MODEL = "claude-sonnet-5"` needs no translation between the two APIs: current-generation Vertex models use the bare first-party model ID, not a dated `@YYYYMMDD` suffix (that's only for pinned snapshot models) - one less unknown than expected going in. `--vertex-region` defaults to `"global"`, Vertex's own recommended region for Claude, overridable if a specific region is ever needed instead.

Paired with this: **Secret Manager is deliberately not being added.** The only secret in this project is `ANTHROPIC_API_KEY`, used in exactly two places - local `.env`/venv, and `docker-compose.yml`'s env vars. Once Cloud Run agents run with `--vertex`, they authenticate as their own service account (the same ADC/impersonation machinery D33 already built for OIDC) - no API key at all on that path. Secret Manager would have nothing left to protect in the cloud deployment; adding it anyway would be infra for a secret that no longer exists there. Local/Compose keep the plain `.env` file, unchanged.

**Why:** this closes the two items D33 left open ("Claude on Vertex AI... and Secret Manager") with less total surface area than originally scoped, once it became clear Vertex's own auth model makes the second item moot rather than merely deferred.

**Not yet done:** a live call against the real Vertex API hasn't been made yet - `LLMPolicy`'s vertex-mode construction is unit-tested (right client class, fails fast without a project), but the actual model call, the exact behavior of `region="global"`, and a redeploy of `robot-a`/`robot-b` with `--vertex` are still open. Verify live before treating Phase 8 as closed.

**Would change our mind:** if a future deployment target (Bedrock, Foundry, or a second GCP project) needs its own client class, the same `use_vertex`-style boolean-plus-config pattern on `LLMPolicy` extends directly - no rework, just another branch in `.client`.

## D35 — Phase 10a: OpenTelemetry tracing on the negotiation path, flag-gated

**Decided:** `agent.py --trace` (off by default, same shape as `--auth`/`--vertex`) turns on OpenTelemetry for the process. A new `src/tracing.py` is the seam: `span(name, **attrs)` is a stdlib-only no-op context manager until `tracing.setup()` runs, so `run.py`/`eval.py`/`simulate.py`/the test suite - none of which pass `--trace` - import it for free and pay nothing. Only `agent.py`'s `main()` calls `setup()`; `negotiation.py` (imported everywhere) calls `span()` around the LLM request, which is why the no-op default has to be genuinely zero-cost.

Four semantic spans, the readable layer: `negotiation.episode` (initiator, per run) → `negotiation.turn` (per exchange) → `llm.respond` (per model call, with `model`/`vertex`/`llm.input_tokens`/`llm.output_tokens`/`llm.stop_reason`); and on the responder, `negotiation.handle` per incoming message. Every span carries `corridor.scenario`.

**Cross-process linking works** and is the whole point: `setup()` calls `HTTPXClientInstrumentor().instrument()` (patches the httpx client class, so it covers both the a2a-sdk peer calls and - later, 10b - the mcp world calls), and `build_responder_app()` calls `tracing.instrument_fastapi(app)` to extract the incoming `traceparent`. Verified live with a 2-robot run: the initiator's `POST` span is the direct parent of the responder's `POST /` span, so `negotiation.episode` → `negotiation.turn` → (initiator `POST`) → (responder `POST /`) → `negotiation.handle` is one connected trace across both processes.

**Bonus, not planned:** a2a-sdk ships its own OpenTelemetry instrumentation. The moment there's a `TracerProvider`, its full task-lifecycle spans (`EventQueueSource.*`, `JsonRpcDispatcher.*`, `DefaultRequestHandler.*`) appear for free - deep protocol visibility, exactly Phase 10's goal, with our four spans as the semantic index on top. The console exporter makes ~50 spans/exchange look like a wall; a real trace UI (Cloud Trace, 10b) renders it as one collapsible tree.

**Exporter** from the standard `OTEL_TRACES_EXPORTER` env var rather than a bespoke flag: `console` (default, dev) or `gcp` (`CloudTraceSpanExporter`, for 10b's deployed path). `SimpleSpanProcessor`, not `BatchSpanProcessor` - a negotiation is a handful of our spans, the initiator is a short-lived process, and a signal-killed responder would strand a batch queue at exit (found this the hard way - the first run's responder emitted nothing).

**Scope - what 10a deliberately does NOT trace, deferred to 10b:** the MCP world path (`world_server.py`, `run_robot`'s `--world-url` loop), the `--webhook` initiator variant, and live Cloud Trace verification in the deployed environment. All three are bundled with 10b's Cloud Run redeploy (itself waiting on the Vertex quota, D34). The `--webhook` *responder* is already covered - it shares `build_responder_app()`.

**Would change our mind:** if the a2a-sdk auto-instrumentation noise becomes a real problem for local console debugging, add a span processor that filters by instrumentation scope - but not before it actually gets in the way, and never for the `gcp` path where the UI collapses it anyway.

## D36 — GeminiPolicy: the negotiation policy, reimplemented against a second provider

**Decided:** a `GeminiPolicy` in `src/negotiation.py` alongside `LLMPolicy` - Claude's exact negotiation contract (`respond(me, other, history, max_turns) -> Message`), run against Gemini via Vertex AI (`google-genai` SDK, `genai.Client(vertexai=True, project, location)`, ADC auth, no API key). Selected with `--policy gemini`; reuses `--vertex-project`/`--vertex-region` (their help text is now "Vertex-backed policies" rather than "--vertex only"), plus a new `--gemini-model` (default `gemini-2.5-flash`).

What's genuinely shared vs. what's provider-specific, since that's the whole point:
- **Shared, unchanged:** the `SYSTEM` prompt, `message_from_tool_call()` (extracted from `LLMPolicy._to_message` to a module function - both policies hand it their parsed function-call dict), `_log_raw()` (extracted from `LLMPolicy._log`), the `llm.respond` tracing span, and every line of the negotiation loop, executor, wire format, and world integration.
- **Provider-specific, and that's all:** the SDK and client construction; the tool-schema dialect (`types.FunctionDeclaration` + `parameters_json_schema` vs Anthropic's `input_schema`; `goes_first` is a nullable string enum here because Gemini enums are string-only, where Anthropic put `None` in the enum list - `message_from_tool_call` maps a missing/odd value to `None` either way); forced tool use (`tool_config` → `FunctionCallingConfig(mode="ANY")` vs `tool_choice={"type": "tool"}`); roles (`user`/`model` vs `user`/`assistant`); response shape (`reply.function_calls[0].args` vs `reply.content` block iteration); token counts (`usage_metadata.prompt_token_count` vs `usage.input_tokens`).

**Also refactored:** `build_robots(side, scenario_id, policy)` now takes a policy *name string* (deterministic policies, built on the spot) OR an already-constructed instance (llm/gemini need config). `agent.py`'s `_make_policy(args)` does the CLI→policy mapping. This keeps `build_robots` from knowing about argparse and leaves `test_agent.py`'s `build_robots("a", id, "stubborn")` calls working unchanged. `_build_policy` (D34's helper) is gone, folded into `_make_policy`.

**Why:** framed as a multi-provider exercise, not a workaround. It's D7 ("policy is pluggable; anything that assumes an agent is *an LLM* is a bug") taken one step further - anything that assumes an agent is *a specific LLM provider* is also a bug - and it de-risks D34's "would change our mind" clause about Bedrock/Foundry/other backends: the `use_vertex`-style boolean-plus-config pattern extended to a whole different SDK with no change to anything outside the policy class.

**Verified live:** `--policy gemini` (Robot A) vs deterministic `always_yield` (Robot B), `routine_vs_medical` - real Gemini call via Vertex, correct outcome (Robot A yields: "my task is not time-critical"), and with `--trace` the `llm.respond` span carries `provider=gemini`, `model=gemini-2.5-flash`, real token counts. 113 tests pass.

**Unplanned but useful:** Gemini's Vertex quota is *non-zero* on this fresh project, unlike `anthropic-claude-sonnet` (D34, still 0, support ticket open). So `--policy gemini` is a working way to exercise the full deployed pipeline (Cloud Run → Vertex → Cloud Trace, 10b) while the Claude-on-Vertex quota is stuck - swap the policy, not the infrastructure.

**Would change our mind:** if the two policies start sharing more than the prompt + `message_from_tool_call` + the span - e.g. if a third provider arrives - extract a small `_ToolNegotiationPolicy` base with the history-formatting and the parse-and-return tail as template methods. Two providers doesn't justify that yet; three would.

## D37 — Phase 10b-1: tracing the MCP world path

**Decided:** extend D35's `--trace` to the world channel. `world_server.py --trace` calls `tracing.setup("world")`, builds its Starlette app itself (`mcp.streamable_http_app(...)` — `mcp.run()` does exactly this internally but exposes no seam), wraps it via `tracing.instrument_asgi()` (returns `OpenTelemetryMiddleware(app)` when on, the app untouched when off — there's no `FastAPIInstrumentor`-style in-place instrumentor for a bare Starlette app), and runs uvicorn directly. `get_observation` and `propose_action` each wrap their body in a `world.*` span (positions, `accepted`, `at_boundary` as attributes); `get_map`/`get_log` are one-shot and left to the ASGI request span. `agent.py`'s `mcp_call()` wraps each call in an `mcp.<tool>` span, and `run_robot`'s loop gets `world.episode` → `world.tick`. The `--webhook` initiator finally gets its `negotiation.episode`/`negotiation.turn` spans too (the one path D35 skipped).

**`tracing.span()` became dual-protocol.** `run_robot` needs `async with Client(world_url) as world, tracing.span("world.episode", ...)` - a sync `@contextmanager` raises `TypeError: object does not support the asynchronous context manager protocol` there. Rewrote `span()` to return a small `_Span` object implementing `__enter__`/`__exit__` *and* `__aenter__`/`__aexit__` (both delegating to one `_enter`/`_exit` pair). Every existing `with tracing.span(...)` call is unaffected; the async form now also works. Found the hard way - the first 3-process run crashed all three robots on the combined `async with`.

**Verified live, 3 processes** (`world_server.py --trace` + two `agent.py --world-url --trace`, deterministic policies): every `world.get_observation`/`world.propose_action` span on the world nests into the calling robot's trace (all 38 in one run, none orphaned) - robot `mcp.propose_action` → world `POST /mcp` → mcp-sdk `tools/call` → `world.propose_action`, one trace across two processes. The `mcp` library ships its own OTel instrumentation (like a2a-sdk does), so its session/tool-lifecycle spans appear for free alongside ours. Console output is a wall (~40 spans/episode/robot); the Cloud Trace UI (10b-3) collapses it.

**Scope:** local only, same as 10a. 10b-2 (Firestore-backed world) and 10b-3 (deploy + Cloud Trace verification) are still ahead. 115 tests.
