"""Phase 10a: the tracing shim must be a genuine no-op until setup() runs -
that's what lets negotiation.py call span() unconditionally without every
non-agent entry point (run.py, eval.py, the tests) paying for OpenTelemetry.
No setup() call here on purpose; the live cross-process trace is verified by
hand (see D35), not in the unit suite.
"""

import sys

sys.path.insert(0, "src")

import tracing  # noqa: E402


def test_span_is_a_noop_context_manager_when_off():
    assert tracing._on is False
    with tracing.span("anything", foo="bar", n=1) as s:
        assert s is None  # nothing to attach attributes to


def test_span_yields_a_value_and_swallows_attrs_without_error():
    # None-valued attrs are dropped by span(); make sure that path doesn't
    # blow up while tracing is off either.
    with tracing.span("x", a=None, b=2):
        pass


def test_instrument_fastapi_is_a_noop_when_off():
    tracing.instrument_fastapi(object())  # would explode if it tried to use the arg


def test_importing_negotiation_does_not_turn_tracing_on():
    import negotiation  # noqa: F401

    assert tracing._on is False
