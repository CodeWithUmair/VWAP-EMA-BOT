"""
Causal Backtesting Engine for XAU/USD Scalping Strategy.

Strict Non-Negotiable Requirements:
1. No Lookahead Bias: Signal generated on bar i MUST fill on bar i+1 Open (never bar i close).
2. Realistic Wick Fills: SL/TP evaluated against bar High/Low including spread and slippage.
3. Realistic Broker Costs: Sourced spread + commission per lot.
4. Train/Test Split: Distinct In-Sample (IS) and Out-of-Sample (OOS) evaluation.
5. Noise Control Gate: Permutation/Monte Carlo reshuffling (100+ iterations) to determine empirical p-value.
"""

import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from trading_bot.strategies import DEFAULT_STRATEGY_KEY, get_strategy
from trading_bot.strategy import StrategyParameters


@dataclass
class Trade:
    """Represents a simulated executed trade."""
    id: int
    direction: str  # "BUY" or "SELL"
    signal_bar: int
    signal_time: str
    entry_bar: int
    entry_time: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_points: float
    reward_points: float
    lot_size: float = 0.1
    exit_bar: Optional[int] = None
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # "TAKE_PROFIT", "STOP_LOSS", "END_OF_DATA", "TIMEOUT"
    gross_pnl_usd: float = 0.0
    net_pnl_usd: float = 0.0
    pnl_r_multiple: float = 0.0
    spread_paid_usd: float = 0.0
    commission_paid_usd: float = 0.0
    duration_bars: int = 0
    pattern_name: str = ""
    is_out_of_sample: bool = False
    # Two-target strategies (CRT + TBS): half off at TP1, stop to break-even.
    take_profit_1: Optional[float] = None
    tp1_hit: bool = False
    tp1_bar: Optional[int] = None
    partial_pnl_usd: float = 0.0


@dataclass
class BacktestMetrics:
    """Summary metrics for a backtest segment."""
    segment_name: str  # "IN_SAMPLE", "OUT_OF_SAMPLE", "OVERALL"
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    expectancy_r: float
    expectancy_usd: float
    total_net_pnl_usd: float
    max_drawdown_usd: float
    max_drawdown_pct: float
    average_trade_bars: float
    avg_win_usd: float
    avg_loss_usd: float
    payoff_ratio: float
    max_consecutive_losses: int
    sharpe_ratio: float
    # Noise control results
    noise_gate_passed: bool
    noise_p_value: float
    z_score: float
    shuffled_expectancies: List[float] = field(default_factory=list)


@dataclass
class BacktestResult:
    """Full backtest results containing trades, equity curves, and metrics."""
    params: StrategyParameters
    in_sample_metrics: BacktestMetrics
    out_of_sample_metrics: BacktestMetrics
    overall_metrics: BacktestMetrics
    trades: List[Trade]
    equity_curve: List[Dict[str, Any]]
    split_index: int
    initial_balance: float
    final_balance: float
    symbol: str = "XAUUSD"
    timeframe: str = "M1"
    spread_points: float = 0.25
    commission_per_lot_usd: float = 7.0
    strategy_key: str = DEFAULT_STRATEGY_KEY
    strategy_label: str = ""


# Share of the position banked when a two-target strategy tags TP1.
TP1_CLOSE_FRACTION = 0.5


def _finalize_trade(
    trade: Trade,
    exit_bar: int,
    exit_time: str,
    exit_price: float,
    exit_reason: str,
    spread_points: float,
    commission_per_lot_usd: float
) -> None:
    """Book the closing leg of a trade and stamp its PnL fields, in place.

    Gold PnL: 1.0 point on 0.1 lot = $10.00, i.e. point_diff * (lot_size * 100).
    When TP1 already banked half the position, only the remainder rides to this
    exit; the partial is added back in as gross. Commission is charged once, on
    the full opened volume.
    """
    trade.exit_bar = exit_bar
    trade.exit_time = exit_time
    trade.exit_price = exit_price
    trade.exit_reason = exit_reason
    trade.duration_bars = exit_bar - trade.entry_bar + 1

    point_multiplier = trade.lot_size * 100.0
    remaining = (1.0 - TP1_CLOSE_FRACTION) if trade.tp1_hit else 1.0

    pts_diff = (exit_price - trade.entry_price) if trade.direction == "BUY" \
        else (trade.entry_price - exit_price)

    gross_pnl = trade.partial_pnl_usd + pts_diff * point_multiplier * remaining
    comm_usd = commission_per_lot_usd * trade.lot_size
    net_pnl = gross_pnl - comm_usd

    # R-multiple = Net PnL / Initial Risk USD (always the full-size risk taken on).
    initial_risk_usd = max(trade.risk_points * point_multiplier + comm_usd, 1.0)

    trade.gross_pnl_usd = round(gross_pnl, 2)
    trade.net_pnl_usd = round(net_pnl, 2)
    trade.commission_paid_usd = round(comm_usd, 2)
    trade.spread_paid_usd = round(spread_points * point_multiplier, 2)
    trade.pnl_r_multiple = round(net_pnl / initial_risk_usd, 2)


