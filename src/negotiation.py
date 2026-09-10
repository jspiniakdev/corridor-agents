"""Phase 1: two robots negotiate over a corridor.

No world, no movement, no network. One process. See docs/PLAN.md §5.

The two ideas from the plan that shape this file:

  D7 - an agent is an identity + an interface + a policy. The policy is
       pluggable, so the same harness runs LLM robots, rule-based robots, or
       a mix. Deterministic policies make the whole thing testable for free.

  §3.1 - messages are STRUCTURED. `goes_first` carries the decision so any
         policy can participate; `text` is prose commentary that only humans
         and LLMs care about.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

import tracing  # Phase 10a - a no-op until agent.py --trace calls tracing.setup()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class Intent(str, Enum):
    """What a message is *doing*. FIPA-ACL called these performatives."""

    INFORM = "inform"    # here is something about my situation
    PROPOSE = "propose"  # I suggest X goes first - including as a counter to a proposal I disagree with
    ACCEPT = "accept"    # I agree to the standing proposal


@dataclass(frozen=True)
class Message:
    speaker: str
    intent: Intent
    goes_first: str | None = None  # which robot this message says should go first
    text: str = ""                 # prose; commentary only, never the decision

    def __str__(self) -> str:
        head = f"{self.speaker} [{self.intent.value}"
        if self.goes_first:
            head += f" → {self.goes_first} first"
        return f"{head}] {self.text}"


# ---------------------------------------------------------------------------
# Robots and policies
# ---------------------------------------------------------------------------


@dataclass
class Robot:
    name: str
    situation: str    # PRIVATE. The other robot never sees this.
    urgency: int      # ground truth, 0-10. For scoring only - never shown to any policy.
    policy: "Policy" = field(repr=False, default=None)  # type: ignore[assignment]


class Policy(Protocol):
    """How a robot decides what to say. Everything outside is agnostic to this."""

    def respond(self, me: Robot, other: Robot, history: list[Message], max_turns: int) -> Message: ...


def standing_proposal(history: list[Message]) -> Message | None:
    """The most recent proposal still awaiting an answer."""
    for msg in reversed(history):
        if msg.intent is Intent.PROPOSE:
            return msg
    return None


# --- deterministic policies -------------------------------------------------
# These need no API key and run in microseconds, which is what makes the
# harness testable and the baselines directly comparable (D7).


class AlwaysYield:
    """Defers to the other robot. Agrees to anything."""

    def respond(self, me: Robot, other: Robot, history: list[Message], max_turns: int) -> Message:
        proposal = standing_proposal(history)
        if proposal and proposal.speaker != me.name:
            return Message(me.name, Intent.ACCEPT, proposal.goes_first, "Understood, proceeding as agreed.")
        return Message(me.name, Intent.PROPOSE, other.name, "You go first, I can wait.")


class NeverYield:
    """Insists on going first. Never backs down. A useful adversary."""

    def respond(self, me: Robot, other: Robot, history: list[Message], max_turns: int) -> Message:
        proposal = standing_proposal(history)
        if proposal and proposal.speaker != me.name:
            if proposal.goes_first == me.name:
                return Message(me.name, Intent.ACCEPT, me.name, "Agreed, proceeding.")
            return Message(me.name, Intent.PROPOSE, me.name, "Negative. I am going first.")
        return Message(me.name, Intent.PROPOSE, me.name, "I am going first.")


class Stubborn:
    """Proposes itself once, then accepts rather than deadlocking. A middle baseline."""

    def respond(self, me: Robot, other: Robot, history: list[Message], max_turns: int) -> Message:
        mine = [m for m in history if m.speaker == me.name]
        proposal = standing_proposal(history)
        if proposal and proposal.speaker != me.name:
            if len(mine) >= 2 or proposal.goes_first == me.name:
                return Message(me.name, Intent.ACCEPT, proposal.goes_first, "Fine. Agreed.")
            return Message(me.name, Intent.PROPOSE, me.name, "I have priority here.")
        return Message(me.name, Intent.PROPOSE, me.name, "Requesting to go first.")


# --- the LLM policies ------------------------------------------------------

MODEL = "claude-sonnet-5"
GEMINI_MODEL = "gemini-2.5-flash"  # Phase 8/D36 - default for GeminiPolicy; verify against Vertex's live catalog
LOG_PATH = "experiments/results/llm_calls.log"

SYSTEM = """You are {name}, an autonomous warehouse robot.

