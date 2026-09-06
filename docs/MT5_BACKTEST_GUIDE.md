# Backtesting in MetaTrader 5 — step by step

Two ways to test a strategy on your machine, and they answer different questions:

| | Python backtester (this repo) | MT5 Strategy Tester |
|---|---|---|
| Runs | `python -m trading_bot.run_backtest` | The `.mq5` EA in `mql5/` |
| Data | M1 bars pulled from your terminal | The broker's own tick/M1 history |
| Fills | Next M1 bar open, fixed spread | Real historical spread, tick-by-tick |
| Gives you | IS/OOS split, expectancy in R, Monte-Carlo noise gate | Equity curve, drawdown, MT5's own report |
| Best for | Is there an edge at all? | Does it survive real spread and execution? |

Run the Python one first — it is faster and it tells you whether the idea is
worth testing properly. Then confirm in MT5.

---

## Part 1 — Python backtest (60 seconds)

Your MT5 terminal must be **open and logged in**; that is where the M1 history
comes from.

```bash
# CRT + TBS on real history from the terminal
python -m trading_bot.run_backtest --strategy crt_tbs --real --bars 50000

# The original scalper, same data, for comparison
python -m trading_bot.run_backtest --strategy vwap_ema_scalper --real --bars 50000

# From a CSV export instead (see Part 2, step 1 for how to make one)
python -m trading_bot.run_backtest --strategy crt_tbs --csv data/XAUUSD_M1.csv
```

`--bars` is a ceiling, not a promise: MT5 only serves what the terminal has
cached. If you get far fewer bars than you asked for, do step 1 below to make it
download more.

The same backtest is in the dashboard under **📊 Causal Backtest & Noise Gate**,
where "Price data → Live MT5 history (real)" runs it on real bars with whatever
the sidebar sliders are currently set to.

To scan many parameter combinations at once:

```bash
python -m trading_bot.run_sweep --strategy crt_tbs --real --bars 50000
```

---

## Part 2 — MT5 Strategy Tester

### 1. Give MT5 enough history

The tester can only test what the terminal has downloaded.

1. **Tools → Options → Charts** → set *Max bars in chart* to `Unlimited`.
2. Open a chart for your gold symbol (yours is **XAUUSDm**) on **M1**.
3. Press **Home** and hold it — keep scrolling left until the chart stops
   loading older candles. That pulls the history down.
4. Optional but useful: **File → Save As** on that chart to get a CSV you can
   feed to `--csv`, which also makes the Python run reproducible.

Note that most brokers only keep a year or two of M1 gold, and some keep far
less. If you want a longer test, download M1 history from a data vendor and
import it via **Tools → History Center** (or just use the CSV path above).

### 2. Install the Expert Advisor

