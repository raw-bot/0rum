"""Strategy engine package.

Exports the AbstractStrategy base class and StrategyRunner coordinator.
Strategy implementations (Plans 02/03) are imported lazily inside StrategyRunner.run()
to avoid circular imports.
"""

from src.strategies.base import AbstractStrategy
from src.strategies.runner import StrategyRunner

__all__ = ["AbstractStrategy", "StrategyRunner"]
