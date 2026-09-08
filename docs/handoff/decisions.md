# Decisions log

Durable "why is it built this way" notes. Read before changing something so you know
whether there's a reason it isn't already that way.

Most entries below are **reverse-engineered** from the code and the upstream `README.md`
(this handoff was started by an outside session that did not write the bot). Where a
reason is stated by the upstream author it's marked *(upstream)*; where it's inferred
it's marked *(inferred)*. Correct these as you learn more.

## Strategy / signal

- **5 filters, all-or-nothing.** *(upstream)* VWAP trend + EMA9/21 crossover + causal
  order block/BOS + pullback-to-EMA + candlestick confirmation. A signal fires only
  when all five pass at bar close; fill is next bar's open + spread. The upstream
  README frames this as strict causality / zero-lookahead, and the unit tests
  (`tests/test_strategy.py`) check the causality of each indicator.
- **The EMA crossover bar is not the entry.** *(upstream)* Price must pull back toward
  the EMAs after the crossover, within `max_pullback_bars` (15 in `StrategyParameters`,
  but **35** as set in `run_live_auto_bot.py`). Rationale given: avoid chasing the
  impulse candle.
- **Order blocks are confirmed with a lag.** *(upstream)* A pivot at bar `k` is only
  visible at `k + ob_swing_lookback` (5). OB stays active `ob_max_age_bars` (40) unless
  invalidated. This is the zero-lookahead guarantee applied to structure.
- **Live params differ from defaults.** *(inferred)* `run_live_auto_bot.py` overrides
  `max_pullback_bars=35`, `ob_buffer_atr=0.35`, `pullback_atr_mult=1.8` with a comment
  "Balanced Strategy Parameters for Realistic M1 Scalping" — i.e. the dataclass
  defaults were found too strict to ever fire live, and these were loosened by hand.
  There's no record of *how* they were chosen; treat them as untuned-by-us.

## Risk / execution

- **Fixed 0.10 lot, every trade.** *(inferred)* Hardcoded in `run_live_auto_bot.py`
  (`volume=0.10`). No position sizing, no risk-per-trade calc. The sibling repo learned
  the hard way that "trade bigger once it looks good" is a real hazard — see
  `scalping_bot/docs/handoff/decisions.md` — so if this ever moves off demo, revisit.
- **RR 1:2, SL behind the last 10 bars, clamped $1–$8.** *(upstream)* The clamp exists
  so the stop can't be tighter than spread absorbs, nor absurdly wide on a spike.
- **Magic number `9212001`.** *(upstream)* Tags every order so the bot never adopts or
  closes manual trades or another system's trades. Distinct from `scalping_bot`'s
  `990101`, so the two bots coexist on one account without fighting over positions.
- **Demo-only, re-checked every order.** *(upstream)* `mt5_bridge.send_order` and
  `circuit_breakers.can_open_trade` both call `account_info().trade_mode` on every
  attempt and hard-refuse a live account. Not a startup check — every order.
- **`bypass_noise_gate_for_demo=True`.** *(inferred)* Both the live runner and the
  dashboard set this. So the "strategy must pass the noise gate before auto-trading is
  unlocked" rule from the upstream README is **off on demo** — the bot trades on demo
  even though its own backtest gate reports FAILED. This looks deliberate (a demo is
  for gathering live behaviour, not for proving edge first), but it means the gate is
  not actually protecting anything in the current setup.

## Infrastructure

- **SQLite, relative path.** *(inferred)* `BotStorage(db_path="trading_bot_data.sqlite")`
  — opened relative to the process's working directory. Run the bot from the repo root
  or the DB will be created somewhere else. A committed `trading_bot_data.sqlite` is in
  the repo (from the upstream author's own runs).
- **MT5 connection takes no account/path.** *(inferred)* `MT5Bridge.connect()` accepts
  an optional `path=` but `run_live_auto_bot.py` calls it with nothing, so
  `mt5.initialize()` attaches to whatever terminal Windows finds. Fine for one account;
  the blocker for multi-account concurrency — see `MULTI-ACCOUNT.md`.
- **Simulation fallback.** *(upstream)* If the `MetaTrader5` package can't import (non-
  Windows), `MT5Bridge` runs a synthetic simulation instead of failing. Handy for CI /
  Linux, but means "it ran" doesn't prove "it talked to a broker" — check the log for
  `Connected to MetaTrader 5 Terminal` vs `Simulation Mode`.

## Strategy registry (2026-09-07)

- **A strategy is a self-contained module implementing `BaseStrategy`**, not a
  function the dashboard/engine/backtester call directly. *(this session)* The
  scalper's logic in `strategy.py` was left untouched; `strategies/vwap_ema_scalper.py`
  is a thin adapter over it. This was chosen specifically so the original,
  already-working scalper never had its actual rules touched while a second
  strategy was added — only its call sites moved.
- **`rr_ratio` 1.5 → 2.0 on the scalper.** *(this session, verified on real data)*
  At 1.5, the win rate on a real 25k-bar broker window (39.0%) sat below the
  break-even rate implied by spread+commission cost (~$3.20/trade, mostly
  spread) and the win/loss payoff ratio (40.4% needed). At 2.0, the required
  win rate drops (bigger wins forgive more losses) and the same window flipped
  from −$763 net to +$720 net. `run_live_auto_bot.py`'s `LIVE_PARAM_OVERRIDES`
  was independently hardcoding 1.5 — that would have silently undone this in
  production if left alone, so it was updated too. Still only ~1 point above
  break-even (34.5% win rate vs. 33.3% needed) — treat as "no longer actively
  losing," not "proven edge."
