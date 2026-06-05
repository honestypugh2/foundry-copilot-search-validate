"""
Tracing / OpenTelemetry bootstrap for the orchestrators.

Calling ``enable_tracing()`` configures the Azure Monitor exporter against
Application Insights so that:

  - Spans emitted by the Azure AI Projects SDK (agent runs, tool calls)
  - Spans emitted by the Microsoft Agent Framework (when installed)
  - Spans emitted by the Azure AI Search SDK
  - Custom spans emitted by orchestrators

flow into App Insights and surface in the Foundry portal **Tracing** tab,
provided the AI Foundry project has an App Insights connection (see
``infra/bicep/main.bicep`` resource ``aiProjectAppInsightsConnection``).

Configuration (env vars, all optional):

  ``APPLICATIONINSIGHTS_CONNECTION_STRING``
      App Insights connection string. If unset, tracing is disabled
      and ``enable_tracing`` is a no-op.
  ``AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED``
      When ``true`` (default), GenAI prompts/completions are captured in
      span attributes. Set to ``false`` to scrub message contents.

The function is idempotent and safe to call multiple times. It is also
defensive: if any of the optional packages are not installed it logs a
single warning and returns without raising.
"""

from __future__ import annotations

import atexit
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_initialised: bool = False
_flush_hook_registered: bool = False


def _flush_tracer_provider() -> None:
    """Force-flush the OTel tracer provider so buffered spans ship before exit.

    Without this, short-lived scripts (CLI, tests) can exit while the
    BatchSpanProcessor still has spans in memory, and they never reach
    Application Insights.
    """
    try:
        from opentelemetry import trace  # type: ignore

        provider = trace.get_tracer_provider()
        force_flush = getattr(provider, "force_flush", None)
        if callable(force_flush):
            force_flush(timeout_millis=10_000)
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            shutdown()
    except Exception:  # noqa: BLE001
        # Never let flush errors crash the host process at exit.
        pass


def enable_tracing(connection_string: Optional[str] = None) -> bool:
    """Configure Azure Monitor + OTel exporters once.

    Returns ``True`` when tracing was activated, ``False`` otherwise.
    """
    global _initialised
    if _initialised:
        return True

    conn = connection_string or os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        logger.debug(
            "enable_tracing: APPLICATIONINSIGHTS_CONNECTION_STRING not set; "
            "tracing disabled"
        )
        return False

    # Ensure GenAI auto-instrumentation captures prompt/response content
    # (the OpenAI v2 / Azure AI Projects SDKs honour this env var).
    os.environ.setdefault(
        "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true"
    )
    os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")
    # The Azure AI Projects SDK only emits agent-run / tool-call spans when this
    # experimental flag is set. Without it the Foundry portal "Tracing" tab is
    # empty even when App Insights is wired up correctly.
    os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
    except ImportError:
        logger.warning(
            "enable_tracing: azure-monitor-opentelemetry not installed; "
            "no traces will be exported. Install with `pip install "
            "azure-monitor-opentelemetry`."
        )
        return False

    try:
        configure_azure_monitor(connection_string=conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("enable_tracing: configure_azure_monitor failed: %s", exc)
        return False

    # Best-effort: enable Microsoft Agent Framework observability when present.
    try:
        from agent_framework.observability import setup_observability  # type: ignore

        try:
            setup_observability(applicationinsights_connection_string=conn)
        except TypeError:
            # Older versions accept no kwargs.
            setup_observability()
        logger.info("enable_tracing: agent_framework.observability activated")
    except ImportError:
        logger.debug(
            "enable_tracing: agent_framework.observability not installed; skipping"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "enable_tracing: agent_framework observability setup failed: %s", exc
        )

    _initialised = True
    global _flush_hook_registered
    if not _flush_hook_registered:
        atexit.register(_flush_tracer_provider)
        _flush_hook_registered = True
    logger.info("enable_tracing: Azure Monitor + OpenTelemetry tracing enabled")
    return True