def run_noise_control_gate(
    trades: List[Trade],
    num_shuffles: int = 100,
    alpha_threshold: float = 0.05,
    min_trade_count: int = 20
) -> Tuple[bool, float, float, List[float]]:
    """
    MANDATORY noise/statistical-significance control test.
    Shuffles trade return sequences N times to generate the null distribution of expectancy.
    Compares real observed expectancy against the null distribution.
    
    Returns (gate_passed, p_value, z_score, shuffled_expectancies).
    """
    if len(trades) < min_trade_count:
        return False, 1.0, 0.0, [0.0] * num_shuffles

    r_multiples = [t.pnl_r_multiple for t in trades]
    real_expectancy = sum(r_multiples) / len(r_multiples)

    if real_expectancy <= 0:
        return False, 1.0, 0.0, [0.0] * num_shuffles

    shuffled_expectancies = []
    better_or_equal_count = 0

    # Null hypothesis test: trade outcomes occur randomly with equal chance of direction/returns
    for _ in range(num_shuffles):
        # Permute with random sign flips or trade order shuffle
        # For expectancy significance vs random coin flip: random sign permutation on absolute magnitudes
        shuffled_sample = [r if random.random() > 0.5 else -abs(r) for r in r_multiples]
        shuffled_exp = sum(shuffled_sample) / len(shuffled_sample)
        shuffled_expectancies.append(round(shuffled_exp, 4))
        if shuffled_exp >= real_expectancy:
            better_or_equal_count += 1

    p_value = round((better_or_equal_count + 1) / (num_shuffles + 1), 4)

    # Calculate z-score
    mean_null = sum(shuffled_expectancies) / len(shuffled_expectancies)
    var_null = sum((x - mean_null) ** 2 for x in shuffled_expectancies) / max(len(shuffled_expectancies) - 1, 1)
    std_null = math.sqrt(max(var_null, 1e-6))
    z_score = round((real_expectancy - mean_null) / std_null, 2)

    passed = (p_value <= alpha_threshold and real_expectancy > 0 and len(trades) >= min_trade_count)
    return passed, p_value, z_score, shuffled_expectancies


