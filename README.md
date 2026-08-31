# 🏆 XAU/USD Triple Filter EMA 9/21 + VWAP Scalping Bot (MetaTrader 5 & Streamlit)

A local, high-frequency scalping trading bot built for **XAU/USD (Gold)** on the **1-minute (M1) timeframe**, implementing strict causality, zero-lookahead backtesting, Monte Carlo noise-control statistical significance gating, and MetaTrader 5 live execution guardrails.

---

## 📖 Strategy Architecture & Rule Decisions

The strategy implements a 5-step causal sequence. Every single ambiguous rule has been formalized into a causal mathematical model with named, tunable parameters.

### 1. Trend Filter: Session-Anchored VWAP
- **Definition:** Volume-Weighted Average Price anchored to **00:00 UTC daily open** (`vwap_anchor_hour_utc = 0`).
- **Reasoning:** Since Gold trades 23 hours a day with no single physical bell, 00:00 UTC provides the standard institutional daily rollover boundary. Tunable to London (`07:00 UTC`) or NY (`13:00 UTC`).
- **Rule:** For LONG, `Close > VWAP`. For SHORT, `Close < VWAP`.

### 2. Moving Average Crossover Filter: EMA(9) & EMA(21)
- **Fast EMA:** 9-period Exponential Moving Average (`ema_fast_period = 9`).
- **Slow EMA:** 21-period Exponential Moving Average (`ema_slow_period = 21`).
- **Rule:** EMA9 must have crossed above EMA21 within the last `max_pullback_bars` (default: 15 bars).
- **Causality Constraint:** The crossover bar itself is **NOT** the entry trigger. Price must pull back after the crossover before an entry is permitted.

### 3. Structural Confirmation: Causal Order Blocks & Break of Structure (BOS)
- **Break of Structure (BOS) Definition:** A candle close that strictly penetrates the most recent confirmed Swing High (for Bullish BOS) or Swing Low (for Bearish BOS).
- **Causal Swing Definition:** A pivot high/low at bar $k$ is identified using an $N$-bar pivot lookback (`ob_swing_lookback = 5`). Pivot at bar $k$ is only confirmed and visible at bar $k + 5$ (zero future lookahead).
- **Order Block (OB) Definition:** 
  - **Bullish Order Block:** The last down-close candle ($\text{Close} < \text{Open}$) before the impulsive leg that caused the Bullish BOS.
  - **Bearish Order Block:** The last up-close candle ($\text{Close} > \text{Open}$) before the impulsive leg that caused the Bearish BOS.
- **Zone & Mitigation:** The OB zone spans `[OB.low, OB.high]`. An OB remains active for up to `ob_max_age_bars = 40` bars unless price closes through its invalidation boundary.
- **Rule:** Price must be currently testing or reacting off an active Order Block zone ($[\text{zone\_low} - \text{buffer}, \text{zone\_high} + \text{buffer}]$ where buffer is $0.2 \times \text{ATR}$).

### 4. Pullback toward the EMAs
- **Maximum Wait Limit:** `max_pullback_bars = 15`. If no setup confirms within 15 bars of the crossover, the setup expires.
- **Proximity Threshold:** Price low (for LONG) or high (for SHORT) must be within `pullback_atr_mult * ATR(14)` of either EMA9 or EMA21 (`pullback_atr_mult = 1.0`).

### 5. Entry Trigger: Confirmation Candlestick Patterns
Textbook candlestick recognition evaluated causally at bar close:
1. **Bullish / Bearish Engulfing:** The body of the trigger candle completely engulfs the prior candle's body in the direction of the trend with body size $\ge 0.35 \times \text{ATR}$.
2. **Hammer / Shooting Star (Pinbar Rejection):** Rejection shadow $\ge 2.0 \times \text{body}$ and opposite nose $\le 0.5 \times \text{shadow}$, closing in the upper 33% (Hammer) or lower 33% (Shooting Star) of total range.
3. **Strong Directional Expansion:** Bullish close exceeding prior bar high or bearish close below prior bar low with body $\ge 0.35 \times \text{ATR}$.

---

## 🎯 Exit & Risk Management

- **Stop Loss (SL):** Placed causally behind the lowest low of the last `sl_lookback_bars = 10` minus `sl_buffer_atr * ATR(14)` for Longs (mirrored for Shorts). Clamped between \$1.00 min distance (to absorb spread) and \$8.00 max distance.
- **Take Profit (TP):** Fixed Risk:Reward multiple:
  $$\text{TP} = \text{Entry} \pm (\text{Risk Distance}) \times \text{rr\_ratio} \quad (\text{default } 1:2 \text{ R:R})$$