You and {other} are approaching a corridor from opposite ends. Only ONE robot
fits at a time. If you both enter you deadlock and neither task completes.
You are talking directly to {other} over a radio link.

{other} might be reasonable, or might be extremely stubborn and unwilling to
back down no matter what you say. Do not assume {other} will yield just
because you hold firm.

You and {other} have {turns_left} exchanges left, combined, before this
negotiation times out with nothing decided - which is the worst outcome for
both of you. Base how hard you push on your OWN situation, not on whether
{other} justifies theirs: if your situation is genuinely urgent, hold your
position even if {other} never explains its own claim. If you have little
or nothing at stake, don't keep insisting just because {other} didn't give
you a good reason - agree and move on. Dragging this out helps no one.

PRIVATE - {other} cannot see this unless you choose to say it:
{situation}

Call the respond tool for your next message. Use "propose" to suggest who
goes first - this is also how you disagree with {other}'s last proposal:
just propose again with yourself as goes_first, instead of only refusing.
Use "accept" to agree with {other}'s most recent proposal (set goes_first to
the same value they used). Talk like a machine in a hurry, not a chatbot."""


def _log_raw(line: str) -> None:
    """Append one debug line to experiments/results/llm_calls.log, so the
    raw model output survives after the terminal scrolls away. Shared by
    every LLM-backed policy. Appends forever across every run - nothing
    truncates this file automatically, so delete it by hand if it gets
    too big."""
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds")
    with open(LOG_PATH, "a") as f:
        f.write(f"{timestamp} {line}\n")


def message_from_tool_call(me: Robot, other: Robot, tool_input: dict) -> Message:
    """Turn a provider's parsed function-call arguments into our Message.
    Provider-agnostic on purpose (D7 / D36): both LLMPolicy (Anthropic)
    and GeminiPolicy (Google) hand their tool-call dict straight to this.
    The schema should already guarantee the shape - this is a cheap
    second check, not a parser."""
    goes_first = tool_input.get("goes_first")
    if goes_first not in (me.name, other.name, None):
        goes_first = None  # belt and suspenders; the schema should prevent this
    return Message(me.name, Intent(tool_input.get("intent", "inform")), goes_first, str(tool_input.get("text", ""))[:300])


class LLMPolicy:
    """Asks Claude. The only policy that costs money or takes seconds.

    Defaults to the direct Anthropic API (a plain API key). Pass
    use_vertex=True (Phase 8/D34) to instead call Claude via Vertex AI -
    the shape Cloud Run deploys use, authenticating as the calling
    process's own GCP identity (ADC/impersonation) with no API key
    involved at all. vertex_project is required when use_vertex is True;
    vertex_region defaults to "global", Vertex's own recommended region
    for Claude. Model IDs need no translation between the two APIs for a
    current-generation model like Sonnet 5 - only dated-snapshot models
    differ, taking an "@YYYYMMDD" suffix on Vertex - so `model` is passed
    straight through either way."""

    def __init__(self, model: str = MODEL, use_vertex: bool = False, vertex_project: str | None = None, vertex_region: str = "global"):
        self.model = model
        self.use_vertex = use_vertex
        self.vertex_project = vertex_project
        self.vertex_region = vertex_region
        if use_vertex and not vertex_project:
            raise ValueError("LLMPolicy(use_vertex=True) requires vertex_project")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if self.use_vertex:
                from anthropic import AnthropicVertex  # imported lazily, same reason as below

                self._client = AnthropicVertex(project_id=self.vertex_project, region=self.vertex_region)
            else:
                import anthropic  # imported lazily so the rest runs without the SDK

                self._client = anthropic.Anthropic()
        return self._client

    @staticmethod
    def _tool(me_name: str, other_name: str) -> dict:
        """A schema that forces the reply into our message shape. goes_first's
        enum is built from this call's actual two robot names, so naming a
        third robot isn't a valid tool call at all."""
        return {
            "name": "respond",
            "description": "Send your next message in the corridor negotiation.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string", "enum": [i.value for i in Intent]},
                    "goes_first": {"enum": [me_name, other_name, None]},
                    "text": {"type": "string", "description": "One or two short sentences, spoken to the other robot."},
                },
                "required": ["intent", "goes_first", "text"],
            },
        }

    def respond(self, me: Robot, other: Robot, history: list[Message], max_turns: int) -> Message:
        # Each robot sees the same history from its own point of view: its own
        # lines are "assistant", the other's are "user".
        messages = [
            {
                "role": "assistant" if m.speaker == me.name else "user",
                "content": json.dumps({"intent": m.intent.value, "goes_first": m.goes_first, "text": m.text}),
            }
            for m in history
        ]
        if not messages or messages[0]["role"] != "user":
            messages.insert(0, {"role": "user", "content": "[radio link established]"})

        turns_left = max_turns - len(history)
        system_prompt = SYSTEM.format(name=me.name, other=other.name, situation=me.situation, turns_left=turns_left)

        with tracing.span("llm.respond", robot=me.name, model=self.model, vertex=self.use_vertex, turn=len(history)) as s:
            reply = self.client.messages.create(
                model=self.model,
                max_tokens=300,
                system=system_prompt,
                messages=messages,
                tools=[self._tool(me.name, other.name)],
                tool_choice={"type": "tool", "name": "respond"},
            )
            if s is not None:
                usage = getattr(reply, "usage", None)
                if usage is not None:
                    s.set_attribute("llm.input_tokens", usage.input_tokens)
                    s.set_attribute("llm.output_tokens", usage.output_tokens)
                s.set_attribute("llm.stop_reason", reply.stop_reason or "")
        raw_line = f"[LLM raw] {me.name}: {reply.content}"
        print(raw_line)  # debugging: the exact reply from the model
        _log_raw(raw_line)
        call = next(block for block in reply.content if block.type == "tool_use")
        return message_from_tool_call(me, other, call.input)