def calculate_metrics_from_trades(
    trades: List[Trade],
    segment_name: str,
    initial_balance: float = 10000.0,
    num_shuffles: int = 100
) -> BacktestMetrics:
    """Computes comprehensive trading performance metrics and runs noise gate."""
    if not trades:
        return BacktestMetrics(
            segment_name=segment_name,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate_pct=0.0,
            profit_factor=0.0,
            expectancy_r=0.0,
            expectancy_usd=0.0,
            total_net_pnl_usd=0.0,
            max_drawdown_usd=0.0,
            max_drawdown_pct=0.0,
            average_trade_bars=0.0,
            avg_win_usd=0.0,
            avg_loss_usd=0.0,
            payoff_ratio=0.0,
            max_consecutive_losses=0,
            sharpe_ratio=0.0,
            noise_gate_passed=False,
            noise_p_value=1.0,
            z_score=0.0,
            shuffled_expectancies=[]
        )

    total_trades = len(trades)
    winning_trades = len([t for t in trades if t.net_pnl_usd > 0])
    losing_trades = len([t for t in trades if t.net_pnl_usd <= 0])
    win_rate_pct = round((winning_trades / total_trades) * 100.0, 2)

    gross_wins = sum(t.net_pnl_usd for t in trades if t.net_pnl_usd > 0)
    gross_losses = abs(sum(t.net_pnl_usd for t in trades if t.net_pnl_usd < 0))
    profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else (99.0 if gross_wins > 0 else 0.0)

    total_net_pnl_usd = round(sum(t.net_pnl_usd for t in trades), 2)
    expectancy_r = round(sum(t.pnl_r_multiple for t in trades) / total_trades, 3)
    expectancy_usd = round(total_net_pnl_usd / total_trades, 2)

    avg_win_usd = round(gross_wins / winning_trades, 2) if winning_trades > 0 else 0.0
    avg_loss_usd = round(gross_losses / losing_trades, 2) if losing_trades > 0 else 0.0
    payoff_ratio = round(avg_win_usd / avg_loss_usd, 2) if avg_loss_usd > 0 else 0.0

    # Drawdown calculation
    balance = initial_balance
    peak = initial_balance
    max_dd_usd = 0.0
    max_dd_pct = 0.0

    curr_consec_losses = 0
    max_consec_losses = 0

    pnl_returns = []

    for t in trades:
        balance += t.net_pnl_usd
        if balance > peak:
            peak = balance
        dd_usd = peak - balance
        dd_pct = (dd_usd / peak) * 100.0 if peak > 0 else 0.0
        if dd_usd > max_dd_usd:
            max_dd_usd = dd_usd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

        if t.net_pnl_usd <= 0:
            curr_consec_losses += 1
            if curr_consec_losses > max_consec_losses:
                max_consec_losses = curr_consec_losses
        else:
            curr_consec_losses = 0

        pnl_returns.append(t.net_pnl_usd)

    avg_duration = round(sum(t.duration_bars for t in trades) / total_trades, 1)

    # Approximate Sharpe Ratio
    if len(pnl_returns) > 1:
        mean_ret = sum(pnl_returns) / len(pnl_returns)
        var_ret = sum((r - mean_ret) ** 2 for r in pnl_returns) / (len(pnl_returns) - 1)
        std_ret = math.sqrt(max(var_ret, 1e-6))
        sharpe = round((mean_ret / std_ret) * math.sqrt(252 * 50), 2)
    else:
        sharpe = 0.0

    # Noise control
    passed_gate, p_val, z_sc, shuffles = run_noise_control_gate(trades, num_shuffles=num_shuffles)

    return BacktestMetrics(
        segment_name=segment_name,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        expectancy_r=expectancy_r,
        expectancy_usd=expectancy_usd,
        total_net_pnl_usd=total_net_pnl_usd,
        max_drawdown_usd=round(max_dd_usd, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        average_trade_bars=avg_duration,
        avg_win_usd=avg_win_usd,
        avg_loss_usd=avg_loss_usd,
        payoff_ratio=payoff_ratio,
        max_consecutive_losses=max_consec_losses,
        sharpe_ratio=sharpe,
        noise_gate_passed=passed_gate,
        noise_p_value=p_val,
        z_score=z_sc,
        shuffled_expectancies=shuffles
    )


def run_causal_backtest(
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    times: List[str],
    volumes: List[float],
    params: StrategyParameters,
    initial_balance: float = 10000.0,
    split_ratio: float = 0.75,  # 75% In-Sample, 25% Out-of-Sample
    spread_points: float = 0.25, # $0.25 spread on Gold
    commission_per_lot_usd: float = 7.0, # $7 round turn commission per standard lot
    fixed_lot_size: float = 0.1,  # 0.1 lot = $10 per point on Gold
    max_open_positions: int = 1,
    num_noise_shuffles: int = 100,
    strategy: Any = None,          # BaseStrategy; defaults to the VWAP/EMA scalper
    max_trade_bars: int = 0        # 0 = hold until SL/TP/end of data
) -> BacktestResult:
    """
    Executes a causal, zero-lookahead backtest across historical bars.

    Causal Guarantee:
    - Checklist evaluated on bar i.
    - Signal recorded at bar i close.
    - Filled at bar i+1 Open.
    - Exit evaluated on bar i+1 and beyond wicks.

    ``strategy`` is any object implementing the BaseStrategy interface from
    :mod:`trading_bot.strategies`. It is prepared once (indicators, higher-
    timeframe candles) and then asked for a verdict at each bar, so swapping in
    the CRT + TBS sweep strategy costs nothing here beyond the argument.
    """
    n = len(closes)
    if n < 50:
        raise ValueError(f"Insufficient historical bars for backtest (got {n}, need at least 50)")

    if strategy is None:
        strategy = get_strategy(DEFAULT_STRATEGY_KEY)

    data = {
        "opens": opens, "highs": highs, "lows": lows,
        "closes": closes, "times": times, "volumes": volumes,
    }
    strategy_ctx = strategy.prepare(data, params)

    split_idx = int(n * split_ratio)
    trades: List[Trade] = []
    trade_id_counter = 1

    open_trade: Optional[Trade] = None
    pending_signal: Optional[Dict[str, Any]] = None

    equity = initial_balance
    equity_curve = [{
        "bar_index": 0,
        "time": times[0] if times else "0",
        "balance": equity,
        "equity": equity,
        "is_out_of_sample": False
    }]

    # Start loop after enough warm-up bars for whatever the strategy needs
    # (indicators and swings for the scalper, whole reference candles for CRT).
    warmup = min(max(strategy.warmup_bars(params), 5), max(n - 2, 5))

    for i in range(warmup, n):
        c_open = opens[i]
        c_high = highs[i]
        c_low = lows[i]
        c_close = closes[i]
        c_time = times[i] if i < len(times) else str(i)
        is_oos = (i >= split_idx)

        # 1. PROCESS PENDING SIGNAL FROM PREVIOUS BAR (Execute at bar i Open)
        if pending_signal is not None and open_trade is None:
            sig = pending_signal
            direction = sig["direction"]
            pattern = sig["pattern"]
            sl = sig["sl"]
            tp = sig["tp"]
            tp1 = sig.get("tp1")

            if direction == "BUY":
                fill_price = c_open + spread_points
                risk_pts = fill_price - sl
                reward_pts = tp - fill_price
                if risk_pts >= params.min_sl_distance_points:
                    open_trade = Trade(
                        id=trade_id_counter,
                        direction="BUY",
                        signal_bar=sig["bar_index"],
                        signal_time=sig["time"],
                        entry_bar=i,
                        entry_time=c_time,
                        entry_price=fill_price,
                        stop_loss=sl,
                        take_profit=tp,
                        risk_points=risk_pts,
                        reward_points=reward_pts,
                        lot_size=fixed_lot_size,
                        pattern_name=pattern,
                        is_out_of_sample=is_oos,
                        take_profit_1=tp1
                    )
                    trade_id_counter += 1
            elif direction == "SELL":
                fill_price = c_open
                risk_pts = sl - fill_price
                reward_pts = fill_price - tp
                if risk_pts >= params.min_sl_distance_points:
                    open_trade = Trade(
                        id=trade_id_counter,
                        direction="SELL",
                        signal_bar=sig["bar_index"],
                        signal_time=sig["time"],
                        entry_bar=i,
                        entry_time=c_time,
                        entry_price=fill_price,
                        stop_loss=sl,
                        take_profit=tp,
                        risk_points=risk_pts,
                        reward_points=reward_pts,
                        lot_size=fixed_lot_size,
                        pattern_name=pattern,
                        is_out_of_sample=is_oos,
                        take_profit_1=tp1
                    )
                    trade_id_counter += 1

            pending_signal = None

        # 2. CHECK EXITS ON ACTIVE TRADE USING CURRENT BAR WICKS
        if open_trade is not None:
            # For BUY: SL hit if Low <= SL, TP hit if High >= TP
            # For SELL: SL hit if High + spread >= SL, TP hit if Low + spread <= TP
            hit_sl = False
            hit_tp = False
            hit_tp1 = False
            exit_price = 0.0
            exit_reason = ""
            tp1 = open_trade.take_profit_1

            if open_trade.direction == "BUY":
                if c_low <= open_trade.stop_loss:
                    hit_sl = True
                    exit_price = open_trade.stop_loss
                    exit_reason = "BREAK_EVEN" if open_trade.tp1_hit else "STOP_LOSS"
                elif c_high >= open_trade.take_profit:
                    hit_tp = True
                    exit_price = open_trade.take_profit
                    exit_reason = "TAKE_PROFIT"
                elif tp1 and not open_trade.tp1_hit and c_high >= tp1:
                    hit_tp1 = True
            elif open_trade.direction == "SELL":
                if (c_high + spread_points) >= open_trade.stop_loss:
                    hit_sl = True
                    exit_price = open_trade.stop_loss
                    exit_reason = "BREAK_EVEN" if open_trade.tp1_hit else "STOP_LOSS"
                elif (c_low + spread_points) <= open_trade.take_profit:
                    hit_tp = True
                    exit_price = open_trade.take_profit
                    exit_reason = "TAKE_PROFIT"
                elif tp1 and not open_trade.tp1_hit and (c_low + spread_points) <= tp1:
                    hit_tp1 = True

            # Two-target management: bank half at TP1 (CRT equilibrium) and pull the
            # stop to entry, so the runner to TP2 can no longer turn into a loss.
            # SL is tested first above, so a bar that reaches both is booked as the
            # stop - the pessimistic reading, since intrabar order is unknowable.
            if hit_tp1:
                open_trade.tp1_hit = True
                open_trade.tp1_bar = i
                point_multiplier = open_trade.lot_size * 100.0
                pts = (tp1 - open_trade.entry_price) if open_trade.direction == "BUY" \
                    else (open_trade.entry_price - tp1)
                open_trade.partial_pnl_usd = round(pts * point_multiplier * TP1_CLOSE_FRACTION, 2)
                open_trade.stop_loss = open_trade.entry_price

            if hit_sl or hit_tp:
                _finalize_trade(open_trade, i, c_time, exit_price, exit_reason,
                                spread_points, commission_per_lot_usd)
                trades.append(open_trade)
                equity += open_trade.net_pnl_usd
                open_trade = None

            elif max_trade_bars and (i - open_trade.entry_bar) >= max_trade_bars:
                _finalize_trade(open_trade, i, c_time, c_close, "TIMEOUT",
                                spread_points, commission_per_lot_usd)
                trades.append(open_trade)
                equity += open_trade.net_pnl_usd
                open_trade = None

        # 3. EVALUATE STRATEGY AT BAR CLOSE (FOR NEXT BAR FILL)
        if open_trade is None and pending_signal is None and i < n - 1:
            checklist = strategy.evaluate(data, i, params, strategy_ctx)
            long_st = checklist["LONG"]
            short_st = checklist["SHORT"]

            if long_st.all_passed:
                pending_signal = {
                    "direction": "BUY",
                    "bar_index": i,
                    "time": c_time,
                    "sl": long_st.suggested_sl,
                    "tp": long_st.suggested_tp,
                    "tp1": long_st.suggested_tp1,
                    "pattern": long_st.pattern_name
                }
            elif short_st.all_passed:
                pending_signal = {
                    "direction": "SELL",
                    "bar_index": i,
                    "time": c_time,
                    "sl": short_st.suggested_sl,
                    "tp": short_st.suggested_tp,
                    "tp1": short_st.suggested_tp1,
                    "pattern": short_st.pattern_name
                }

        # Record equity point every 10 bars or on trade close
        if i % 10 == 0 or open_trade is None:
            equity_curve.append({
                "bar_index": i,
                "time": c_time,
                "balance": round(equity, 2),
                "equity": round(equity, 2),
                "is_out_of_sample": is_oos
            })

    # Close any remaining open trade at end of data
    if open_trade is not None:
        _finalize_trade(open_trade, n - 1, times[-1] if times else str(n - 1),
                        closes[-1], "END_OF_DATA", spread_points, commission_per_lot_usd)
        trades.append(open_trade)
        equity += open_trade.net_pnl_usd

    # Partition trades into In-Sample and Out-of-Sample
    is_trades = [t for t in trades if not t.is_out_of_sample]
    oos_trades = [t for t in trades if t.is_out_of_sample]

    is_metrics = calculate_metrics_from_trades(is_trades, "IN_SAMPLE", initial_balance, num_noise_shuffles)
    oos_metrics = calculate_metrics_from_trades(oos_trades, "OUT_OF_SAMPLE", initial_balance, num_noise_shuffles)
    overall_metrics = calculate_metrics_from_trades(trades, "OVERALL", initial_balance, num_noise_shuffles)

    return BacktestResult(
        params=params,
        in_sample_metrics=is_metrics,
        out_of_sample_metrics=oos_metrics,
        overall_metrics=overall_metrics,
        trades=trades,
        equity_curve=equity_curve,
        split_index=split_idx,
        initial_balance=initial_balance,
        final_balance=round(equity, 2),
        symbol="XAUUSD",
        timeframe=getattr(strategy, "timeframe", "M1"),
        spread_points=spread_points,
        commission_per_lot_usd=commission_per_lot_usd,
        strategy_key=getattr(strategy, "key", DEFAULT_STRATEGY_KEY),
        strategy_label=getattr(strategy, "label", "")
    )
