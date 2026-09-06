"""
Shared contract every strategy in this package implements.

The bot feeds one thing everywhere - a stream of M1 XAU/USD bars from MT5 - and
each strategy decides for itself what to do with them. A scalper reads every M1
bar; a swing strategy aggregates them into M5/H1 candles internally and only
speaks on a higher-timeframe close. That keeps the live engine, the dashboard and
the backtester identical across strategies: they hand over bars and an index, and
get back an :class:`Evaluation` per direction.

Causality rule (non-negotiable, same as the rest of the codebase): an evaluation
at bar ``i`` may only read bars ``0..i``. Signals fill at bar ``i+1`` open.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """One line of the entry checklist, rendered as-is by the dashboard."""
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Evaluation:
    """A strategy's verdict for one direction at one bar."""
    direction: str                      # "LONG" or "SHORT"
    timestamp: str
    bar_index: int
    close_price: float

    steps: List[StepResult] = field(default_factory=list)
    all_passed: bool = False
    signal: Optional[str] = None        # "BUY" / "SELL" / None

    suggested_entry: float = 0.0
    suggested_sl: float = 0.0
    suggested_tp: float = 0.0           # final target (TP2 where a strategy has two)
    suggested_tp1: Optional[float] = None  # partial / runner-shield target
    risk_points: float = 0.0
    reward_points: float = 0.0
    pattern_name: str = ""

    # Free-form extras for charting / logging (CRT levels, VWAP value, ...).
    context: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed_count(self) -> int:
        return sum(1 for s in self.steps if s.passed)

    @property
    def step_count(self) -> int:
        return len(self.steps)


@dataclass
class ParamSpec:
    """Declarative description of one tunable, so the sidebar can build itself."""
    key: str
    label: str
    kind: str                    # "number" | "slider" | "select" | "toggle"
    default: Any
    min_value: Any = None
    max_value: Any = None
    step: Any = None
    options: Optional[List[Any]] = None
    help: str = ""