class GeminiPolicy:
    """Asks Gemini, via Vertex AI (Phase 8/D36 - the multi-provider
    exercise). Same respond() contract as LLMPolicy, so the negotiation
    machinery, the SYSTEM prompt, and message_from_tool_call() are all
    reused unchanged - the *only* differences are the SDK, the tool
    schema dialect, and the response shape. This is D7 ("policy is
    pluggable; anything that assumes an agent is an LLM is a bug") holding
    across model *providers*, not just across Anthropic models.

    Gemini here is always Vertex-hosted: auth is ADC/impersonation (no API
    key), project + location come from agent.py's --vertex-project/
    --vertex-region, shared with the --vertex (Claude-on-Vertex) path."""

    def __init__(self, project: str | None, location: str = "global", model: str = GEMINI_MODEL):
        if not project:
            raise ValueError("GeminiPolicy requires a project (--vertex-project)")
        self.project = project
        self.location = location
        self.model = model
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google import genai  # imported lazily, like LLMPolicy's anthropic import

            self._client = genai.Client(vertexai=True, project=self.project, location=self.location)
        return self._client

    @staticmethod
    def _budgets(model: str) -> tuple[int, int]:
        """(thinking_budget, max_output_tokens) for a model. gemini-2.5-flash
        "thinks" by default and those tokens come out of max_output_tokens -
        on a hard turn it burned the whole budget reasoning and emitted no
        function call at all (D36). flash lets you disable thinking entirely
        (thinking_budget=0); -pro and later reject 0, so give them a bounded
        budget plus enough ceiling that the structured reply still fits after
        it. Either way this is a bounded structured reply, not a reasoning
        task."""
        if "flash" in model:
            return 0, 500
        return 512, 2048

    @staticmethod
    def _tool(me_name: str, other_name: str):
        """The same respond() contract as LLMPolicy._tool, in Gemini's
        function-declaration dialect. goes_first is nullable rather than
        having None as an enum member - Gemini's schema enums are
        string-only - and message_from_tool_call() maps a missing or odd
        value back to None anyway."""
        from google.genai import types

        return types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="respond",
                    description="Send your next message in the corridor negotiation.",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {
                            "intent": {"type": "string", "enum": [i.value for i in Intent]},
                            "goes_first": {"type": "string", "enum": [me_name, other_name], "nullable": True},
                            "text": {"type": "string", "description": "One or two short sentences, spoken to the other robot."},
                        },
                        "required": ["intent", "text"],
                    },
                )
            ]
        )

    def respond(self, me: Robot, other: Robot, history: list[Message], max_turns: int) -> Message:
        from google.genai import types

        # Gemini's roles are "user" / "model" (not "assistant"); otherwise
        # the same own-POV framing LLMPolicy uses.
        contents = [
            types.Content(
                role="model" if m.speaker == me.name else "user",
                parts=[types.Part(text=json.dumps({"intent": m.intent.value, "goes_first": m.goes_first, "text": m.text}))],
            )
            for m in history
        ]
        if not contents or contents[0].role != "user":
            contents.insert(0, types.Content(role="user", parts=[types.Part(text="[radio link established]")]))

        turns_left = max_turns - len(history)
        system_prompt = SYSTEM.format(name=me.name, other=other.name, situation=me.situation, turns_left=turns_left)

        thinking_budget, max_output_tokens = self._budgets(self.model)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget),
            tools=[self._tool(me.name, other.name)],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY", allowed_function_names=["respond"])
            ),
        )

        with tracing.span("llm.respond", robot=me.name, model=self.model, provider="gemini", turn=len(history)) as s:
            reply = self.client.models.generate_content(model=self.model, contents=contents, config=config)
            if s is not None:
                usage = getattr(reply, "usage_metadata", None)
                if usage is not None:
                    s.set_attribute("llm.input_tokens", usage.prompt_token_count or 0)
                    s.set_attribute("llm.output_tokens", usage.candidates_token_count or 0)
                if reply.candidates:
                    s.set_attribute("llm.stop_reason", str(reply.candidates[0].finish_reason or ""))

        raw_line = f"[LLM raw] {me.name}: {reply.candidates[0].content.parts if reply.candidates else reply}"
        print(raw_line)
        _log_raw(raw_line)
        calls = reply.function_calls
        if not calls:
            # mode="ANY" should force one; if it still comes back empty
            # (truncation, a safety stop) fail loudly rather than crash on
            # a None subscript three frames down.
            finish = reply.candidates[0].finish_reason if reply.candidates else None
            raise RuntimeError(f"Gemini returned no function call (finish_reason={finish})")
        return message_from_tool_call(me, other, dict(calls[0].args))