- **The wick-sweep CRT variant (`crt_tbs.py`) is archived, not deleted, and not
  offered in the sidebar.** *(this session)* Tested at profit factor 0.72–0.88
  across every real-data window and parameter combination tried in this
  session — every one lost money. Kept importable via
  `strategies.ARCHIVED` so old scripts/settings don't crash, but
  `list_strategies()` / the sidebar / the live engine's default resolution
  never surface it. Don't re-add it to `REGISTRY` without a real-data result
  that contradicts the above.
- **CRT+TBS "Body Soup" (`crt_body_soup.py`) targets the CRT range midpoint for
  a 50%-off partial (TP1), then the opposite side for the runner (TP2).**
  *(this session, per the strategy manuals the owner supplied)* An experiment
  swapping TP2 for a fixed-R multiple of the stop improved profit factor
  (0.74 → ~0.81–0.92 depending on entry mode) but never crossed 1.0 — the exit
  was a contributing factor, not the core problem. Kept `target_mode` as a
  parameter (`opposite_side` default, `fixed_r` available) rather than picking
  one, since neither is settled by the data so far.
- **`enable_htf_bias` (H4/H1 EMA trend filter) exists on the sweep but should
  not be assumed to help.** *(this session)* It improved profit factor from
  0.77 to 1.15 on one 100k-bar window and made it worse (0.94 vs. 1.04) on an
  independent 75k-bar window covering different months. This reversal, on top
  of the wick-sweep CRT's own gate-pass-then-fail pattern, is why the session
  file frames *nothing* about the sweep strategy as settled yet.

## Infrastructure (2026-09-07)

- **Sidebar controls persist to SQLite, and the live engine reads them back.**
  *(this session)* Before this, only the strategy picker (`active_strategy`)
  was saved — every other sidebar value (Max Daily Loss, strategy parameters)
  reverted to a hardcoded default on every page refresh, because Streamlit
  rebuilds widgets from scratch on each rerun. Worse, even where a value did
  stick for the session, the headless engine never looked at it — it always
  traded `LIVE_PARAM_OVERRIDES` regardless of what the dashboard showed.
  Fixed by writing every control to `param.<strategy_key>.<param_key>` /
  `risk.*` settings keys on change, and having
  `run_live_auto_bot.load_strategy_settings()` resolve
  `strategy defaults → LIVE_PARAM_OVERRIDES → saved settings` before building
  the strategy's params object.
- **Circuit breakers scale to the live account balance (10%), not a fixed
  dollar figure.** *(this session)* The prior `max_daily_loss_usd=500.0` was
  larger than the entire account on the $100 demo balance being used for
  forward-testing — i.e., no cap in practice. Now computed from
  `account_info().balance` at connect time, floored at $5, and overridable
  from the dashboard (which itself defaults its own control to the same 10%).
- **ForexFactory's calendar has no official API and requires no key.**
  *(this session, verified by fetching it directly)* Paid "Forex Factory API"
  services found online (e.g. a forum thread the owner asked about) are simply
  re-serving FF's own free public JSON
  (`https://nfs.faireconomy.media/ff_calendar_thisweek.json`). Don't go looking
  for credentials to manage for this — there aren't any. Only `thisweek` is
  reliably served (last/next week 404 often); `news_filter.py` handles that as
  a per-week skip, not an error, and merges fetches into the cache rather than
  overwriting it so an empty/failed fetch can't wipe good data.
- **MQL5's `CalendarValueHistory()` is not available to the Python
  `MetaTrader5` package.** *(this session, verified directly:
  `[a for a in dir(mt5) if 'calendar' in a.lower()]` → empty list)* The Python
  news filter and the `.mq5` Expert Advisor are two separate implementations
  for this reason — don't try to share one calendar source between them.
- **`py_compile` passing does not mean the dashboard runs.** *(this session,
  the hard way — three separate runtime crashes shipped in a row, each past
  `py_compile`)* `streamlit_app.py` is a script whose failures are almost all
  runtime ones (wrong attribute on the wrong strategy's params object, a
  variable read before assignment inside an auto-refreshing fragment).
  `test_dashboard_renders.py` now actually renders the app via
  `streamlit.testing.v1.AppTest`, for every registered strategy, including a
  rerun (fragments re-execute independently on rerun, which is where one bug
  hid). Any future change to `streamlit_app.py` should be checked against this
  test, not just compiled.
- **The one-command launcher (`scripts/start_bot.ps1`) checks real state,
  never assumes it.** *(this session)* MT5 by process name, the dashboard by
  whether its port is actually listening, the engine by whether its SQLite
  heartbeat is current (< 20s old) — not by "did I launch it earlier in this
  script." This is what makes it safe to run twice. A PowerShell-specific
  gotcha hit while building it: passing a Python one-liner via
  `python -c "..."` loses embedded double-quotes when PowerShell hands the
  string to a native process's argv. Fixed by writing the one-liners to small
  files under `.run/` and invoking those instead.

## This handoff session's own choices (2026-09-01)

- **Added `docs/handoff/` only.** No bot code touched — the owner's explicit constraint.
- **`PYTHONUTF8=1` documented, not patched.** The headless runner crashes on its emoji
  banner when stdout isn't a console. The fix in the owner's other repo for the same
  class of bug was a code change; here the code is off-limits, so it's an env var.
- **Own `venv/`.** Kept separate from `scalping_bot` (system Python) so dependency
  versions (pandas 3.x, numpy 2.x, MetaTrader5 5.0.6147) don't collide.