class BaseStrategy:
    """Interface implemented by every strategy in this package."""

    key: str = ""
    label: str = ""
    description: str = ""
    timeframe: str = "M1"        # execution timeframe, for display
    doc_file: str = ""           # markdown spec living next to the code

    # -- parameters ---------------------------------------------------------
    def param_specs(self) -> List[ParamSpec]:
        raise NotImplementedError

    def default_params(self) -> Any:
        return self.build_params({s.key: s.default for s in self.param_specs()})

    def build_params(self, values: Dict[str, Any]) -> Any:
        """Turn a flat dict of sidebar/CLI values into the strategy's params object."""
        raise NotImplementedError

    # -- evaluation ---------------------------------------------------------
    def warmup_bars(self, params: Any) -> int:
        """Bars to skip at the start of a dataset before signals are trustworthy."""
        return 50

    def prepare(self, data: Dict[str, List], params: Any) -> Dict[str, Any]:
        """Compute anything derivable once per dataset (indicators, HTF candles).

        The backtester calls this once and passes the result to every
        :meth:`evaluate` call, so an O(n) aggregation never becomes O(n^2).
        """
        return {}

    def evaluate(self, data: Dict[str, List], idx: int, params: Any,
                 ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Evaluation]:
        """Return {"LONG": Evaluation, "SHORT": Evaluation} at bar ``idx``."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Timeframe aggregation - used by any strategy whose logic is not M1
# ---------------------------------------------------------------------------

def to_datetime(t: Any) -> Optional[datetime]:
    """Best-effort parse of the several time shapes this codebase carries around."""
    if isinstance(t, datetime):
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(float(t), tz=timezone.utc)
    if isinstance(t, str) and t:
        s = t.strip().replace("Z", "+00:00")
        for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                    "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M"):
            try:
                dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


@dataclass
class HTFCandle:
    """One aggregated higher-timeframe candle built from M1 bars."""
    bucket: int          # floor(epoch_minutes / tf_minutes)
    open: float
    high: float
    low: float
    close: float
    volume: float
    first_idx: int       # M1 index of the first bar in this candle
    last_idx: int        # M1 index of the last bar seen so far in this candle
    start_time: str

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def range_size(self) -> float:
        return self.high - self.low


@dataclass
class HTFSeries:
    """M1 bars folded into a higher timeframe, with M1 -> candle back-references."""
    tf_minutes: int
    candles: List[HTFCandle]
    candle_of_bar: List[int]     # per M1 bar: index into `candles` (-1 if untimed)
    is_bucket_close: List[bool]  # per M1 bar: True when the clock closes its candle

    def previous_completed(self, bar_idx: int,
                           max_gap_buckets: Optional[int] = None) -> Optional[HTFCandle]:
        """The last fully-closed HTF candle as known at M1 bar ``bar_idx``.

        Causal by construction: it is the candle *before* the one bar ``bar_idx``
        lives in, so every bar that built it precedes ``bar_idx``.

        ``max_gap_buckets`` rejects a reference candle separated from the current
        one by more than that many buckets. Without it a hole in the data - a
        holiday, or a month missing from a downloaded set - silently hands back a
        candle from weeks ago, and the setup gets priced off a level the market
        left long before. A normal weekend sits well inside the default.
        """
        if bar_idx >= len(self.candle_of_bar):
            return None
        ci = self.candle_of_bar[bar_idx]
        if ci <= 0:
            return None
        candidate = self.candles[ci - 1]
        if max_gap_buckets is not None and \
                (self.candles[ci].bucket - candidate.bucket) > max_gap_buckets:
            return None
        return candidate

    def current(self, bar_idx: int) -> Optional[HTFCandle]:
        """The forming candle bar ``bar_idx`` belongs to (partial, up to this bar)."""
        if bar_idx >= len(self.candle_of_bar):
            return None
        ci = self.candle_of_bar[bar_idx]
        return self.candles[ci] if ci >= 0 else None


def build_htf_series(data: Dict[str, List], tf_minutes: int) -> HTFSeries:
    """Fold M1 bars into ``tf_minutes`` candles.

    Buckets come from the wall clock (``epoch_minutes // tf_minutes``), never from
    neighbouring bars, so a candle's boundaries are knowable at the moment its
    last bar closes and a weekend gap simply produces no candle instead of a
    stitched-together one.
    """
    times = data["times"]
    opens, highs, lows = data["opens"], data["highs"], data["lows"]
    closes, volumes = data["closes"], data.get("volumes") or []
    n = len(closes)

    candles: List[HTFCandle] = []
    candle_of_bar = [-1] * n
    is_bucket_close = [False] * n

    for i in range(n):
        dt = to_datetime(times[i]) if i < len(times) else None
        if dt is None:
            # Untimed bar: keep it attached to whatever candle is open so the
            # series never develops holes, but never let it close a bucket.
            candle_of_bar[i] = len(candles) - 1
            continue

        epoch_min = int(dt.timestamp() // 60)
        bucket = epoch_min // tf_minutes
        vol = float(volumes[i]) if i < len(volumes) and volumes[i] is not None else 0.0

        if not candles or candles[-1].bucket != bucket:
            candles.append(HTFCandle(
                bucket=bucket, open=opens[i], high=highs[i], low=lows[i],
                close=closes[i], volume=vol, first_idx=i, last_idx=i,
                start_time=dt.isoformat(),
            ))
        else:
            c = candles[-1]
            c.high = max(c.high, highs[i])
            c.low = min(c.low, lows[i])
            c.close = closes[i]
            c.volume += vol
            c.last_idx = i

        candle_of_bar[i] = len(candles) - 1
        is_bucket_close[i] = (epoch_min % tf_minutes) == (tf_minutes - 1)

    return HTFSeries(
        tf_minutes=tf_minutes,
        candles=candles,
        candle_of_bar=candle_of_bar,
        is_bucket_close=is_bucket_close,
    )