---

## 🧪 Backtest Engine & Noise-Control Gate

### Zero-Lookahead Guarantees
1. **Signal at Bar $i$ $\rightarrow$ Execution at Bar $i+1$ Open:** A signal calculated at the close of bar $i$ fills strictly at the opening tick of bar $i+1$ plus broker spread.
2. **Wick-Based SL/TP Checks:** Subsequent bars evaluate stop-loss and take-profit hits using high/low wicks, including broker spread adjustments.
3. **Broker Costs:** Real bid-ask spread (\$0.25 default on Gold) and \$7.00 round-turn commission per standard lot.
4. **Data Splitting:** Historical data is split into **In-Sample (75%)** and **Out-of-Sample (25%)** sets.

### Monte Carlo Noise-Control Statistical Significance Test
- The backtest reshuffles the trade sequence $N = 100+$ times under the null hypothesis of zero edge.
- Computes empirical p-value:
  $$p = \frac{\sum [\text{Expectancy}_{\text{shuffled}} \ge \text{Expectancy}_{\text{real}}] + 1}{N + 1}$$
- **Gate Requirement:** Requires $p \le 0.05$, positive expectancy in both In-Sample and Out-of-Sample, and minimum 20 trades. If this gate fails, automated trading is **strictly locked** in the dashboard.

---

## 🛡️ Safety Guardrails & Live Execution

1. **Strict Demo-Only Enforcement:** On **EVERY single order attempt**, queries `account_info().trade_mode == ACCOUNT_TRADE_MODE_DEMO`. Live accounts are hard-rejected.
2. **Terminal AlgoTrading Check:** Verifies `terminal_info().trade_allowed == True`.
3. **Unique Magic Number (`9212001`):** Tags every order so the bot never interferes with manual trades or other systems.
4. **Daily Loss Circuit Breaker:** Pauses auto-trading if daily loss exceeds \$200 or 3% of starting balance until next UTC day.
5. **Consecutive Losses Circuit Breaker:** Pauses auto-trading if 3 consecutive losses occur until manually reviewed.
6. **SQLite Persistence:** All trade history, logs, and settings are saved to `trading_bot_data.sqlite`.

---

## 🚀 Installation & Local Running (MetaTrader 5 on Windows)

### 1. Prerequisites
- Windows 10/11 with **MetaTrader 5 Terminal** installed and logged into a **Demo Account**.
- Python 3.9+ (64-bit).
- Enable **"Allow Algo Trading"** in MT5: `Tools -> Options -> Expert Advisors -> Allow automated trading`.

### 2. Setup Virtual Environment
```bash
# Clone or navigate to the directory
cd trading_bot_app

# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install requirements
pip install -r requirements.txt
```

### 3. Run Unit Tests
```bash
python trading_bot/run_tests.py
```

### 4. Run Backtest & Verify Noise Gate
```bash
python trading_bot/run_backtest.py
```

### 5. Launch Streamlit Live Dashboard
```bash
streamlit run trading_bot/streamlit_app.py
```

---

## 📊 Directory Structure
```
├── README.md                      # Complete documentation & math specs
├── requirements.txt               # Python dependencies
├── trading_bot/
│   ├── __init__.py                # Package root
│   ├── strategy.py                # Indicators, Order Blocks, 5-Step Checklist
│   ├── backtest.py                # Causal backtest engine & metrics
│   ├── circuit_breakers.py        # Daily loss & demo guardrails
│   ├── mt5_bridge.py              # MT5 terminal connector & simulation fallback
│   ├── storage.py                 # SQLite persistence layer
│   ├── data_feed.py               # Gold market simulator & data loader
│   ├── run_tests.py               # Unit test runner
│   ├── run_backtest.py            # CLI backtest runner & gate output
│   ├── streamlit_app.py           # Streamlit desktop live dashboard
│   └── tests/                     # Hand-crafted unit tests
│       ├── test_strategy.py       # Indicator & pattern unit tests
│       ├── test_backtest.py       # Causal execution & noise gate tests
│       └── test_circuit_breakers.py # Safety & guardrail tests
└── server.ts                      # Web preview server for AI Studio
```
