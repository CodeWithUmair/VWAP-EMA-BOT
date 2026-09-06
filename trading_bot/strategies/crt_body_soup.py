"""
CRT + TBS "Body Soup" - Candle Range Theory + Turtle BODY Soup (XAUUSD).

Spec: ``CRT_TBS_Complete_Manual_XAUUSD.md`` next to this file.

This is a distinct, stricter variant of :mod:`trading_bot.strategies.crt_tbs`.
That module fades a WICK sweep that closes back inside the range on the same
candle (the manual here calls that "Turtle Wick Soup" - lower probability).
This one requires two separate candles:

1. **Manipulator candle** - closes its full BODY beyond the CRT boundary
   (a "disrespect", not a wick). This traps the breakout traders who saw the
   body close as strength.
2. **Trigger candle** - a later candle whose close breaks back through the
   manipulator candle's opposite extreme (its low, for a short; its high, for
   a long), confirming the reversal is actually underway.

Only then is a trade taken - entry at the next bar's open, same causal fill
rule as everywhere else in this codebase. The manual calls for the trigger to
land within 1-2 candles of the manipulator and treats 5+ as decayed; that is
``max_break_wait_candles`` here.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from trading_bot.strategies.base import (
    BaseStrategy,
    Evaluation,
    HTFCandle,
    ParamSpec,
    StepResult,
    build_htf_series,
    to_datetime,
)
from trading_bot.strategy import calculate_ema
from trading_bot.strategies.crt_tbs import TF_MINUTES, in_killzone, in_news_blackout


@dataclass
class BodySoupParams:
    """Every tunable of the Body Soup setup. No magic numbers in the logic below."""

    # 1. Timeframes
    reference_tf: str = "H1"          # candle whose High/Low defines the CRT range
    execution_tf: str = "M5"          # candle the manipulator/trigger pair is judged on

    # 2. Manipulator candle (the "guilty party")
    manipulator_lookback: int = 6      # how many exec candles back it may have formed
    min_body_break_points: float = 0.10  # how far the body must close past the level ($)
    max_body_break_points: float = 15.0  # deeper than this is a real breakout, not a trap

    # 3. Trigger (confirmation break of the manipulator's opposite extreme)
    max_break_wait_candles: int = 5    # manual: "ideally 1-2... 5+ decays"
    # "confirmed" (Complete Manual): wait for a later candle to break the
    # manipulator's opposite extreme before entering.
    # "immediate" (Liquidity Purge guide, Model 1): enter at the manipulator
    # candle's own close, one candle earlier and more aggressive.
    entry_mode: str = "confirmed"

    # 4. Range quality
    min_range_points: float = 3.0
    max_range_points: float = 60.0

    # 5. Risk
    sl_buffer_points: float = 1.20     # beyond the manipulator candle's extreme
    min_sl_distance_points: float = 1.50
    max_sl_distance_points: float = 12.0
    min_rr_tp2: float = 1.20
    use_tp1_partial: bool = True       # manual: 50% off at equilibrium, capped by TP2 at the far side
    # Where the final target sits. "opposite_side" is what every CRT manual
    # asks for (the far side of the range). "fixed_r" takes a multiple of the
    # stop instead - far less ambitious, and the backtests say the far side is
    # what these setups keep failing to reach.
    target_mode: str = "opposite_side"
    fixed_r_multiple: float = 2.0

    # 6. Session / news filters
    enable_killzone_filter: bool = True
    london_start_utc: str = "07:00"
    london_end_utc: str = "10:00"
    ny_start_utc: str = "12:30"
    ny_end_utc: str = "16:00"
    news_blackout_utc: str = ""
    news_blackout_minutes: int = 30

    # 7. HTF directional bias
    # Fading a sweep against a strong higher-timeframe trend is the single
    # most expensive habit in a sweep strategy: the "sweep" is often just the
    # trend continuing. With this on, only sweeps that resolve WITH the HTF
    # trend are taken (buy the sweep of a low in an uptrend, and vice versa).
    enable_htf_bias: bool = True
    htf_bias_tf: str = "H4"            # timeframe the trend is measured on
    htf_bias_ema: int = 50             # EMA period on that timeframe

    # 8. Data hygiene (see crt_tbs.py for the rationale)
    max_reference_gap_slots: int = 80


class CRTBodySoupStrategy(BaseStrategy):
    """Two-candle liquidity trap: a body-close break, then a confirmed reversal."""

    key = "crt_body_soup"
    label = "CRT + TBS (Body Soup)"
    description = (
        "Stricter sweep variant. Requires a manipulator candle to close its full "
        "BODY beyond the CRT boundary (not just a wick), then waits for a later "
        "candle to break back through that candle's opposite extreme before "
        "entering. Fewer, later, more selective signals than the wick-sweep variant."
    )
    timeframe = "M5 exec / H1 range"
    doc_file = "CRT_TBS_Complete_Manual_XAUUSD.md"

    def param_specs(self) -> List[ParamSpec]:
        d = BodySoupParams()
        return [
            ParamSpec("reference_tf", "CRT Reference Candle", "select", d.reference_tf,
                      options=["H1", "H4"]),
            ParamSpec("execution_tf", "Execution Candle", "select", d.execution_tf,
                      options=["M5", "M15"]),
            ParamSpec("manipulator_lookback", "Manipulator Lookback (exec candles)", "number",
                      d.manipulator_lookback, 2, 12, 1,
                      help="How far back the body-close-beyond-level candle may have formed."),
            ParamSpec("min_body_break_points", "Min Body Break Depth ($)", "slider",
                      d.min_body_break_points, 0.0, 3.0, 0.05,
                      help="How far the manipulator candle's close must clear the level."),
            ParamSpec("max_body_break_points", "Max Body Break Depth ($)", "slider",
                      d.max_body_break_points, 3.0, 40.0, 0.5,
                      help="Deeper than this is a genuine breakout, not a trap."),
            ParamSpec("max_break_wait_candles", "Max Wait for Trigger (candles)", "number",
                      d.max_break_wait_candles, 1, 10, 1,
                      help="Manual: ideally 1-2 candles; 5+ and the setup has decayed."),
            ParamSpec("min_range_points", "Min CRT Range ($)", "slider",
                      d.min_range_points, 1.0, 20.0, 0.5),
            ParamSpec("max_range_points", "Max CRT Range ($)", "slider",
                      d.max_range_points, 20.0, 150.0, 5.0),
            ParamSpec("sl_buffer_points", "SL Buffer past Manipulator Extreme ($)", "slider",
                      d.sl_buffer_points, 0.2, 4.0, 0.1),
            ParamSpec("min_sl_distance_points", "Min SL Distance ($)", "slider",
                      d.min_sl_distance_points, 0.5, 6.0, 0.1),
            ParamSpec("max_sl_distance_points", "Max SL Distance ($)", "slider",
                      d.max_sl_distance_points, 3.0, 30.0, 0.5),
            ParamSpec("entry_mode", "Entry Timing", "select", d.entry_mode,
                      options=["confirmed", "immediate"],
                      help="confirmed = wait for Candle 3 to break Candle 2's extreme "
                           "(Complete Manual). immediate = enter at Candle 2's close "
                           "(Liquidity Purge Model 1)."),
            ParamSpec("target_mode", "Final Target", "select", d.target_mode,
                      options=["opposite_side", "fixed_r"],
                      help="opposite_side = far side of the CRT range, as the manuals "
                           "specify. fixed_r = a multiple of the stop instead."),
            ParamSpec("fixed_r_multiple", "Fixed Target (R)", "slider",
                      d.fixed_r_multiple, 0.5, 6.0, 0.25,
                      help="Only used when Final Target is fixed_r."),
            ParamSpec("min_rr_tp2", "Min R:R to TP2", "slider", d.min_rr_tp2, 0.5, 5.0, 0.1),
            ParamSpec("use_tp1_partial", "Half off at Equilibrium (TP1)", "toggle",
                      d.use_tp1_partial),
            ParamSpec("enable_htf_bias", "HTF Trend Bias Filter", "toggle",
                      d.enable_htf_bias,
                      help="Only fade sweeps that resolve WITH the higher-timeframe trend."),
            ParamSpec("htf_bias_tf", "Bias Timeframe", "select", d.htf_bias_tf,
                      options=["H1", "H4"]),
            ParamSpec("htf_bias_ema", "Bias EMA Period", "number", d.htf_bias_ema, 10, 200, 5),
            ParamSpec("enable_killzone_filter", "London / NY Killzones Only", "toggle",
                      d.enable_killzone_filter),
            ParamSpec("news_blackout_utc", "News Blackout Times (UTC)", "text",
                      d.news_blackout_utc),
            ParamSpec("news_blackout_minutes", "News Blackout Window (min)", "number",
                      d.news_blackout_minutes, 0, 120, 5),
        ]

    def build_params(self, values: Dict[str, Any]) -> BodySoupParams:
        defaults = BodySoupParams()
        kwargs = {s.key: values.get(s.key, getattr(defaults, s.key)) for s in self.param_specs()}
        kwargs["max_reference_gap_slots"] = values.get(
            "max_reference_gap_slots", defaults.max_reference_gap_slots)
        return BodySoupParams(**kwargs)

    def warmup_bars(self, params: BodySoupParams) -> int:
        ref_min = TF_MINUTES.get(params.reference_tf, 60)
        exec_min = TF_MINUTES.get(params.execution_tf, 5)
        return ref_min * 2 + exec_min * (params.manipulator_lookback + params.max_break_wait_candles + 2)

    def prepare(self, data: Dict[str, List], params: BodySoupParams) -> Dict[str, Any]:
        ctx = {
            "ref": build_htf_series(data, TF_MINUTES.get(params.reference_tf, 60)),
            "exec": build_htf_series(data, TF_MINUTES.get(params.execution_tf, 5)),
        }
        if params.enable_htf_bias:
            bias = build_htf_series(data, TF_MINUTES.get(params.htf_bias_tf, 240))
            closes = [c.close for c in bias.candles]
            ctx["bias"] = bias
            ctx["bias_ema"] = calculate_ema(closes, params.htf_bias_ema) if closes else []
        return ctx

    def evaluate(self, data: Dict[str, List], idx: int, params: BodySoupParams,
                 ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Evaluation]:
        ctx = ctx or self.prepare(data, params)
        ref_series, exec_series = ctx["ref"], ctx["exec"]

        close = data["closes"][idx]
        t_raw = data["times"][idx] if idx < len(data["times"]) else str(idx)
        stamp = t_raw if isinstance(t_raw, str) else str(t_raw)
        dt = to_datetime(t_raw)
        minute_of_day = (dt.hour * 60 + dt.minute) if dt else 0

        crt = ref_series.previous_completed(idx, params.max_reference_gap_slots)
        bar_closes_exec = idx < len(exec_series.is_bucket_close) and exec_series.is_bucket_close[idx]
        gates = self._shared_gates(crt, bar_closes_exec, minute_of_day, params)

        return {
            "LONG": self._evaluate_side("LONG", exec_series, idx, params, crt,
                                        bar_closes_exec, gates, stamp, close, ctx),
            "SHORT": self._evaluate_side("SHORT", exec_series, idx, params, crt,
                                         bar_closes_exec, gates, stamp, close, ctx),
        }

    def _shared_gates(self, crt, bar_closes_exec, minute_of_day, params) -> List[StepResult]:
        if crt is None:
            crt_step = StepResult(f"CRT range ({params.reference_tf})", False,
                                  "No usable reference candle (warming up, or a data gap)")
        elif crt.range_size < params.min_range_points:
            crt_step = StepResult(f"CRT range ({params.reference_tf})", False,
                                  f"Range ${crt.range_size:.2f} below ${params.min_range_points:.2f} minimum")
        elif crt.range_size > params.max_range_points:
            crt_step = StepResult(f"CRT range ({params.reference_tf})", False,
                                  f"Range ${crt.range_size:.2f} above ${params.max_range_points:.2f} maximum")
        else:
            crt_step = StepResult(f"CRT range ({params.reference_tf})", True,
                                  f"High ${crt.high:.2f} / EQ ${crt.midpoint:.2f} / Low ${crt.low:.2f}")

        exec_step = StepResult(f"{params.execution_tf} candle closed", bool(bar_closes_exec),
                               "Closed" if bar_closes_exec else "Mid-candle, trigger only judged on a close")

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

    def _evaluate_side(self, direction, exec_series, idx, params, crt,
                       bar_closes_exec, gates, stamp, close, ctx=None) -> Evaluation:
        steps = list(gates)
        ctx = ctx or {}

        # HTF bias: a long fades a sweep of the LOW, which only makes sense
        # while the higher timeframe is bullish (and the mirror for shorts).
        if params.enable_htf_bias:
            bias, bias_detail = self._htf_bias(ctx, idx, params)
            wanted = "BULLISH" if direction == "LONG" else "BEARISH"
            ok = (bias == wanted)
            steps.append(StepResult(
                f"HTF bias ({params.htf_bias_tf} EMA{params.htf_bias_ema})", ok,
                bias_detail if ok else
                f"{bias} - a {direction} needs {wanted}. {bias_detail}"))

        gates_ok = all(s.passed for s in steps)

        if not gates_ok or crt is None:
            steps.append(StepResult("Manipulator candle (body close beyond level)", False,
                                    "Waiting on the checks above"))
            steps.append(StepResult("Trigger (break of manipulator extreme)", False,
                                    "Waiting on the checks above"))
            steps.append(StepResult("Risk:Reward to TP2", False, "Waiting on the checks above"))
            return self._make_eval(direction, stamp, idx, close, steps, 0.0, 0.0, None,
                                   0.0, 0.0, "", crt)

        ci = exec_series.candle_of_bar[idx]
        candles = exec_series.candles
        current = candles[ci]
        level = crt.high if direction == "SHORT" else crt.low

        # 1. Find the nearest manipulator candle within the lookback window: a
        # body close fully past the level, deep enough to be a trap but not so
        # deep it is a genuine breakout.
        manip = None
        manip_gap = None
        # "immediate" (Liquidity Purge Model 1) enters on the manipulator candle's
        # own close, so the candle being judged IS the manipulator. "confirmed"
        # (Complete Manual) looks back for one and needs a later break of it.
        immediate = (params.entry_mode == "immediate")
        first = ci if immediate else ci - 1
        start = ci if immediate else max(0, ci - params.manipulator_lookback)
        for j in range(first, start - 1, -1):
            c = candles[j]
            depth = (c.close - level) if direction == "SHORT" else (level - c.close)
            if params.min_body_break_points <= depth <= params.max_body_break_points:
                manip = c
                manip_gap = ci - j
                break

        if manip is None:
            manip_detail = (f"No candle in the last {params.manipulator_lookback} "
                            f"{params.execution_tf} bars closed its body past "
                            f"${level:.2f} by ${params.min_body_break_points:.2f}-"
                            f"${params.max_body_break_points:.2f}")
            steps.append(StepResult("Manipulator candle (body close beyond level)", False, manip_detail))
            steps.append(StepResult("Trigger (break of manipulator extreme)", False,
                                    "No manipulator candle yet"))
            steps.append(StepResult("Risk:Reward to TP2", False, "No manipulator candle yet"))
            return self._make_eval(direction, stamp, idx, close, steps, 0.0, 0.0, None,
                                   0.0, 0.0, "", crt)

        side_word = "above" if direction == "SHORT" else "below"
        manip_detail = (f"{params.execution_tf} candle closed ${manip.close:.2f}, "
                        f"{side_word} ${level:.2f} ({manip_gap} candles ago)")
        steps.append(StepResult("Manipulator candle (body close beyond level)", True, manip_detail))

        # 2. Trigger. In "immediate" mode the manipulator's own close is the
        # entry (Liquidity Purge Model 1). In "confirmed" mode a later candle
        # must close back through the manipulator's opposite extreme, within
        # the decay window (Complete Manual). Only the latest closed candle is
        # tested here; an earlier qualifying bar would already have fired then.
        extreme = manip.low if direction == "SHORT" else manip.high
        low_high = "low" if direction == "SHORT" else "high"

        if immediate:
            trigger_ok = True
            steps.append(StepResult(
                "Trigger (Model 1: manipulator close)", True,
                f"Entering on the manipulator's own close ${current.close:.2f} - "
                f"no confirmation candle required"))
        else:
            broke = (current.close < extreme) if direction == "SHORT"                 else (current.close > extreme)
            in_window = 1 <= manip_gap <= params.max_break_wait_candles
            trigger_ok = broke and in_window
            if not in_window:
                trig_detail = (f"Manipulator was {manip_gap} candles ago - past the "
                               f"{params.max_break_wait_candles}-candle decay window")
            elif broke:
                trig_detail = (f"Confirmed - closed ${current.close:.2f}, breaking the "
                               f"manipulator's {low_high} ${extreme:.2f}")
            else:
                trig_detail = (f"Not yet - close ${current.close:.2f} has not broken the "
                               f"manipulator's {low_high} ${extreme:.2f} "
                               f"({params.max_break_wait_candles - manip_gap} candles left)")
            steps.append(StepResult("Trigger (break of manipulator extreme)",
                                    trigger_ok, trig_detail))

        # 3. Levels + R:R, priced off this close (next bar fills at its open).
        entry_ref = current.close
        fixed = (params.target_mode == "fixed_r")
        if direction == "SHORT":
            sl = self._clamp_sl(entry_ref, manip.high + params.sl_buffer_points, True, params)
            risk = sl - entry_ref
            tp2 = (entry_ref - risk * params.fixed_r_multiple) if fixed else crt.low
            tp1 = crt.midpoint
            reward = entry_ref - tp2
            pattern = "Body Soup - bearish trap reversal"
        else:
            sl = self._clamp_sl(entry_ref, manip.low - params.sl_buffer_points, False, params)
            risk = entry_ref - sl
            tp2 = (entry_ref + risk * params.fixed_r_multiple) if fixed else crt.high
            tp1 = crt.midpoint
            reward = tp2 - entry_ref
            pattern = "Body Soup - bullish trap reversal"

        # A midpoint target only makes sense while it sits between entry and the
        # final target; with a fixed-R target it often does not, so drop it.
        if fixed:
            beyond = (tp1 < entry_ref and tp1 > tp2) if direction == "SHORT"                 else (tp1 > entry_ref and tp1 < tp2)
            if not beyond:
                tp1 = None

        rr = (reward / risk) if risk > 0 else 0.0
        rr_ok = risk > 0 and reward > 0 and rr >= params.min_rr_tp2
        rr_detail = (f"Risk ${risk:.2f} to SL ${sl:.2f}, reward ${reward:.2f} to TP2 ${tp2:.2f} "
                     f"= {rr:.2f}R (need {params.min_rr_tp2:.2f}R)") if risk > 0 and reward > 0 else \
                    f"TP2 ${tp2:.2f} already through price ${entry_ref:.2f}"
        steps.append(StepResult("Risk:Reward to TP2", rr_ok, rr_detail))

        return self._make_eval(direction, stamp, idx, close, steps, sl, tp2,
                               tp1 if (params.use_tp1_partial and tp1) else None,
                               round(risk, 2), round(reward, 2), pattern, crt,
                               entry_ref=entry_ref, rr=rr)


    @staticmethod
    def _htf_bias(ctx, idx, params) -> tuple:
        """(bias, detail) where bias is "BULLISH" / "BEARISH" / "NEUTRAL".

        Read from the last CLOSED bias candle, never the forming one, so this
        stays causal: the candle the current bar sits inside is excluded.
        """
        bias_series = ctx.get("bias")
        ema = ctx.get("bias_ema") or []
        if bias_series is None or not ema:
            return "NEUTRAL", "HTF bias unavailable"
        ci = bias_series.candle_of_bar[idx] if idx < len(bias_series.candle_of_bar) else -1
        prev = ci - 1
        if prev < 0 or prev >= len(ema):
            return "NEUTRAL", "Not enough HTF history yet"
        candle = bias_series.candles[prev]
        ema_val = ema[prev]
        if candle.close > ema_val:
            return "BULLISH", (f"{params.htf_bias_tf} close ${candle.close:.2f} > "
                               f"EMA{params.htf_bias_ema} ${ema_val:.2f}")
        return "BEARISH", (f"{params.htf_bias_tf} close ${candle.close:.2f} < "
                           f"EMA{params.htf_bias_ema} ${ema_val:.2f}")

    @staticmethod
    def _clamp_sl(entry: float, raw_sl: float, is_short: bool, params: BodySoupParams) -> float:
        dist = (raw_sl - entry) if is_short else (entry - raw_sl)
        dist = min(max(dist, params.min_sl_distance_points), params.max_sl_distance_points)
        return round(entry + dist, 2) if is_short else round(entry - dist, 2)

    @staticmethod
    def _make_eval(direction, stamp, idx, close, steps, sl, tp, tp1, risk, reward,
                   pattern, crt, entry_ref=0.0, rr=0.0) -> Evaluation:
        all_passed = bool(steps) and all(s.passed for s in steps)
        return Evaluation(
            direction=direction, timestamp=stamp, bar_index=idx, close_price=close,
            steps=steps, all_passed=all_passed,
            signal=("SELL" if direction == "SHORT" else "BUY") if all_passed else None,
            suggested_entry=round(entry_ref or close, 2),
            suggested_sl=round(sl, 2), suggested_tp=round(tp, 2),
            suggested_tp1=round(tp1, 2) if tp1 else None,
            risk_points=risk, reward_points=reward, pattern_name=pattern,
            context={
                "crt_high": round(crt.high, 2) if crt else None,
                "crt_low": round(crt.low, 2) if crt else None,
                "crt_eq": round(crt.midpoint, 2) if crt else None,
                "rr_tp2": round(rr, 2),
            },
        )


def strategy_doc_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CRTBodySoupStrategy.doc_file)