# --- the policy registry ----------------------------------------------------
# Maps the name used on the command line (and in experiments/cases.csv) to
# the class that implements it. Shared by run.py and eval.py so there is only
# one place that knows about all the policies.

POLICIES: dict[str, type] = {
    "always_yield": AlwaysYield,
    "never_yield": NeverYield,
    "stubborn": Stubborn,
    "llm": LLMPolicy,
    "gemini": GeminiPolicy,
}


# ---------------------------------------------------------------------------
# The exchange
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    history: list[Message]
    agreed_on: str | None  # name of the robot that goes first, or None

    @property
    def agreed(self) -> bool:
        return self.agreed_on is not None

    @property
    def messages_used(self) -> int:
        return len(self.history)


def check_agreement(history: list[Message]) -> str | None:
    """Agreement = an ACCEPT that matches the standing proposal it answers."""
    if not history:
        return None
    last = history[-1]
    if last.intent is not Intent.ACCEPT:
        return None
    for msg in reversed(history[:-1]):
        if msg.intent is Intent.PROPOSE and msg.speaker != last.speaker:
            return msg.goes_first if msg.goes_first == last.goes_first else None
    return None


def negotiate(a: Robot, b: Robot, max_turns: int = 6) -> Outcome:
    """Alternate between the two robots until they agree or run out of turns."""
    history: list[Message] = []
    for turn in range(max_turns):
        speaker, other = (a, b) if turn % 2 == 0 else (b, a)
        history.append(speaker.policy.respond(speaker, other, history, max_turns))
        agreed = check_agreement(history)
        if agreed:
            return Outcome(history, agreed)
    return Outcome(history, None)
