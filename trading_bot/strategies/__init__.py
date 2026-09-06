"""
Strategy registry.

Every tradeable strategy lives in this folder as one module plus its markdown
spec, and registers itself here. The dashboard sidebar, the CLI backtester and
the live engine all pick from :data:`REGISTRY` - adding a strategy means adding a
module and one line below, nothing else.
"""

from typing import Dict, List

from trading_bot.strategies.base import (
    BaseStrategy,
    Evaluation,
    ParamSpec,
    StepResult,
    build_htf_series,
)
from trading_bot.strategies.crt_tbs import CRTParams, CRTTurtleSoupStrategy
from trading_bot.strategies.crt_body_soup import BodySoupParams, CRTBodySoupStrategy
from trading_bot.strategies.vwap_ema_scalper import VwapEmaScalperStrategy

DEFAULT_STRATEGY_KEY = "vwap_ema_scalper"

# Strategies offered in the dashboard sidebar, in order.
#
# The original wick-sweep CRT (``CRTTurtleSoupStrategy``) is deliberately NOT
# listed: it was backtested at profit factor 0.72-0.88 across every window and
# variant tried, and keeping a known-losing strategy one click away from a live
# account is a hazard, not an option. The class is still importable so its
# backtests stay reproducible - see docs/MT5_BACKTEST_GUIDE.md.
REGISTRY: Dict[str, BaseStrategy] = {
    s.key: s for s in (
        VwapEmaScalperStrategy(),
        CRTBodySoupStrategy(),
    )
}

# Importable by key for research/backtests, but never shown in the UI.
ARCHIVED: Dict[str, BaseStrategy] = {s.key: s for s in (CRTTurtleSoupStrategy(),)}


def get_strategy(key: str) -> BaseStrategy:
    """Look a strategy up by key, falling back to the scalper for unknown keys.

    Archived strategies resolve too, so old backtest scripts and any saved
    ``active_strategy`` setting keep working - they just are not offered in the UI.
    """
    if key in REGISTRY:
        return REGISTRY[key]
    if key in ARCHIVED:
        return ARCHIVED[key]
    return REGISTRY[DEFAULT_STRATEGY_KEY]


def list_strategies() -> List[BaseStrategy]:
    """Registry order, which is also the order the sidebar offers them in."""
    return list(REGISTRY.values())


__all__ = [
    "BaseStrategy", "Evaluation", "ParamSpec", "StepResult", "build_htf_series",
    "CRTParams", "CRTTurtleSoupStrategy", "BodySoupParams", "CRTBodySoupStrategy",
    "VwapEmaScalperStrategy",
    "REGISTRY", "ARCHIVED", "DEFAULT_STRATEGY_KEY", "get_strategy", "list_strategies",
]
