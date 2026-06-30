"""Registry: loads a `StrategyEngine` by name from `goal.yaml`'s
`strategy_engine` block. Not called from `loop.py` yet -- the cutover (done
in shadow mode first) is a later commit.

    strategy_engine:
      name: ak_macd                     # required; checked against the
                                         # built-in table first
      module: orum.strategies.ak_macd   # optional; only consulted when
                                         # `name` isn't built in, so a future
                                         # engine can ship without touching
                                         # this file
      params: {...}                     # passed verbatim to engine.init()

A missing or unknown engine is a hard `StrategyEngineError`, never a silent
fallback to a different strategy than the operator configured -- the same
discipline `dsl/schema.py` and `external/signal.py` already apply to bad
config.
"""

from __future__ import annotations

import importlib

from orum.strategies.ak_macd import AkMacdEngine
from orum.strategies.base import StrategyEngine
from orum.strategies.dummy import DummyEngine
from orum.strategies.native_dsl import NativeDslEngine

_ENGINES: dict[str, type] = {
    "native_dsl": NativeDslEngine,
    "ak_macd": AkMacdEngine,
    "dummy": DummyEngine,
}


class StrategyEngineError(ValueError):
    pass


def register_engine(name: str, engine_cls: type) -> None:
    """Adds/overrides a built-in engine entry. Used by tests and, later, by
    a plugin that wants a short name instead of a full module path."""
    _ENGINES[name] = engine_cls


def _resolve_class(name: str, module_path: str | None) -> type:
    if name in _ENGINES:
        return _ENGINES[name]
    if not module_path:
        raise StrategyEngineError(
            f"unknown strategy engine {name!r}: not built in, and no `module` given to import it from"
        )
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise StrategyEngineError(f"strategy engine {name!r}: cannot import module {module_path!r}: {exc}") from exc
    engine_cls = getattr(module, "ENGINE_CLASS", None)
    if engine_cls is None:
        raise StrategyEngineError(f"strategy engine {name!r}: module {module_path!r} has no ENGINE_CLASS attribute")
    return engine_cls


def load_engine(goal: dict) -> StrategyEngine:
    """Builds and initializes the engine named in `goal['strategy_engine']`."""
    config = goal.get("strategy_engine")
    if not isinstance(config, dict) or not config.get("name"):
        raise StrategyEngineError("goal.yaml is missing a strategy_engine.name entry")
    name = config["name"]
    engine_cls = _resolve_class(name, config.get("module"))
    engine = engine_cls()
    engine.init(config.get("params") or {})
    return engine
