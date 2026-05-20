"""In-process pub-sub surface for cross-phase alert delivery.

Phase 6 publishes CircuitBreakerAlert via _publish_alert() whenever the circuit
breaker trips (CONTEXT D-13). Startup wiring may append alert adapters to
_alert_hooks at application startup (src/main.py), never from a request path.

No external notification import lives here — Phase 6 emits only. Monitoring
adapters own delivery. This split keeps Phase 6 free of notification-layer
dependencies.

Trust model (T-06-02-04): register_alert_hook is a plain Python function with no
auth gate. Registration is expected only from src/main.py at startup. ASVS L1
does not require runtime hook authentication for in-process pub-sub.

Failure isolation (T-06-02-01 / T-06-02-03): a failing hook is logged via
risk.alert_hook.failed and the iteration continues to subsequent hooks.
A buggy hook MUST NOT suppress alerts to other hooks or crash the breaker.
"""

from typing import Awaitable, Callable

import structlog

from src.risk.events import CircuitBreakerAlert

log = structlog.get_logger(__name__)

BreakerAlertHook = Callable[[CircuitBreakerAlert], Awaitable[None]]
_alert_hooks: list[BreakerAlertHook] = []


def register_alert_hook(hook: BreakerAlertHook) -> None:
    """Append a hook to the alert subscriber list.

    Startup wiring calls this to register alert delivery adapters.
    Multiple hooks may be registered; they fire in registration order.
    Hook failures are isolated — a failing hook does not prevent others from
    receiving the alert.
    """
    _alert_hooks.append(hook)


async def _publish_alert(alert: CircuitBreakerAlert) -> None:
    """Fan-out alert to all registered hooks.

    Failure isolation: any hook raising an exception is logged via
    risk.alert_hook.failed and iteration continues. Per T-06-02-03, every
    registered hook is always visited regardless of upstream failures.
    """
    for hook in _alert_hooks:
        try:
            await hook(alert)
        except Exception as exc:  # noqa: BLE001 — hook failures must not break the runner
            log.error("risk.alert_hook.failed", error=str(exc))
