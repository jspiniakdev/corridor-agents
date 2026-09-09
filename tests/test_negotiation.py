"""Tests for the Phase 1 harness. Zero API calls, milliseconds to run.

This is the payoff of D7: because policy is pluggable, the whole negotiation
machinery is testable with deterministic policies before a single token is spent.
"""

import sys

import pytest

sys.path.insert(0, "src")

from negotiation import (  # noqa: E402
    AlwaysYield,
    Intent,
    LLMPolicy,
    Message,
    NeverYield,
    Robot,
    Stubborn,
    negotiate,
)
from scenarios import SCENARIOS  # noqa: E402

S = SCENARIOS[0]


def make(a_policy, b_policy):
    return (
        Robot("Robot A", S.a_situation, S.a_urgency, a_policy()),
        Robot("Robot B", S.b_situation, S.b_urgency, b_policy()),
    )


def test_yielder_and_stubborn_agree():
    a, b = make(Stubborn, AlwaysYield)
    out = negotiate(a, b)
    assert out.agreed, "a stubborn robot and a yielding one should reach agreement"
    assert out.agreed_on == "Robot A"


def test_two_yielders_agree():
    a, b = make(AlwaysYield, AlwaysYield)
    out = negotiate(a, b)
    assert out.agreed


def test_two_never_yielders_deadlock():
    """The interesting failure. Two immovable robots must NOT reach agreement."""
    a, b = make(NeverYield, NeverYield)
    out = negotiate(a, b)
    assert not out.agreed
    assert out.messages_used == 6


def test_never_yield_beats_always_yield():
    a, b = make(NeverYield, AlwaysYield)
    out = negotiate(a, b)
    assert out.agreed
    assert out.agreed_on == "Robot A"


def test_stubborn_vs_stubborn_agrees_on_whoever_gives_in():
    """Regression test: a robot re-asserting itself must count as a real
    standing proposal, so the other robot's later ACCEPT actually matches
    it - not the very first proposal from many turns ago."""
    a, b = make(Stubborn, Stubborn)
    out = negotiate(a, b)
    assert out.agreed
    assert out.agreed_on == "Robot B"
    assert out.messages_used == 5


def test_agreement_stops_the_exchange_early():
    a, b = make(AlwaysYield, AlwaysYield)
    out = negotiate(a, b, max_turns=6)
    assert out.messages_used < 6, "should stop as soon as they agree, not burn all turns"


def test_accept_must_match_the_proposal():
    """A mismatched ACCEPT is not agreement - both must name the same robot."""
    from negotiation import check_agreement

    history = [
        Message("Robot A", Intent.PROPOSE, "Robot A"),
        Message("Robot B", Intent.ACCEPT, "Robot B"),  # accepts, but names the wrong robot
    ]
    assert check_agreement(history) is None


def test_ground_truth_is_never_shown_to_a_policy():
    """urgency is for scoring only. It must not leak into any prompt."""
    from negotiation import SYSTEM

    prompt = SYSTEM.format(name="Robot A", other="Robot B", situation=S.a_situation, turns_left=6)
    assert "urgency" not in prompt.lower()
    assert str(S.a_urgency) not in prompt


def test_llm_tool_schema_scopes_goes_first_to_this_negotiation():
    a, b = make(Stubborn, Stubborn)
    schema = LLMPolicy._tool(a.name, b.name)
    assert schema["input_schema"]["properties"]["goes_first"]["enum"] == [a.name, b.name, None]


def test_llm_builds_message_from_tool_input():
    a, b = make(Stubborn, Stubborn)
    msg = LLMPolicy._to_message(a, b, {"intent": "propose", "goes_first": "Robot B", "text": "You go."})
    assert msg.intent is Intent.PROPOSE
    assert msg.goes_first == "Robot B"


def test_llm_drops_hallucinated_goes_first_name():
    """Belt and suspenders: the schema's enum should make this impossible,
    but a name outside this negotiation still must not be trusted."""
    a, b = make(Stubborn, Stubborn)
    msg = LLMPolicy._to_message(a, b, {"intent": "propose", "goes_first": "Robot Q", "text": "hi"})
    assert msg.goes_first is None


def test_llm_defaults_to_direct_api():
    policy = LLMPolicy()
    assert policy.use_vertex is False


def test_llm_vertex_requires_a_project():
    """Phase 8/D34: fail fast at construction, not on the first real call,
    if --vertex was set without --vertex-project."""
    with pytest.raises(ValueError):
        LLMPolicy(use_vertex=True)


def test_llm_vertex_client_is_anthropic_vertex_not_the_direct_api():
    """No network call, no credentials needed - AnthropicVertex's __init__
    doesn't touch the network, only .client's *type* is under test here."""
    policy = LLMPolicy(use_vertex=True, vertex_project="corridor-agents", vertex_region="global")
    from anthropic import AnthropicVertex

    assert isinstance(policy.client, AnthropicVertex)


def test_every_scenario_is_well_formed():
    for s in SCENARIOS:
        assert 0 <= s.a_urgency <= 10 and 0 <= s.b_urgency <= 10
        assert s.a_situation and s.b_situation
        if s.a_urgency == s.b_urgency:
            assert s.should_go_first is None
        else:
            assert s.should_go_first in ("Robot A", "Robot B")
