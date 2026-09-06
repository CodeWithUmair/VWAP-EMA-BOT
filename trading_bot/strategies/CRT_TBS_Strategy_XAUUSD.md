# CRT + TBS Strategy — XAUUSD (Gold)

## Core Overview & Strategy Logic

| Field | Detail |
|---|---|
| **Strategy Name** | CRT + TBS (Candle Range Theory + Turtle Soup) |
| **Market / Asset** | XAUUSD (Gold) |
| **Underlying Methodology** | Smart Money Concepts (SMC) & ICT Liquidity Trading |
| **Primary Objective** | Capitalize on liquidity sweeps (fakeouts/stop hunts) at key candle boundaries during high-volatility trading sessions |

---

## System Definitions

**CRT (Candle Range Theory):**
Defines the structural boundary by marking the absolute High and Low of a reference candle on a higher timeframe (e.g., 1-Hour or 4-Hour).
- The area **above** the High represents **Buy-Side Liquidity (BSL)**
- The area **below** the Low represents **Sell-Side Liquidity (SSL)**

**TBS (Turtle Soup Pattern):**
The mechanical entry protocol triggered when price temporarily breaches a CRT boundary to gather liquidity, fails to sustain momentum, and aggressively closes back inside the defined CRT range.

---

## Step-by-Step Execution Protocol

### Phase 1: Higher Timeframe Setup (CRT Identification)

1. Open the XAUUSD chart on the **H1 or H4** timeframe.
2. Identify the most recently completed candle.
3. Draw horizontal levels at:
   - **CRT High** → BSL Target (Buy Stops)
   - **CRT Low** → SSL Target (Sell Stops)
   - **CRT Equilibrium** → 50% midpoint of the candle range

### Phase 2: Lower Timeframe Execution (TBS Trigger)

1. Drop to a lower timeframe (**M5 or M15**).
2. Monitor price action as it approaches the CRT High or CRT Low during key trading windows.

**Condition for Short (Sell Setup):**
- Price spikes above the CRT High (sweeping BSL).
- The M5/M15 candle fails to hold above the level and closes **below** the CRT High, back into the range.

**Condition for Long (Buy Setup):**
- Price spikes below the CRT Low (sweeping SSL).
- The M5/M15 candle fails to hold below the level and closes **above** the CRT Low, back into the range.

---

## Trade Parameters (Risk Management)

| Parameter | Execution Rule |
|---|---|
| **Entry Point** | Open of the immediate next candle following the confirmed M5/M15 range-reentry close |
| **Stop Loss (SL)** | Place SL 10–15 pips beyond the extreme high/low of the sweep wick |
| **Take Profit 1 (TP1)** | 50% Midpoint of the reference CRT Candle Range (partial profits / move SL to breakeven) |
| **Take Profit 2 (TP2)** | The opposing side of the CRT Range (targeting the CRT Low for Short, CRT High for Long) |

---

## Operational Filters & Constraints

**Optimal Trading Hours (Kill Zones):**
- London Session — 07:00 – 10:00 UTC
- New York Session — 12:30 – 16:00 UTC

**Invalidation Criteria:**
- If a lower timeframe candle closes and consolidates outside the CRT boundary without immediate rejection, the setup is invalidated.

**Do Not Trade:**
- 30 minutes before or after high-impact economic releases (e.g., CPI, NFP, FOMC).
