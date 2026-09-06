# VWAP + EMA 9/21 Scalper — XAUUSD (Gold)

## Core Overview & Strategy Logic

| Field | Detail |
|---|---|
| **Strategy Name** | Triple Filter EMA 9/21 + VWAP + Order Block |
| **Market / Asset** | XAUUSD (Gold) |
| **Execution Timeframe** | M1 (signals evaluated on every closed 1-minute bar) |
| **Primary Objective** | Ride short intraday continuation legs after a pullback into value, with a tight structural stop |

---

## The 5-Step Checklist

A trade is taken only when **all five** pass on the same closed M1 bar.

1. **Trend filter (VWAP)** — close above session VWAP for longs, below for shorts. VWAP resets at the configured UTC hour (00:00 by default).
2. **EMA 9/21 crossover** — a cross in the trade direction happened within the last `max_pullback_bars` bars, and the EMAs are still stacked that way. A cross on the current bar does **not** count; the setup needs a pullback first.
3. **Order block reaction** — price is trading inside (± an ATR buffer) an unmitigated, uninvalidated order block formed before a break of structure in the trade direction.
4. **Pullback to EMAs** — the bar reached back into the EMA 9/21 zone, within `pullback_atr_mult` × ATR.
5. **Confirmation candle** — an engulfing candle, a rejection pinbar, or a strong directional expansion closing beyond the previous bar's extreme.

---

## Trade Parameters (Risk Management)

| Parameter | Execution Rule |
|---|---|
| **Entry Point** | Open of the next M1 bar after the 5/5 close (never the signal bar itself) |
| **Stop Loss (SL)** | Recent swing low/high over `sl_lookback_bars`, plus `sl_buffer_atr` × ATR, clamped to the $1.80–$6.00 band |
| **Take Profit (TP)** | Fixed `rr_ratio` multiple of the stop distance (1:1.5 by default) |
| **Profit Shield** | Live engine only: once 60% of the way to TP, the stop moves to lock $2.50 (capped at 40% of the target) |

---

## Operational Filters & Constraints

- **M15 macro trend filter** (`enable_htf_filter`) — M1 longs are skipped while the M15 EMA 50 reads bearish, and vice versa.
- **Killzone filter** (`enable_session_filter`, off by default) — London 07:00–11:00 UTC, New York 12:30–17:00 UTC.
- **Anti-chop guard** — the live engine skips bars where M1 ATR is under $0.45.
- **Post-loss cooldown** — 3 minutes after any losing trade.
- **Single position** — one open trade at a time, no stacking.

---

## Character

Many trades per session, each lasting minutes. Small stops, small targets, and the edge lives or dies on cost control — spread and commission are a large fraction of every winner. Compare with [CRT + TBS](CRT_TBS_Strategy_XAUUSD.md), which trades a handful of times a week and holds for hours.
