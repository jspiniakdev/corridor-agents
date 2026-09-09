"""Phase 10a: optional OpenTelemetry tracing for the negotiation path.
See docs/PLAN.md §5 (Phase 10), D35.

Off by default and genuinely zero-cost when off: span() is a no-op
context manager and this module imports nothing beyond the stdlib until
setup() runs. Only agent.py --trace calls setup(); negotiation.py (which
every entry point imports) calls span() around the LLM request, so the
no-op default has to cost nothing for `python run.py`, eval.py, tests -
none of which ever call setup().

When on (agent.py --trace), setup() lazily pulls in the OTel SDK,
instruments the shared httpx client used by BOTH the A2A peer calls and
the MCP world calls (outbound W3C traceparent injection), and picks an
exporter from the standard OTEL_TRACES_EXPORTER env var - "console"
(default, dev) or "gcp" (Cloud Trace, for the deployed path in 10b).
build_responder_app() calls instrument_fastapi() so the responder side
extracts the incoming traceparent and its spans nest under the caller's -
one connected trace tree across the two robot processes, which is the
whole point of Phase 10 (D35).
"""

from __future__ import annotations

import contextlib
import os

_on = False
_tracer = None
_provider = None
_scenario = None


@contextlib.contextmanager
def span(name: str, **attrs):
    """A real span when setup() has run, a plain no-op context manager
    otherwise. Yields the span object (or None when off) so callers can
    attach attributes they only know after the fact - token counts, say."""
    if not _on:
        yield None
        return
    with _tracer.start_as_current_span(name) as s:
        if _scenario:
            s.set_attribute("corridor.scenario", _scenario)
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v)
        yield s


def setup(service_name: str, scenario: str | None = None) -> None:
    """Turn tracing on for this process. Idempotent - safe to call once
    from agent.py's main() when --trace is passed. service_name shows up
    as the service in the trace UI (e.g. "robot-a"); scenario is stamped
    on every span this process emits."""
    global _on, _tracer, _provider, _scenario
    if _on:
        return

    from opentelemetry import trace
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    _scenario = scenario
    _provider = TracerProvider(resource=Resource.create({"service.name": service_name}))

    if os.environ.get("OTEL_TRACES_EXPORTER", "console") == "gcp":
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

        exporter = CloudTraceSpanExporter()
    else:
        exporter = ConsoleSpanExporter()

    # SimpleSpanProcessor, not Batch: a negotiation is a handful of spans,
    # and one side (the initiator, and a signal-killed responder) is a
    # short-lived process where a batch queue would still hold spans at
    # exit. Synchronous export is fine at this volume.
    _provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(_provider)

    # Patches the httpx client class, so it covers both the a2a-sdk peer
    # calls and the mcp world calls, and the --auth path's hand-built
    # httpx.AsyncClient too - all of them get the traceparent header.
    HTTPXClientInstrumentor().instrument()

    _tracer = trace.get_tracer("corridor-agents")
    _on = True


def instrument_fastapi(app) -> None:
    """The responder side of the propagation: extract the incoming
    traceparent so this process's spans nest under the caller's. No-op
    unless setup() ran - build_responder_app() always calls this."""
    if not _on:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


def shutdown() -> None:
    """Shut the provider down cleanly (flush any exporter buffers, stop
    background threads). SimpleSpanProcessor exports synchronously so
    nothing should be pending, but agent.py's main() calls this in a
    finally regardless - and it's what a switch back to a batch processor
    would need."""
    if _on and _provider is not None:
        _provider.shutdown()