1. In MT5: **File → Open Data Folder**.
2. Navigate into `MQL5\Experts\`.
3. Copy **`mql5/CRT_TBS_XAUUSD.mq5`** from this repo into that folder.
4. Back in MT5, open **MetaEditor** (F4), find `CRT_TBS_XAUUSD.mq5` in the
   Navigator, and press **Compile** (F7). You want `0 errors, 0 warnings`.
5. Return to the terminal. The EA now shows up under **Navigator → Expert
   Advisors**. If it doesn't, right-click Navigator → Refresh.

### 3. Run the test

1. Open the tester: **View → Strategy Tester** (Ctrl+R).
2. Fill in the **Settings** tab:

   | Field | Value |
   |---|---|
   | Expert | `CRT_TBS_XAUUSD` |
   | Symbol | `XAUUSDm` (your broker's gold symbol) |
   | Period | **M5** — the execution timeframe |
   | Modelling | **Every tick based on real ticks** (most honest), or *1 minute OHLC* for a fast first pass |
   | Date | a range your history actually covers |
   | Forward | **1/4** — holds back the last quarter as an out-of-sample check |
   | Deposit | e.g. 10000 USD |
   | Leverage | match your live account |

3. **Inputs** tab — the defaults mirror the Python strategy. The ones worth
   touching first:

   | Input | Default | What it does |
   |---|---|---|
   | `InpReferenceTF` | H1 | Candle whose High/Low is the CRT range |
   | `InpExecutionTF` | M5 | Candle the sweep-and-reject is judged on |
   | `InpMinSweepPoints` | 0.30 | How far past the level price must poke |
   | `InpSLBufferPoints` | 1.20 | Stop distance beyond the sweep wick |
   | `InpUseKillzones` | true | Restrict to London / NY windows |
   | `InpLots` | 0.10 | Needs to be ≥ 2× your broker's minimum for TP1 partials |

4. Press **Start**. Read the **Backtest** tab for the report and the **Graph**
   tab for the equity curve.

### 4. Reading the result honestly

- **Modelling quality** under 90% on a tick run means the data is thin — the
  result is decoration, not evidence.
- The **Forward** section is the number that matters. A strategy that only
  works in the optimisation window found a pattern in noise.
- **Profit Factor** under ~1.2 with fewer than ~100 trades is not a signal
  either way; it is a sample too small to read.
- Watch the **spread** column in the trade log. Gold spreads widen hard around
  news, which is exactly when sweeps happen.

### 5. Optimising (optional, and dangerous)

The tester's **Optimization** dropdown will happily search the input space and
hand you a beautiful curve that means nothing. If you use it:

- Optimise on the in-sample period, judge only on the forward period.
- Look for a *plateau* of nearby parameter values that all work, not a single
  spike. A spike is a fit to noise.
- Change one or two inputs at a time, not eight.

---

## Part 3 — What our own backtest found

### The headline: 8 months says no

Tested on **75,402 real M1 bars, January–September 2026** (Dukascopy tick data,
79 trading days sampled across all eight months — your broker only retains ~3
months, so this came from `scripts/fetch_dukascopy_m1.py`):

| Configuration | Trades | Win rate | Profit factor | Expectancy | Net P&L | Max DD | Noise gate |
|---|---|---|---|---|---|---|---|
| CRT+TBS spec defaults (H1/M5, killzones on, TP1 on) | 234 | 41.5% | 0.88 | −0.046R | −$1,077 | 14.7% | fail |
| CRT+TBS tuned (min sweep $1.00, killzone off, TP1 off) | 495 | 25.9% | 0.86 | −0.072R | −$3,637 | 54.1% | fail |
| CRT+TBS tuned + TP1 partial (wide SL, killzone off) | 464 | 47.2% | 0.89 | −0.032R | −$2,076 | 30.7% | fail |
| **VWAP+EMA scalper, defaults** (for comparison) | 1,508 | 37.2% | 0.85 | −0.105R | −$7,356 | 78.1% | fail |

**Every configuration loses money and every one fails the noise gate — and the
existing scalper does worse than any of them**, losing more than twice as much
per trade with a 78% drawdown that would have ended the account.

One caveat on that comparison: this dataset has day-sized holes (see coverage
above), and an M1 scalper is hurt more by them than CRT+TBS is. The scalper
reads EMA crossovers and session VWAP bar-to-bar, so a gap corrupts its state
directly; CRT+TBS only reads completed H1 candles and skips a setup when the
reference is stale. On the broker's own gap-free May–September bars the scalper
came out flat (PF 1.00, +$2 over 1,720 trades) rather than badly negative — so
treat its −$7,356 as a pessimistic bound, and "roughly break-even before costs"
as the fairer read. Neither strategy shows an edge either way.

The important part is *why* this differs from the short-window test below. The
"tuned" configuration scored **+0.09R with a 1.13 profit factor** on the
July–September window alone, and cleared the noise gate on every split. Over
the full eight months the same settings produce **−0.072R, profit factor 0.86,
and a 54% drawdown.** That is not a small discrepancy — it is a sign reversal.

The July–September result was a fit to one regime, discovered by searching 64
configurations against 35 days of data. Extending the window destroyed it. This
is exactly the failure mode out-of-sample testing exists to catch, and it is
worth remembering the next time any configuration looks good on a short sample.

### Cross-check: the broker's own May–September bars

A third window, run on 100,000 continuous XAUUSDm M1 bars straight from the
terminal (no gaps, so this is the cleanest execution data of the three):

| Configuration | Trades | Win rate | Profit factor | Expectancy | Net P&L | Noise gate |
|---|---|---|---|---|---|---|
| CRT+TBS spec defaults | 345 | 40.3% | 0.77 | −0.152R | −$2,796 | fail |
| CRT+TBS tuned | 632 | 26.9% | 0.94 | −0.017R | −$1,571 | fail |
| VWAP+EMA scalper defaults | 1,720 | 41.2% | 1.00 | −0.014R | +$2.21 | fail |

Three independent windows now agree: CRT + TBS loses, and the scalper is flat.
Note the tuned CRT row's *out-of-sample* slice passes the gate here (+0.193R,
PF 1.22) — but that slice is precisely the July–September stretch that produced
the flattering result below. The same favourable regime keeps showing up
wherever it lands in a split, which is the clearest possible sign it is a
property of that stretch of market and not of the strategy.

### The earlier short-window test (kept for contrast)

Run on **50,000 real M1 bars of XAUUSDm** (about 35 trading days,
mid-July to early September 2026), 0.1 lots, $0.25 spread, $7/lot commission,
500-shuffle Monte-Carlo noise gate:

| Configuration | Trades | Win rate | Profit factor | Expectancy | Net P&L | Noise gate (overall / OOS) |
|---|---|---|---|---|---|---|
| CRT + TBS, spec defaults (H1/M5, killzones on, TP1 on) | 150 | 42.0% | 0.78 | −0.14R | −$1,088 | fail / **pass** |
| CRT + TBS, min sweep $1.00, killzones off, TP1 off | 294 | 29.2% | 1.13 | +0.09R | +$1,501 | **pass** / **pass** |
| CRT + TBS, min sweep $1.00, killzones off, TP1 on, wide SL | 288 | 51.0% | 1.11 | +0.04R | +$994 | **pass** / **pass** |
| VWAP + EMA scalper, dashboard defaults | 855 | 42.1% | 1.04 | +0.01R | +$902 | pass / fail |

An initial quick pass (1-shuffle sweep across 64 configurations, used only to
find candidates) had suggested the spec defaults lose money and only a
minority of configurations look promising — that quick pass understates the
result, because 1 shuffle can't compute a meaningful p-value. Re-running the
top candidates through the full 500-shuffle gate tells a better story:

1. **The spec defaults (H1/M5, killzones on) lose money overall on this
   window, but their out-of-sample slice alone passes the noise gate**
   (p = 0.002, z = +2.7) — 33 trades, small net loss, but not distinguishable
   from a real (if weak) edge. Too few trades to trust on their own.
2. **Requiring a deeper sweep and dropping the killzone filter clearly
   helps.** Both configurations with `min_sweep_points = $1.00` and
   killzones off pass the noise gate on **both** the in-sample, out-of-sample,
   and overall splits — the strongest pattern in the data. `z ≈ +8` to `+10`
   on close to 300 trades is a real, if modest, statistical result on this
   35-day window.
3. **H4 as the reference candle lost money in every one of 32 variants
   tried** in the earlier sweep — its range is wide enough that TP2 is rarely
   reached before the stop. H1 is the timeframe worth trading.
4. **The scalper's baseline configuration passes the gate overall but fails
   out-of-sample** (E = −0.02R on the last quarter) — a reminder that
   "passed the gate" on the full dataset is not the same as "still worked on
   the most recent data," which is exactly why the OOS split exists.

**Honest summary (superseded by the 8-month test above):** on this one 35-day
window, CRT + TBS with a $1.00 minimum sweep depth and the killzone filter off
was the only configuration clearing the noise gate on every split. Extending
the test to January-September 2026 reversed that result completely - the same
settings lose money over eight months. Treat this section as a worked example
of how convincing a curve-fit can look, not as a recommendation.

- **Get more data.** 35 days is one regime. Pull 6–12 months of M1 through the
  CSV path and re-run `python -m trading_bot.run_sweep --strategy crt_tbs`. If
  the $1.00-sweep, no-killzone configuration keeps clearing the gate across
  several regimes, that is real evidence.
- **Demo it.** The dashboard runs it live on a demo account with full circuit
  breakers. Sweep setups are rarer than scalper setups, so give it weeks, not
  days, before judging.
- **Reconsider the killzone filter.** It's in the spec for a reason (liquidity
  is genuinely thinner outside London/NY), but on this sample it cost trades
  without buying anything. Worth testing on the larger dataset rather than
  trusting either result yet.

---

## Troubleshooting

**"MT5 returned no M1 bars"** — the terminal is closed, not logged in, or has
not downloaded that far back. Do Part 2 step 1.

**EA compiles but never trades in the tester** — check the Journal tab. Common
causes: the execution timeframe is not smaller than the reference timeframe;
`InpMinRangePoints` is filtering out every reference candle; or the killzone
windows do not match your broker's server clock (see the next item).

**Killzones look shifted** — MT5 hands the EA *server* time, and most gold
brokers run GMT+2 or GMT+3. The EA measures the offset from the live terminal
once and applies it, but in a pure tester run that measurement can be off. If
the trade times look wrong, set `InpLondonStartMin` / `InpNYStartMin` etc. in
your **server's** clock instead of UTC.

**Python and MT5 disagree on trade count** — expected, and informative. The
Python engine fills at the next M1 open with a fixed spread; MT5 fills on real
ticks with the historical spread. Large divergence usually means the setups are
clustering around news, when spreads blow out.
