"""Phase 5: MCP polling transport — TradingView -> Hermes, no webhook.

The free plan cannot POST outbound webhooks, so instead of TradingView pushing
to us, Hermes PULLS: it reads the Hermes Mirror's debug table over the MCP
bridge (``data_get_pine_tables``), extracts the JSON payload cell, and feeds it
to the orchestrator. Dedup by bar_time is already handled by the store, so
polling the same bar twice is harmless.

The actual MCP/CDP call is injected as a ``reader`` callable returning the
``data_get_pine_tables`` response. That keeps this layer pure and testable on
simulated responses, and lets the live binding (agent-driven MCP call, or a
future direct-CDP reader) sit at the edge. Nothing here decides or executes —
it only transports a candidate into the pipeline, where Hermes judges it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from hermes_trading.events import log_event
from hermes_trading.external.orchestrator import ExternalOrchestrator, OrchestratorOutcome

# What the Mirror's table cell holds before any signal has fired.
_NO_SIGNAL = {"", "none"}


def extract_mirror_payload(
    response: dict, *, study_name: str = "Hermes Mirror", payload_key: str = "payload"
) -> str | None:
    """Pull the payload JSON string from a data_get_pine_tables response.

    Returns the JSON string, or ``None`` if there is no readable payload row
    (no matching study / unreadable response). The literal ``"none"`` is
    returned verbatim when the Mirror has emitted no signal yet, so the caller
    can tell "nothing to do" apart from "can't read".

    Rows arrive as ``"key | value"`` strings. The payload JSON itself contains
    ``|`` (inside sig_key), so we split on the FIRST separator only.
    """
    if not isinstance(response, dict) or not response.get("success", True):
        return None
    for study in response.get("studies", []):
        if study_name.lower() not in str(study.get("name", "")).lower():
            continue
        for table in study.get("tables", []):
            for row in table.get("rows", []):
                key, sep, value = str(row).partition("|")
                if sep and key.strip() == payload_key:
                    return value.strip()
    return None


@dataclass(frozen=True)
class PollResult:
    action: str  # "handled" | "no_signal" | "no_table"
    outcome: OrchestratorOutcome | None
    payload: str | None = None


class MirrorPoller:
    """Reads the Mirror table once and routes any payload to the orchestrator."""

    def __init__(
        self,
        *,
        orchestrator: ExternalOrchestrator,
        reader: Callable[[], dict],
        study_name: str = "Hermes Mirror",
        logger=log_event,
    ):
        self.orchestrator = orchestrator
        self.reader = reader
        self.study_name = study_name
        self._log = logger

    def poll_once(self) -> PollResult:
        response = self.reader()
        raw = extract_mirror_payload(response, study_name=self.study_name)
        if raw is None:
            self._log("external_poll_no_table", f"no readable {self.study_name} payload")
            return PollResult("no_table", None)
        if raw in _NO_SIGNAL:
            return PollResult("no_signal", None, raw)
        outcome = self.orchestrator.handle(raw)
        return PollResult("handled", outcome, raw)
