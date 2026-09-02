"""
Strategy Module: Triple Filter EMA 9/21 + VWAP Scalping with Order Blocks
Designed for XAU/USD (Gold) on 1-minute (M1) timeframe.

Zero Lookahead Bias: Every calculation causal at bar i.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple


@dataclass
class StrategyParameters:
    """
    All strategy parameters with explicit names and default values.
    No hardcoded magic numbers.
    """
    # 1. Moving Averages
    ema_fast_period: int = 9
    ema_slow_period: int = 21

    # 2. VWAP Settings
    vwap_anchor_hour_utc: int = 0       # 00:00 UTC daily open reset
    vwap_min_dist_points: float = 0.0   # Minimum distance from VWAP

    # 3. Order Block & Break of Structure (BOS) Settings
    ob_swing_lookback: int = 3          # Lookback bars for causal swing high/low
    pivot_lookback: int = 3             # Alias for lookback bars
    ob_max_age_bars: int = 60           # Maximum bars an OB remains active
    ob_zone_type: str = "full"          # "full" (low to high) or "body" (open to close)
    ob_buffer_atr: float = 0.35         # Buffer in ATR to touch/react to OB

    # 4. Pullback Settings
    max_pullback_bars: int = 20         # Max bars after crossover before setup expires
    pullback_atr_mult: float = 1.5      # Max distance from EMA in ATR
    pullback_require_ema_touch: bool = True

    # 5. Confirmation Candle & ATR Settings
    atr_period: int = 14                # ATR lookback period (ZAROORI)
    min_candle_body_atr: float = 0.35   # Minimum body size in ATR (ZAROORI)
    pinbar_wick_ratio: float = 2.0      # Rejection wick ratio (ZAROORI)
    pinbar_nose_ratio: float = 0.5      # Nose ratio (ZAROORI)

    # 6. Exit & Risk:Reward Settings
    rr_ratio: float = 1.5               # Risk:Reward Ratio (1:1.5)
    sl_lookback_bars: int = 8           # Lookback bars for recent swing low/high
    sl_buffer_atr: float = 0.50         # Extra cushion beyond swing in ATR (breathing room)
    min_sl_distance_points: float = 1.8  # Minimum SL in USD ($1.80 on Gold to avoid noise)
    max_sl_distance_points: float = 6.0  # Maximum allowable SL ($6.00 on Gold)

    # 7. Multi-Timeframe & Session Settings
    enable_htf_filter: bool = True       # Enforce 15M / 5M HTF trend alignment
    htf_ema_period: int = 50             # 50-period EMA on HTF
    enable_session_filter: bool = False  # Restrict to London/NY killzones only



@dataclass
class OrderBlock:
    """Represents a causal Order Block formed before a Break of Structure."""
    id: str
    direction: str  # "BULLISH" or "BEARISH"
    bar_index: int
    time: str
    open: float
    high: float
    low: float
    close: float
    bos_bar_index: int
    bos_level: float
    zone_low: float
    zone_high: float
    is_mitigated: bool = False
    is_active: bool = True


@dataclass
class ChecklistStatus:
    """Detailed live evaluation checklist for the 5 entry conditions."""
    direction: str  # "LONG" or "SHORT"
    timestamp: str
    bar_index: int
    close_price: float

    # 1. Trend filter (VWAP)
    vwap_value: float
    vwap_pass: bool
    vwap_detail: str

    # 2. Crossover (EMA9 vs EMA21)
    ema_fast: float
    ema_slow: float
    crossover_pass: bool
    bars_since_cross: int
    crossover_detail: str

    # 3. Structural Confirmation (Order Block)
    ob_pass: bool
    active_ob: Optional[OrderBlock] = None
    ob_detail: str = ""

    # 4. Pullback to EMAs
    pullback_pass: bool = False
    pullback_dist_atr: float = 0.0
    pullback_detail: str = ""

    # 5. Confirmation Candle Trigger
    confirmation_pass: bool = False
    pattern_name: str = ""
    confirmation_detail: str = ""

    # Overall Signal
    all_passed: bool = False
    signal: Optional[str] = None  # "BUY", "SELL", or None
    suggested_entry: float = 0.0
    suggested_sl: float = 0.0
    suggested_tp: float = 0.0
    risk_points: float = 0.0
    reward_points: float = 0.0


def calculate_ema(prices: List[float], period: int) -> List[float]:
    """Calculates Exponential Moving Average causally."""
    if not prices or period <= 0:
        return []
    n = len(prices)
    ema = [0.0] * n
    if n < period:
        avg = sum(prices) / n
        return [avg] * n

    sma = sum(prices[:period]) / period
    for i in range(period):
        ema[i] = sma

    multiplier = 2.0 / (period + 1.0)
    for i in range(period, n):
        ema[i] = (prices[i] - ema[i - 1]) * multiplier + ema[i - 1]
    return ema


def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[float]:
    """Calculates Average True Range (Wilder's ATR) causally."""
    n = len(highs)
    if n == 0:
        return []
    tr = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

    atr = [0.0] * n
    if n < period:
        avg = sum(tr) / n if n > 0 else 1.0
        return [avg] * n

    atr[period - 1] = sum(tr[:period]) / period
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    for i in range(period - 1):
        atr[i] = atr[period - 1]
    return atr


def calculate_session_vwap(
    times: List[Any],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    volumes: List[float],
    anchor_hour_utc: int = 0
) -> List[float]:
    """
    Session-anchored Volume Weighted Average Price (VWAP).
    Resets at every new UTC day at `anchor_hour_utc` (00:00 UTC default).
    """
    n = len(closes)
    vwap = [0.0] * n
    if n == 0:
        return vwap

    cum_vol = 0.0
    cum_vol_price = 0.0
    last_day_key = None

    for i in range(n):
        t = times[i]
        if isinstance(t, str):
            try:
                dt = datetime.fromisoformat(t.replace('Z', '+00:00'))
            except Exception:
                dt = datetime.utcfromtimestamp(i * 60)
        elif isinstance(t, (int, float)):
            dt = datetime.fromtimestamp(t, tz=timezone.utc)
        elif isinstance(t, datetime):
            dt = t
        else:
            dt = datetime.utcfromtimestamp(i * 60)

        day_key = dt.strftime("%Y-%m-%d")

        if day_key != last_day_key:
            cum_vol = 0.0
            cum_vol_price = 0.0
            last_day_key = day_key

        typical_price = (highs[i] + lows[i] + closes[i]) / 3.0
        vol = max(float(volumes[i]) if i < len(volumes) and volumes[i] is not None else 1.0, 1.0)

        cum_vol_price += typical_price * vol
        cum_vol += vol

        vwap[i] = cum_vol_price / cum_vol if cum_vol > 0 else typical_price

    return vwap


def find_causal_swings(highs: List[float], lows: List[float], lookback: int = 5) -> Tuple[List[Dict], List[Dict]]:
    """
    Identifies swing highs and swing lows strictly causally.
    A swing high at bar k is only confirmed at bar k + lookback.
    """
    n = len(highs)
    swing_highs = []
    swing_lows = []

    for k in range(lookback, n - lookback):
        is_sh = True
        for j in range(1, lookback + 1):
            if highs[k - j] >= highs[k] or highs[k + j] >= highs[k]:
                is_sh = False
                break
        if is_sh:
            swing_highs.append({
                'index': k,
                'price': highs[k],
                'confirmed_at': k + lookback
            })

        is_sl = True
        for j in range(1, lookback + 1):
            if lows[k - j] <= lows[k] or lows[k + j] <= lows[k]:
                is_sl = False
                break
        if is_sl:
            swing_lows.append({
                'index': k,
                'price': lows[k],
                'confirmed_at': k + lookback
            })

    return swing_highs, swing_lows


def detect_order_blocks_causal(
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    times: List[str],
    current_idx: int,
    params: StrategyParameters
) -> List[OrderBlock]:
    """
    Detects Order Blocks strictly causally up to current_idx.
    """
    if current_idx < params.ob_swing_lookback * 2 + 5:
        return []

    window_start = max(0, current_idx - (params.ob_max_age_bars + params.ob_swing_lookback * 3))
    sub_highs = highs[:current_idx + 1]
    sub_lows = lows[:current_idx + 1]
    sub_opens = opens[:current_idx + 1]
    sub_closes = closes[:current_idx + 1]

    swing_highs, swing_lows = find_causal_swings(sub_highs, sub_lows, params.ob_swing_lookback)

    order_blocks: List[OrderBlock] = []
    start_eval = max(params.ob_swing_lookback * 2, window_start)

    for i in range(start_eval, current_idx + 1):
        confirmed_sh = [sh for sh in swing_highs if sh['confirmed_at'] < i and sh['index'] < i]
        if confirmed_sh:
            last_sh = confirmed_sh[-1]
            if sub_closes[i] > last_sh['price'] and sub_closes[i - 1] <= last_sh['price']:
                ob_idx = None
                for k in range(i - 1, max(0, last_sh['index'] - 2), -1):
                    if sub_closes[k] < sub_opens[k]:
                        ob_idx = k
                        break
                if ob_idx is not None:
                    zone_l = sub_lows[ob_idx] if params.ob_zone_type == "full" else min(sub_opens[ob_idx], sub_closes[ob_idx])
                    zone_h = sub_highs[ob_idx] if params.ob_zone_type == "full" else max(sub_opens[ob_idx], sub_closes[ob_idx])
                    ob = OrderBlock(
                        id=f"BULL_OB_{ob_idx}",
                        direction="BULLISH",
                        bar_index=ob_idx,
                        time=times[ob_idx] if ob_idx < len(times) else str(ob_idx),
                        open=sub_opens[ob_idx],
                        high=sub_highs[ob_idx],
                        low=sub_lows[ob_idx],
                        close=sub_closes[ob_idx],
                        bos_bar_index=i,
                        bos_level=last_sh['price'],
                        zone_low=zone_l,
                        zone_high=zone_h,
                        is_mitigated=False,
                        is_active=True
                    )
                    order_blocks.append(ob)

        confirmed_sl = [sl for sl in swing_lows if sl['confirmed_at'] < i and sl['index'] < i]
        if confirmed_sl:
            last_sl = confirmed_sl[-1]
            if sub_closes[i] < last_sl['price'] and sub_closes[i - 1] >= last_sl['price']:
                ob_idx = None
                for k in range(i - 1, max(0, last_sl['index'] - 2), -1):
                    if sub_closes[k] > sub_opens[k]:
                        ob_idx = k
                        break
                if ob_idx is not None:
                    zone_l = sub_lows[ob_idx] if params.ob_zone_type == "full" else min(sub_opens[ob_idx], sub_closes[ob_idx])
                    zone_h = sub_highs[ob_idx] if params.ob_zone_type == "full" else max(sub_opens[ob_idx], sub_closes[ob_idx])
                    ob = OrderBlock(
                        id=f"BEAR_OB_{ob_idx}",
                        direction="BEARISH",
                        bar_index=ob_idx,
                        time=times[ob_idx] if ob_idx < len(times) else str(ob_idx),
                        open=sub_opens[ob_idx],
                        high=sub_highs[ob_idx],
                        low=sub_lows[ob_idx],
                        close=sub_closes[ob_idx],
                        bos_bar_index=i,
                        bos_level=last_sl['price'],
                        zone_low=zone_l,
                        zone_high=zone_h,
                        is_mitigated=False,
                        is_active=True
                    )
                    order_blocks.append(ob)

    active_obs = []
    for ob in order_blocks:
        age = current_idx - ob.bar_index
        if age > params.ob_max_age_bars:
            ob.is_active = False
            continue

        invalidated = False
        mitigated = False
        for b in range(ob.bos_bar_index + 1, current_idx + 1):
            if ob.direction == "BULLISH":
                if sub_closes[b] < ob.zone_low:
                    invalidated = True
                    break
                if sub_lows[b] <= ob.zone_high and sub_highs[b] >= ob.zone_low:
                    mitigated = True
            else:
                if sub_closes[b] > ob.zone_high:
                    invalidated = True
                    break
                if sub_highs[b] >= ob.zone_low and sub_lows[b] <= ob.zone_high:
                    mitigated = True

        if invalidated:
            ob.is_active = False
        else:
            ob.is_mitigated = mitigated
            active_obs.append(ob)

    return active_obs


def check_confirmation_candle(
    open_val: float,
    high_val: float,
    low_val: float,
    close_val: float,
    prev_open: float,
    prev_high: float,
    prev_low: float,
    prev_close: float,
    atr: float,
    direction: str,
    params: StrategyParameters
) -> Tuple[bool, str, str]:
    """
    Evaluates confirmation candlestick patterns:
    1. Bullish / Bearish Engulfing
    2. Hammer / Shooting Star (Pinbar Rejection)
    3. Strong Directional Expansion
    """
    total_range = max(high_val - low_val, 0.001)
    body = abs(close_val - open_val)

    if direction == "LONG":
        is_bullish = close_val > open_val
        lower_wick = min(open_val, close_val) - low_val
        upper_wick = high_val - max(open_val, close_val)

        # Pattern 1: Bullish Engulfing
        if is_bullish and prev_close < prev_open:
            if close_val >= prev_open and open_val <= prev_close and body >= params.min_candle_body_atr * atr:
                return True, "Bullish Engulfing", f"Body ({body:.2f}) engulfed prior red candle"

        # Pattern 2: Bullish Hammer / Pinbar Rejection
        if lower_wick >= params.pinbar_wick_ratio * max(body, 0.1 * atr) and upper_wick <= params.pinbar_nose_ratio * lower_wick:
            if close_val >= (low_val + 0.60 * total_range):
                return True, "Hammer / Rejection Pinbar", f"Long lower wick ({lower_wick:.2f}) rejecting support"

        # Pattern 3: Strong Momentum Reversal Candle
        if is_bullish and close_val > prev_high and body >= params.min_candle_body_atr * atr:
            return True, "Strong Bullish Expansion", f"Close ({close_val:.2f}) broke above previous high ({prev_high:.2f})"

    elif direction == "SHORT":
        is_bearish = close_val < open_val
        upper_wick = high_val - max(open_val, close_val)
        lower_wick = min(open_val, close_val) - low_val

        # Pattern 1: Bearish Engulfing
        if is_bearish and prev_close > prev_open:
            if close_val <= prev_open and open_val >= prev_close and body >= params.min_candle_body_atr * atr:
                return True, "Bearish Engulfing", f"Body ({body:.2f}) engulfed prior green candle"

        # Pattern 2: Shooting Star / Bearish Pinbar Rejection
        if upper_wick >= params.pinbar_wick_ratio * max(body, 0.1 * atr) and lower_wick <= params.pinbar_nose_ratio * upper_wick:
            if close_val <= (low_val + 0.40 * total_range):
                return True, "Shooting Star / Upper Rejection", f"Long upper wick ({upper_wick:.2f}) rejecting resistance"

        # Pattern 3: Strong Bearish Expansion Candle
        if is_bearish and close_val < prev_low and body >= params.min_candle_body_atr * atr:
            return True, "Strong Bearish Expansion", f"Close ({close_val:.2f}) broke below previous low ({prev_low:.2f})"

    return False, "None", "No qualifying confirmation pattern"


def calculate_sl_tp(
    direction: str,
    entry_price: float,
    recent_highs: List[float],
    recent_lows: List[float],
    atr: float,
    params: StrategyParameters
) -> Tuple[float, float, float]:
    """
    Calculates causal SL and TP strictly based on recent swing low/high + ATR buffer.
    Returns (sl, tp, risk_points).
    """
    lookback = min(params.sl_lookback_bars, len(recent_highs))
    buffer = params.sl_buffer_atr * atr

    if direction.upper() in ["BUY", "LONG"]:
        swing_low = min(recent_lows[-lookback:]) if lookback > 0 else entry_price - 2.0
        sl = swing_low - buffer
        
        sl_dist = entry_price - sl
        if sl_dist < params.min_sl_distance_points:
            sl = entry_price - params.min_sl_distance_points
        elif sl_dist > params.max_sl_distance_points:
            sl = entry_price - params.max_sl_distance_points

        risk = entry_price - sl
        tp = entry_price + (risk * params.rr_ratio)

    else:  # SELL / SHORT
        swing_high = max(recent_highs[-lookback:]) if lookback > 0 else entry_price + 2.0
        sl = swing_high + buffer
        
        sl_dist = sl - entry_price
        if sl_dist < params.min_sl_distance_points:
            sl = entry_price + params.min_sl_distance_points
        elif sl_dist > params.max_sl_distance_points:
            sl = entry_price + params.max_sl_distance_points

        risk = sl - entry_price
        tp = entry_price - (risk * params.rr_ratio)

    return round(sl, 2), round(tp, 2), round(risk, 2)


def evaluate_checklist_at_bar(
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    times: List[str],
    volumes: List[float],
    current_idx: int,
    params: StrategyParameters,
    cached_indicators: Optional[Dict[str, List[float]]] = None
) -> Dict[str, ChecklistStatus]:
    """
    Evaluates the complete 5-step checklist for both LONG and SHORT directions at bar current_idx.
    """
    if cached_indicators:
        ema9 = cached_indicators["ema9"]
        ema21 = cached_indicators["ema21"]
        vwap = cached_indicators["vwap"]
        atr = cached_indicators["atr"]
    else:
        ema9 = calculate_ema(closes[:current_idx + 1], params.ema_fast_period)
        ema21 = calculate_ema(closes[:current_idx + 1], params.ema_slow_period)
        vwap = calculate_session_vwap(
            times[:current_idx + 1],
            highs[:current_idx + 1],
            lows[:current_idx + 1],
            closes[:current_idx + 1],
            volumes[:current_idx + 1],
            params.vwap_anchor_hour_utc
        )
        atr = calculate_atr(highs[:current_idx + 1], lows[:current_idx + 1], closes[:current_idx + 1], params.atr_period)

    c_close = closes[current_idx]
    c_open = opens[current_idx]
    c_high = highs[current_idx]
    c_low = lows[current_idx]
    c_time = times[current_idx] if current_idx < len(times) else str(current_idx)
    c_vwap = vwap[current_idx]
    c_ema9 = ema9[current_idx]
    c_ema21 = ema21[current_idx]
    c_atr = max(atr[current_idx], 0.2)

    prev_open = opens[current_idx - 1] if current_idx > 0 else c_open
    prev_high = highs[current_idx - 1] if current_idx > 0 else c_high
    prev_low = lows[current_idx - 1] if current_idx > 0 else c_low
    prev_close = closes[current_idx - 1] if current_idx > 0 else c_close

    active_obs = detect_order_blocks_causal(opens, highs, lows, closes, times, current_idx, params)
    bullish_obs = [ob for ob in active_obs if ob.direction == "BULLISH"]
    bearish_obs = [ob for ob in active_obs if ob.direction == "BEARISH"]

    bars_since_bull_cross = -1
    bars_since_bear_cross = -1
    search_start = max(1, current_idx - params.max_pullback_bars)

    for k in range(current_idx, search_start - 1, -1):
        if ema9[k] > ema21[k] and ema9[k - 1] <= ema21[k - 1]:
            if bars_since_bull_cross == -1:
                bars_since_bull_cross = current_idx - k
        if ema9[k] < ema21[k] and ema9[k - 1] >= ema21[k - 1]:
            if bars_since_bear_cross == -1:
                bars_since_bear_cross = current_idx - k

    # ==================== EVALUATE LONG ====================
    vwap_long_pass = (c_close > c_vwap + params.vwap_min_dist_points)
    vwap_long_detail = f"Close ${c_close:.2f} > VWAP ${c_vwap:.2f} (+${c_close - c_vwap:.2f})" if vwap_long_pass else f"Close ${c_close:.2f} <= VWAP ${c_vwap:.2f} (-${c_vwap - c_close:.2f})"

    cross_long_pass = (bars_since_bull_cross >= 1 and bars_since_bull_cross <= params.max_pullback_bars and ema9[current_idx] >= ema21[current_idx])
    if bars_since_bull_cross == 0:
        cross_long_detail = f"EMA9 crossed EMA21 on THIS bar ({c_ema9:.2f} > {c_ema21:.2f}) - Must wait for pullback"
    elif bars_since_bull_cross > 0:
        cross_long_detail = f"Bullish cross {bars_since_bull_cross} bars ago (EMA9={c_ema9:.2f}, EMA21={c_ema21:.2f})"
    else:
        cross_long_detail = f"No bullish EMA crossover within last {params.max_pullback_bars} bars"

    ob_long_pass = False
    matching_bull_ob = None
    ob_buffer = params.ob_buffer_atr * c_atr
    for ob in bullish_obs:
        if c_low <= ob.zone_high + ob_buffer and c_high >= ob.zone_low - ob_buffer:
            ob_long_pass = True
            matching_bull_ob = ob
            break

    if ob_long_pass and matching_bull_ob:
        ob_long_detail = f"Reacting off Bullish OB #{matching_bull_ob.id} [${matching_bull_ob.zone_low:.2f} - ${matching_bull_ob.zone_high:.2f}]"
    else:
        ob_long_detail = f"No active Bullish Order Block at current price (${c_close:.2f})"

    dist_to_ema9 = abs(c_low - c_ema9)
    dist_to_ema21 = abs(c_low - c_ema21)
    min_ema_dist = min(dist_to_ema9, dist_to_ema21)
    dist_in_atr = min_ema_dist / c_atr

    pullback_long_pass = False
    if cross_long_pass:
        if c_low <= max(c_ema9, c_ema21) + (params.pullback_atr_mult * c_atr) and c_close >= min(c_ema9, c_ema21) - (0.5 * c_atr):
            pullback_long_pass = True
            pullback_long_detail = f"Price pulled back to EMA zone (Low=${c_low:.2f}, EMA9=${c_ema9:.2f}, EMA21=${c_ema21:.2f}, dist={dist_in_atr:.2f} ATR)"
        else:
            pullback_long_detail = f"Price too far from EMA zone ({dist_in_atr:.2f} ATR > {params.pullback_atr_mult:.1f} ATR limit)"
    else:
        pullback_long_detail = "Waiting for valid recent crossover"

    conf_long_pass, pattern_long, conf_long_detail = check_confirmation_candle(
        c_open, c_high, c_low, c_close,
        prev_open, prev_high, prev_low, prev_close,
        c_atr, "LONG", params
    )

    long_all_pass = (vwap_long_pass and cross_long_pass and ob_long_pass and pullback_long_pass and conf_long_pass)

    # Dynamic Strategy SL/TP calculation
    sub_highs_window = highs[:current_idx + 1]
    sub_lows_window = lows[:current_idx + 1]
    sl_long, tp_long, risk_long = calculate_sl_tp("BUY", c_close, sub_highs_window, sub_lows_window, c_atr, params)

    long_status = ChecklistStatus(
        direction="LONG",
        timestamp=c_time,
        bar_index=current_idx,
        close_price=c_close,
        vwap_value=c_vwap,
        vwap_pass=vwap_long_pass,
        vwap_detail=vwap_long_detail,
        ema_fast=c_ema9,
        ema_slow=c_ema21,
        crossover_pass=cross_long_pass,
        bars_since_cross=bars_since_bull_cross,
        crossover_detail=cross_long_detail,
        ob_pass=ob_long_pass,
        active_ob=matching_bull_ob,
        ob_detail=ob_long_detail,
        pullback_pass=pullback_long_pass,
        pullback_dist_atr=dist_in_atr,
        pullback_detail=pullback_long_detail,
        confirmation_pass=conf_long_pass,
        pattern_name=pattern_long,
        confirmation_detail=conf_long_detail,
        all_passed=long_all_pass,
        signal="BUY" if long_all_pass else None,
        suggested_entry=c_close,
        suggested_sl=sl_long,
        suggested_tp=tp_long,
        risk_points=risk_long,
        reward_points=round(tp_long - c_close, 2)
    )

    # ==================== EVALUATE SHORT ====================
    vwap_short_pass = (c_close < c_vwap - params.vwap_min_dist_points)
    vwap_short_detail = f"Close ${c_close:.2f} < VWAP ${c_vwap:.2f} (-${c_vwap - c_close:.2f})" if vwap_short_pass else f"Close ${c_close:.2f} >= VWAP ${c_vwap:.2f} (+${c_close - c_vwap:.2f})"

    cross_short_pass = (bars_since_bear_cross >= 1 and bars_since_bear_cross <= params.max_pullback_bars and ema9[current_idx] <= ema21[current_idx])
    if bars_since_bear_cross == 0:
        cross_short_detail = f"EMA9 crossed below EMA21 on THIS bar ({c_ema9:.2f} < {c_ema21:.2f}) - Must wait for pullback"
    elif bars_since_bear_cross > 0:
        cross_short_detail = f"Bearish cross {bars_since_bear_cross} bars ago (EMA9={c_ema9:.2f}, EMA21={c_ema21:.2f})"
    else:
        cross_short_detail = f"No bearish EMA crossover within last {params.max_pullback_bars} bars"

    ob_short_pass = False
    matching_bear_ob = None
    for ob in bearish_obs:
        if c_high >= ob.zone_low - ob_buffer and c_low <= ob.zone_high + ob_buffer:
            ob_short_pass = True
            matching_bear_ob = ob
            break

    if ob_short_pass and matching_bear_ob:
        ob_short_detail = f"Reacting off Bearish OB #{matching_bear_ob.id} [${matching_bear_ob.zone_low:.2f} - ${matching_bear_ob.zone_high:.2f}]"
    else:
        ob_short_detail = f"No active Bearish Order Block at current price (${c_close:.2f})"

    dist_to_ema9_short = abs(c_high - c_ema9)
    dist_to_ema21_short = abs(c_high - c_ema21)
    min_ema_dist_short = min(dist_to_ema9_short, dist_to_ema21_short)
    dist_in_atr_short = min_ema_dist_short / c_atr

    pullback_short_pass = False
    if cross_short_pass:
        if c_high >= min(c_ema9, c_ema21) - (params.pullback_atr_mult * c_atr) and c_close <= max(c_ema9, c_ema21) + (0.5 * c_atr):
            pullback_short_pass = True
            pullback_short_detail = f"Price pulled back up to EMA zone (High=${c_high:.2f}, EMA9=${c_ema9:.2f}, EMA21=${c_ema21:.2f}, dist={dist_in_atr_short:.2f} ATR)"
        else:
            pullback_short_detail = f"Price too far from EMA zone ({dist_in_atr_short:.2f} ATR > {params.pullback_atr_mult:.1f} ATR limit)"
    else:
        pullback_short_detail = "Waiting for valid recent crossover"

    conf_short_pass, pattern_short, conf_short_detail = check_confirmation_candle(
        c_open, c_high, c_low, c_close,
        prev_open, prev_high, prev_low, prev_close,
        c_atr, "SHORT", params
    )

    short_all_pass = (vwap_short_pass and cross_short_pass and ob_short_pass and pullback_short_pass and conf_short_pass)

    sl_short, tp_short, risk_short = calculate_sl_tp("SELL", c_close, sub_highs_window, sub_lows_window, c_atr, params)

    short_status = ChecklistStatus(
        direction="SHORT",
        timestamp=c_time,
        bar_index=current_idx,
        close_price=c_close,
        vwap_value=c_vwap,
        vwap_pass=vwap_short_pass,
        vwap_detail=vwap_short_detail,
        ema_fast=c_ema9,
        ema_slow=c_ema21,
        crossover_pass=cross_short_pass,
        bars_since_cross=bars_since_bear_cross,
        crossover_detail=cross_short_detail,
        ob_pass=ob_short_pass,
        active_ob=matching_bear_ob,
        ob_detail=ob_short_detail,
        pullback_pass=pullback_short_pass,
        pullback_dist_atr=dist_in_atr_short,
        pullback_detail=pullback_short_detail,
        confirmation_pass=conf_short_pass,
        pattern_name=pattern_short,
        confirmation_detail=conf_short_detail,
        all_passed=short_all_pass,
        signal="SELL" if short_all_pass else None,
        suggested_entry=c_close,
        suggested_sl=sl_short,
        suggested_tp=tp_short,
        risk_points=risk_short,
        reward_points=round(c_close - tp_short, 2)
    )

    return {
        "LONG": long_status,
        "SHORT": short_status
    }


def is_in_killzone(utc_dt: Optional[datetime] = None) -> Tuple[bool, str]:
    """
    Checks if current UTC time falls within institutional high-volume killzones.
    - London Killzone: 07:00 to 11:00 UTC
    - New York Killzone: 12:30 to 17:00 UTC
    Returns (in_killzone, session_name).
    """
    now = utc_dt or datetime.now(timezone.utc)
    hour = now.hour
    minute = now.minute
    total_minutes = hour * 60 + minute

    # London Killzone: 07:00 (420 min) to 11:00 (660 min)
    if 420 <= total_minutes <= 660:
        return True, "London Killzone (High Volatility)"

    # New York Killzone: 12:30 (750 min) to 17:00 (1020 min)
    if 750 <= total_minutes <= 1020:
        return True, "New York Killzone (Peak Momentum)"

    return False, "Off-Hours / Asian Consolidation"


def evaluate_htf_trend(
    htf_closes: List[float],
    period: int = 50
) -> Tuple[str, float, str]:
    """
    Calculates Higher Timeframe (M15 or M5) EMA 50 trend alignment.
    Returns (trend_direction, ema_value, reason).
    trend_direction is "BULLISH", "BEARISH", or "NEUTRAL".
    """
    if not htf_closes or len(htf_closes) < period:
        return "NEUTRAL", 0.0, "Insufficient HTF history"

    ema_htf = calculate_ema(htf_closes, period)
    if not ema_htf:
        return "NEUTRAL", 0.0, "Could not compute HTF EMA"

    curr_close = htf_closes[-1]
    curr_ema = ema_htf[-1]

    # Check slope over last 3 bars for strong momentum
    slope = curr_ema - ema_htf[-3] if len(ema_htf) >= 3 else 0.0

    if curr_close > curr_ema and slope >= 0:
        return "BULLISH", curr_ema, f"HTF Price (${curr_close:.2f}) > EMA{period} (${curr_ema:.2f}) [Uptrend]"
    elif curr_close < curr_ema and slope <= 0:
        return "BEARISH", curr_ema, f"HTF Price (${curr_close:.2f}) < EMA{period} (${curr_ema:.2f}) [Downtrend]"
    elif curr_close > curr_ema:
        return "BULLISH", curr_ema, f"HTF Price (${curr_close:.2f}) > EMA{period} (${curr_ema:.2f})"
    else:
        return "BEARISH", curr_ema, f"HTF Price (${curr_close:.2f}) < EMA{period} (${curr_ema:.2f})"

