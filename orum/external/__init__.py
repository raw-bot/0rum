"""External-signal ingestion for the `tradingview_external` mode.

Mode `native` keeps 0rum as the sole signal source (the DSL evaluator). This
package covers the OTHER, mutually-exclusive mode: TradingView (or any external
source) emits *candidate* signals that 0rum must validate before acting on.

Phase 1 scope = TYPES + STRICT PARSING only. Nothing here places an order,
reads risk gates, or touches the native DSL. Business validation (allowlist,
drawdown, kill-switch, position conflict) is Phase 3 and lives elsewhere.
"""

from orum.external.ingest import (
    EXTERNAL_SIGNALS_PATH,
    ExternalSignalStore,
    IngestResult,
)
from orum.external.signal import (
    ExternalSignal,
    ExternalSignalError,
    ExternalSignalEvent,
    ExternalSignalSource,
    ExternalSignalStatus,
    parse_external_signal,
)
from orum.external.orchestrator import (
    ExternalOrchestrator,
    OrchestratorOutcome,
    StateAccess,
    signal_source,
)
from orum.external.poller import (
    MirrorPoller,
    PollResult,
    extract_mirror_payload,
)
from orum.external.state import LiveStateAccess
from orum.external.validate import (
    ValidationContext,
    ValidationResult,
    timeframe_to_ms,
    validate_external_signal,
)

__all__ = [
    "EXTERNAL_SIGNALS_PATH",
    "ExternalOrchestrator",
    "ExternalSignal",
    "ExternalSignalError",
    "ExternalSignalEvent",
    "ExternalSignalSource",
    "ExternalSignalStatus",
    "ExternalSignalStore",
    "IngestResult",
    "LiveStateAccess",
    "MirrorPoller",
    "OrchestratorOutcome",
    "PollResult",
    "StateAccess",
    "extract_mirror_payload",
    "ValidationContext",
    "ValidationResult",
    "parse_external_signal",
    "signal_source",
    "timeframe_to_ms",
    "validate_external_signal",
]
