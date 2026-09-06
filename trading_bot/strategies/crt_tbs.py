"""
CRT + TBS - Candle Range Theory + Turtle Soup (XAUUSD).

Spec: ``CRT_TBS_Strategy_XAUUSD.md`` next to this file.

Where the scalper hunts many small M1 moves, this one waits for the market to run
a higher-timeframe candle's high or low, fail there, and snap back inside. Few
setups, wide targets, trades that live for hours rather than minutes.

How it maps onto an M1 feed
---------------------------
The engine, the dashboard and the backtester all speak M1. This strategy folds
those bars into two clocks of its own:

* **Reference clock** (H1 by default) - the last *completed* candle defines the
  CRT range: its High is buy-side liquidity, its Low is sell-side liquidity, its
  midpoint is equilibrium.
* **Execution clock** (M5 by default) - the Turtle Soup trigger is only ever
  judged on a *closed* execution candle.

So a signal can only appear on an M1 bar that closes an execution candle, and it
fills at the next M1 open - which is the open of the next execution candle,
exactly the "open of the immediate next candle" the spec asks for.

Every read is causal: the reference candle is the one *before* the bucket the
current bar sits in, so every bar that shaped it is already in the past.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from trading_bot.strategies.base import (
    BaseStrategy,
    Evaluation,
    HTFCandle,
    ParamSpec,
    StepResult,
    build_htf_series,
    to_datetime,
)

TF_MINUTES = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}


@dataclass
class CRTParams:
    """Every tunable of the CRT + TBS setup. No magic numbers in the logic below."""

    # 1. Timeframes
    reference_tf: str = "H1"          # candle whose High/Low defines the CRT range
    execution_tf: str = "M5"          # candle the Turtle Soup trigger is judged on

    # 2. Sweep / Turtle Soup trigger
    sweep_lookback_candles: int = 2    # execution candles the sweep may have happened in
    min_sweep_points: float = 0.30     # how far past the level price must poke ($ on Gold)
    max_sweep_points: float = 12.0     # a run this deep is displacement, not a sweep
    require_close_inside: bool = True  # trigger candle must close back inside the range
    reentry_buffer_points: float = 0.0 # extra $ inside the level the close must reclaim

    # 3. Range quality
    min_range_points: float = 3.0      # skip dead reference candles
    max_range_points: float = 60.0     # skip news-bar ranges with an unreachable TP2

    # 4. Risk
    sl_buffer_points: float = 1.20     # beyond the sweep wick (10-15 "pips" on Gold)
    min_sl_distance_points: float = 1.50
    max_sl_distance_points: float = 12.0
    min_rr_tp2: float = 1.20           # reject setups whose TP2 does not pay for the stop
    use_tp1_partial: bool = True       # half off at equilibrium, remainder to break-even

    # 5. Session / news filters
    enable_killzone_filter: bool = True
    london_start_utc: str = "07:00"
    london_end_utc: str = "10:00"
    ny_start_utc: str = "12:30"
    ny_end_utc: str = "16:00"
    news_blackout_utc: str = ""        # e.g. "12:30,14:00" - blocks +/- the window below
    news_blackout_minutes: int = 30

    # 6. Data hygiene
    # Longest hole (in reference-candle slots) still allowed between the CRT
    # candle and now. A weekend is ~2 slots on H4 and ~65 on H1; anything much
    # larger means missing data, and a level from before it is meaningless.
    max_reference_gap_slots: int = 80


def _hhmm_to_minutes(value: str, fallback: int = 0) -> int:
    try:
        hh, mm = value.strip().split(":")
        return int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        return fallback


def in_killzone(minute_of_day: int, params: CRTParams) -> Tuple[bool, str]:
    """London / New York kill zones from the spec, as UTC minute-of-day windows."""
    lon_a = _hhmm_to_minutes(params.london_start_utc, 420)
    lon_b = _hhmm_to_minutes(params.london_end_utc, 600)
    ny_a = _hhmm_to_minutes(params.ny_start_utc, 750)
    ny_b = _hhmm_to_minutes(params.ny_end_utc, 960)

    if lon_a <= minute_of_day <= lon_b:
        return True, "London killzone"
    if ny_a <= minute_of_day <= ny_b:
        return True, "New York killzone"
    return False, "Outside London / NY killzones"


def in_news_blackout(minute_of_day: int, params: CRTParams) -> Tuple[bool, str]:
    """Static blackout around user-listed release times (CPI / NFP / FOMC)."""
    raw = (params.news_blackout_utc or "").strip()
    if not raw:
        return False, ""
    for token in raw.replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        centre = _hhmm_to_minutes(token, -10_000)
        if centre < 0:
            continue
        if abs(minute_of_day - centre) <= params.news_blackout_minutes:
            return True, f"news blackout +/-{params.news_blackout_minutes}m around {token} UTC"
    return False, ""


class CRTTurtleSoupStrategy(BaseStrategy):
    """Liquidity sweep of a completed H1/H4 candle, faded back into its range."""

    key = "crt_tbs"
    label = "CRT + TBS Liquidity Sweep"
    description = (
        "Sweep-and-reverse swing setup. Marks the last completed H1/H4 candle as the "
        "CRT range, waits for price to run its High or Low and close back inside on "
        "M5/M15, then targets equilibrium (TP1) and the opposite side (TP2). Few "
        "trades, long holds, wide targets."
    )
    timeframe = "M5 exec / H1 range"
    doc_file = "CRT_TBS_Strategy_XAUUSD.md"

    # -- parameters ---------------------------------------------------------
    def param_specs(self) -> List[ParamSpec]:
        d = CRTParams()
        return [
            ParamSpec("reference_tf", "CRT Reference Candle", "select", d.reference_tf,
                      options=["H1", "H4"],
                      help="Timeframe whose last completed candle defines the range."),
            ParamSpec("execution_tf", "Execution Candle", "select", d.execution_tf,
                      options=["M5", "M15"],
                      help="Timeframe the Turtle Soup re-entry close is judged on."),
            ParamSpec("sweep_lookback_candles", "Sweep Lookback (exec candles)", "number",
                      d.sweep_lookback_candles, 1, 6, 1,
                      help="How recently the wick through the level may have happened."),
            ParamSpec("min_sweep_points", "Min Sweep Depth ($)", "slider",
                      d.min_sweep_points, 0.0, 3.0, 0.05,
                      help="Price must poke at least this far past the CRT level."),
            ParamSpec("max_sweep_points", "Max Sweep Depth ($)", "slider",
                      d.max_sweep_points, 2.0, 40.0, 0.5,
                      help="Deeper than this is a real break, not a liquidity grab."),
            ParamSpec("min_range_points", "Min CRT Range ($)", "slider",
                      d.min_range_points, 1.0, 20.0, 0.5),
            ParamSpec("max_range_points", "Max CRT Range ($)", "slider",
                      d.max_range_points, 20.0, 150.0, 5.0),
            ParamSpec("sl_buffer_points", "SL Buffer past Wick ($)", "slider",
                      d.sl_buffer_points, 0.2, 4.0, 0.1,
                      help="10-15 pips on Gold is roughly $1.00-$1.50."),
            ParamSpec("min_sl_distance_points", "Min SL Distance ($)", "slider",
                      d.min_sl_distance_points, 0.5, 6.0, 0.1),
            ParamSpec("max_sl_distance_points", "Max SL Distance ($)", "slider",
                      d.max_sl_distance_points, 3.0, 30.0, 0.5),
            ParamSpec("min_rr_tp2", "Min R:R to TP2", "slider",
                      d.min_rr_tp2, 0.5, 5.0, 0.1),
            ParamSpec("use_tp1_partial", "Half off at Equilibrium (TP1)", "toggle",
                      d.use_tp1_partial,
                      help="Close half at the CRT midpoint and move the rest to break-even."),
            ParamSpec("enable_killzone_filter", "London / NY Killzones Only", "toggle",
                      d.enable_killzone_filter),
            ParamSpec("news_blackout_utc", "News Blackout Times (UTC)", "text",
                      d.news_blackout_utc,
                      help="Comma-separated HH:MM, e.g. 12:30,14:00 for CPI / NFP / FOMC."),
            ParamSpec("news_blackout_minutes", "News Blackout Window (min)", "number",
                      d.news_blackout_minutes, 0, 120, 5),
        ]

    def build_params(self, values: Dict[str, Any]) -> CRTParams:
        defaults = CRTParams()
        kwargs = {}
        for spec in self.param_specs():
            kwargs[spec.key] = values.get(spec.key, getattr(defaults, spec.key))
        # Not surfaced in the sidebar, but still overridable programmatically.
        for hidden in ("require_close_inside", "reentry_buffer_points",
                       "max_reference_gap_slots"):
            kwargs[hidden] = values.get(hidden, getattr(defaults, hidden))
        return CRTParams(**kwargs)

    # -- evaluation ---------------------------------------------------------
    def warmup_bars(self, params: CRTParams) -> int:
        ref_min = TF_MINUTES.get(params.reference_tf, 60)
        exec_min = TF_MINUTES.get(params.execution_tf, 5)
        # One full reference candle plus the sweep lookback window.
        return ref_min * 2 + exec_min * (params.sweep_lookback_candles + 1)

    def prepare(self, data: Dict[str, List], params: CRTParams) -> Dict[str, Any]:
        return {
            "ref": build_htf_series(data, TF_MINUTES.get(params.reference_tf, 60)),
            "exec": build_htf_series(data, TF_MINUTES.get(params.execution_tf, 5)),
        }

    def evaluate(self, data: Dict[str, List], idx: int, params: CRTParams,
                 ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Evaluation]:
        ctx = ctx or self.prepare(data, params)
        ref_series = ctx["ref"]
        exec_series = ctx["exec"]

        close = data["closes"][idx]
        t_raw = data["times"][idx] if idx < len(data["times"]) else str(idx)
        stamp = t_raw if isinstance(t_raw, str) else str(t_raw)
        dt = to_datetime(t_raw)
        minute_of_day = (dt.hour * 60 + dt.minute) if dt else 0

        crt = ref_series.previous_completed(idx, params.max_reference_gap_slots)
        exec_candle = exec_series.current(idx)
        bar_closes_exec = idx < len(exec_series.is_bucket_close) and exec_series.is_bucket_close[idx]

        # Shared gates, evaluated once and reported on both sides of the book.
        gates = self._shared_gates(crt, exec_candle, bar_closes_exec, minute_of_day, params)

        return {
            "LONG": self._evaluate_side("LONG", data, idx, params, ctx, crt, exec_candle,
                                        bar_closes_exec, gates, stamp, close),
            "SHORT": self._evaluate_side("SHORT", data, idx, params, ctx, crt, exec_candle,
                                         bar_closes_exec, gates, stamp, close),
        }

    # -- internals ----------------------------------------------------------
    def _shared_gates(self, crt: Optional[HTFCandle], exec_candle: Optional[HTFCandle],
                      bar_closes_exec: bool, minute_of_day: int,
                      params: CRTParams) -> List[StepResult]:
        """Checks that do not depend on direction: range, exec close, session."""
        if crt is None:
            crt_step = StepResult(f"CRT range ({params.reference_tf})", False,
                                  "No usable reference candle (still warming up, or a "
                                  "gap in the data makes the last one stale)")
        elif crt.range_size < params.min_range_points:
            crt_step = StepResult(f"CRT range ({params.reference_tf})", False,
                                  f"Range ${crt.range_size:.2f} below ${params.min_range_points:.2f} minimum")
        elif crt.range_size > params.max_range_points:
            crt_step = StepResult(f"CRT range ({params.reference_tf})", False,
                                  f"Range ${crt.range_size:.2f} above ${params.max_range_points:.2f} maximum")
        else:
            crt_step = StepResult(
                f"CRT range ({params.reference_tf})", True,
                f"High ${crt.high:.2f} / EQ ${crt.midpoint:.2f} / Low ${crt.low:.2f} "
                f"(${crt.range_size:.2f} wide)")

        if bar_closes_exec and exec_candle is not None:
            exec_step = StepResult(f"{params.execution_tf} candle closed", True,
                                   f"Closed at ${exec_candle.close:.2f}")
        else:
            exec_step = StepResult(f"{params.execution_tf} candle closed", False,
                                   f"Mid-candle - the trigger is only judged on a closed "
                                   f"{params.execution_tf}")

        if params.enable_killzone_filter:
            ok, why = in_killzone(minute_of_day, params)
            session_step = StepResult("Killzone / news window", ok, why)
            if ok:
                blocked, reason = in_news_blackout(minute_of_day, params)
                if blocked:
                    session_step = StepResult("Killzone / news window", False, reason)
        else:
            blocked, reason = in_news_blackout(minute_of_day, params)
            session_step = StepResult("Killzone / news window", not blocked,
                                      reason or "Session filter off")

        return [crt_step, exec_step, session_step]

    def _evaluate_side(self, direction: str, data: Dict[str, List], idx: int,
                       params: CRTParams, ctx: Dict[str, Any],
                       crt: Optional[HTFCandle], exec_candle: Optional[HTFCandle],
                       bar_closes_exec: bool, gates: List[StepResult],
                       stamp: str, close: float) -> Evaluation:
        steps = list(gates)
        gates_ok = all(s.passed for s in gates)

        sweep_ok = False
        reentry_ok = False
        sweep_extreme = 0.0
        sl = tp1 = tp2 = 0.0
        risk = reward = 0.0
        pattern = ""

        if not gates_ok or crt is None or exec_candle is None:
            steps.append(StepResult("Liquidity sweep (TBS)", False, "Waiting on the checks above"))
            steps.append(StepResult("Close back inside range", False, "Waiting on the checks above"))
            steps.append(StepResult("Risk:Reward to TP2", False, "Waiting on the checks above"))
            return self._make_eval(direction, stamp, idx, close, steps, 0.0, 0.0, None,
                                   0.0, 0.0, "", crt)

        level = crt.high if direction == "SHORT" else crt.low
        window = self._sweep_window(ctx["exec"], idx, params.sweep_lookback_candles)

        if direction == "SHORT":
            sweep_extreme = max(c.high for c in window)
            depth = sweep_extreme - level
            consolidated_outside = any(
                c.close > level for c in window if c.last_idx != exec_candle.last_idx)
        else:
            sweep_extreme = min(c.low for c in window)
            depth = level - sweep_extreme
            consolidated_outside = any(
                c.close < level for c in window if c.last_idx != exec_candle.last_idx)

        # 4. Liquidity sweep: a wick through the level, deep enough to be a stop
        #    run but not so deep it is a genuine break of the range.
        if depth < params.min_sweep_points:
            sweep_detail = (f"No sweep - extreme ${sweep_extreme:.2f} only ${max(depth, 0):.2f} "
                            f"past the CRT {'High' if direction == 'SHORT' else 'Low'} "
                            f"${level:.2f} (need ${params.min_sweep_points:.2f})")
        elif depth > params.max_sweep_points:
            sweep_detail = (f"Ran ${depth:.2f} past ${level:.2f} - displacement, not a sweep "
                            f"(max ${params.max_sweep_points:.2f})")
        elif consolidated_outside:
            # Spec invalidation: a candle closed and settled outside the boundary.
            sweep_detail = (f"Invalidated - an earlier {params.execution_tf} candle closed "
                            f"outside ${level:.2f} (consolidation, not a fakeout)")
        else:
            sweep_ok = True
            side = "BSL above" if direction == "SHORT" else "SSL below"
            sweep_detail = (f"Swept {side} ${level:.2f} to ${sweep_extreme:.2f} "
                            f"(${depth:.2f} deep)")
        steps.append(StepResult("Liquidity sweep (TBS)", sweep_ok, sweep_detail))

        # 5. Turtle Soup confirmation: this candle rejects and closes back inside.
        buf = params.reentry_buffer_points
        if direction == "SHORT":
            back_inside = exec_candle.close < (level - buf)
            reentry_detail = (f"{params.execution_tf} closed ${exec_candle.close:.2f} back inside "
                              f"below ${level:.2f}") if back_inside else (
                              f"{params.execution_tf} closed ${exec_candle.close:.2f} - still at or "
                              f"above the CRT High ${level:.2f}")
        else:
            back_inside = exec_candle.close > (level + buf)
            reentry_detail = (f"{params.execution_tf} closed ${exec_candle.close:.2f} back inside "
                              f"above ${level:.2f}") if back_inside else (
                              f"{params.execution_tf} closed ${exec_candle.close:.2f} - still at or "
                              f"below the CRT Low ${level:.2f}")
        reentry_ok = back_inside if params.require_close_inside else True
        steps.append(StepResult("Close back inside range", reentry_ok, reentry_detail))

        # 6. Levels + R:R. Entry is next bar's open; price the setup off this close.
        entry_ref = exec_candle.close
        if direction == "SHORT":
            raw_sl = sweep_extreme + params.sl_buffer_points
            sl = self._clamp_sl(entry_ref, raw_sl, direction, params)
            tp1 = crt.midpoint
            tp2 = crt.low
            risk = sl - entry_ref
            reward = entry_ref - tp2
            pattern = "Turtle Soup - BSL sweep rejection"
        else:
            raw_sl = sweep_extreme - params.sl_buffer_points
            sl = self._clamp_sl(entry_ref, raw_sl, direction, params)
            tp1 = crt.midpoint
            tp2 = crt.high
            risk = entry_ref - sl
            reward = tp2 - entry_ref
            pattern = "Turtle Soup - SSL sweep rejection"

        rr = (reward / risk) if risk > 0 else 0.0
        rr_ok = risk > 0 and reward > 0 and rr >= params.min_rr_tp2
        if reward <= 0:
            rr_detail = (f"TP2 ${tp2:.2f} is already through price ${entry_ref:.2f} - "
                         "the range is spent")
        else:
            rr_detail = (f"Risk ${risk:.2f} to SL ${sl:.2f}, reward ${reward:.2f} to TP2 "
                         f"${tp2:.2f} = {rr:.2f}R (need {params.min_rr_tp2:.2f}R)")
        steps.append(StepResult("Risk:Reward to TP2", rr_ok, rr_detail))

        return self._make_eval(direction, stamp, idx, close, steps, sl, tp2,
                               tp1 if params.use_tp1_partial else None,
                               round(risk, 2), round(reward, 2), pattern, crt,
                               entry_ref=entry_ref, sweep_extreme=sweep_extreme, rr=rr)

    @staticmethod
    def _sweep_window(exec_series, idx: int, lookback: int) -> List[HTFCandle]:
        """The current execution candle plus the ``lookback - 1`` closed before it."""
        ci = exec_series.candle_of_bar[idx]
        start = max(0, ci - (lookback - 1))
        return exec_series.candles[start:ci + 1]

    @staticmethod
    def _clamp_sl(entry: float, raw_sl: float, direction: str, params: CRTParams) -> float:
        """Keep the stop inside the configured distance band, wick-based otherwise."""
        if direction == "SHORT":
            dist = raw_sl - entry
            dist = min(max(dist, params.min_sl_distance_points), params.max_sl_distance_points)
            return round(entry + dist, 2)
        dist = entry - raw_sl
        dist = min(max(dist, params.min_sl_distance_points), params.max_sl_distance_points)
        return round(entry - dist, 2)

    @staticmethod
    def _make_eval(direction: str, stamp: str, idx: int, close: float,
                   steps: List[StepResult], sl: float, tp: float, tp1: Optional[float],
                   risk: float, reward: float, pattern: str, crt: Optional[HTFCandle],
                   entry_ref: float = 0.0, sweep_extreme: float = 0.0,
                   rr: float = 0.0) -> Evaluation:
        all_passed = bool(steps) and all(s.passed for s in steps)
        return Evaluation(
            direction=direction,
            timestamp=stamp,
            bar_index=idx,
            close_price=close,
            steps=steps,
            all_passed=all_passed,
            signal=("SELL" if direction == "SHORT" else "BUY") if all_passed else None,
            suggested_entry=round(entry_ref or close, 2),
            suggested_sl=round(sl, 2),
            suggested_tp=round(tp, 2),
            suggested_tp1=round(tp1, 2) if tp1 else None,
            risk_points=risk,
            reward_points=reward,
            pattern_name=pattern,
            context={
                "crt_high": round(crt.high, 2) if crt else None,
                "crt_low": round(crt.low, 2) if crt else None,
                "crt_eq": round(crt.midpoint, 2) if crt else None,
                "crt_start": crt.start_time if crt else None,
                "sweep_extreme": round(sweep_extreme, 2) if sweep_extreme else None,
                "rr_tp2": round(rr, 2),
            },
        )


def strategy_doc_path() -> str:
    """Absolute path to the markdown spec shipped beside this module."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        CRTTurtleSoupStrategy.doc_file)
