# 2026-09-07 — Strategy registry, CRT sweep variants, news filter, one-command launcher

**START HERE if you're picking this up.** This session touched `trading_bot/`
directly, at the owner's explicit and repeated request — the "don't touch bot
code" constraint from the 2026-09-01 handoff **no longer applies**. The owner
asked for a second, structurally different strategy (liquidity-sweep swing
trades) alongside the existing M1 scalper, plus a way to pick between them.

Commits, oldest first: `7e48644` → `bee36bc` → `6c508ef` → `68215a1` →
`0b8b6df` (handoff write-up) → **`d314298` (merge — see its own section
below, read it before touching `run_live_auto_bot.py` or `streamlit_app.py`
again)**. Pushed to `origin/main` — this is now the shared history, not a
local-only branch.

## Addendum — reconciling with a concurrent push (`cfabc94` → `d314298`)

After the work below was committed locally, `git push` was rejected:
`origin/main` had moved. Someone else — `umairamir007`, commit `cfabc94`,
same day — had pushed **"Live-readiness pack: exec-safety guards, CB
persistence, config that sticks."** This was not a simple rebase situation:
that commit fixed **the exact same "sidebar values don't persist / engine
doesn't read them" bug this session also fixed, independently, with an
incompatible design** (one `bot_config` dict row vs. this session's
`param.<strategy>.<key>` / `risk.*` settings-table scheme). It touched the
same four files this session had rewritten:
`run_live_auto_bot.py`, `streamlit_app.py`, `storage.py`, `mt5_bridge.py`.

**A plain `git merge` was attempted, and it did conflict** in
`run_live_auto_bot.py` and `streamlit_app.py` exactly as expected (their diff
was against the *old*, pre-strategy-registry version of both files — pre-loop
structure, `long_st.vwap_pass`-style scalper-only fields that no longer exist
on the generic `Evaluation` object). `storage.py` and `mt5_bridge.py`
auto-merged cleanly — both sides were purely additive there.

Resolution, done by hand rather than trusting the auto-merge:
1. For the two conflicting files, kept **this session's structure** as the
   base (`git checkout --ours`) — it's the more general one (strategy-generic
   loop) and already had test coverage (`test_dashboard_renders.py`,
   `test_settings_persist.py`).
2. Read `cfabc94`'s full diff first, then **manually re-applied every
   genuinely new capability** on top of that base — not merged, ported:
   - `_in_news_blackout()` / `_bar_age_seconds()` helpers, verbatim
   - **circuit-breaker state now survives an engine restart** — consecutive
     losses and the day's P&L are saved to `settings["cb_state"]` after every
     closed trade and restored on startup if it's still the same UTC day.
     Neither this session's work nor the prior 2026-09-01/02 handoffs had this;
     it closes a real hole (a losing streak split across a restart never
     tripped the breaker).
   - max-spread gate and stale-feed gate, as two new shields in the per-bar
     loop, reading `risk.max_spread_usd` / `risk.stale_bar_secs`
   - a **manual** `"HH:MM-HH:MM"` UTC news-window list
     (`risk.news_blackout_windows`) — supplements, does not replace, this
     session's ForexFactory-driven automatic `NewsFilter`
   - per-fill spread/slippage/latency now logged and stored on every trade
     (`MT5Bridge.last_exec`, unchanged from `cfabc94`)
   - the new sidebar knobs (Max Spread, Stale-Feed Halt, Manual News Windows)
     were added via **this session's** `_persisted_number` /
     `_persisted_text_list` pattern, not `cfabc94`'s `bot_config` dict — so the
     merged codebase has **one** settings scheme, not two
   - trade-history R-multiple is now computed live from entry/SL/pnl (the
     stored `pnl_r_multiple` column is 0 outside the backtester)
3. `tests/test_exec_safety.py` (8 cases, from `cfabc94`) merged in and **passes
   unmodified** against the reconciled file — `_in_news_blackout` /
   `_bar_age_seconds` kept their exact names and signatures on purpose so this
   wouldn't need touching.
4. Committed as a real two-parent merge commit (`d314298`), not a rebase or a
   force-push — `git log -1 --format="%P"` on it shows both `0b8b6df` and
   `cfabc94` as parents. **Nothing from either side was discarded.**

**A real bug surfaced while verifying the merge**, not by inspection but by
actually stopping and restarting the stack: `stop_bot.ps1` didn't clear the
SQLite `engine_heartbeat` setting when it killed the engine process. A
heartbeat can read "fresh" (< 20s old) for a few seconds after the process is
actually dead, so running `start_bot.ps1` right after `stop_bot.ps1` saw a
still-fresh heartbeat and skipped restarting the engine — the exact "the merge
is committed but the OLD process is still what's live" trap. Fixed:
`stop_bot.ps1` now explicitly clears the heartbeat whenever it actually stops
the engine. Verified by doing the stop → start cycle twice and confirming the
engine.log timestamp and PID actually changed the second time.

**If you're merging anything into `run_live_auto_bot.py` or `streamlit_app.py`
again**: check `git log --all --oneline -- trading_bot/run_live_auto_bot.py`
first. Both files are now flashpoints — general-purpose (strategy-agnostic)
and actively being extended from more than one direction. A blind merge on
either will conflict; read both diffs in full before resolving, the way this
one was done.

## What exists now that didn't before

### A strategy registry (`trading_bot/strategies/`)

The dashboard, the live engine, and the backtester used to all be wired
directly to the scalper's functions. They now go through a small interface
(`BaseStrategy` / `Evaluation` / `ParamSpec` in `base.py`) so a strategy is a
self-contained module that declares its own tunables and evaluates itself at a
bar. Adding a strategy means adding a file and one line in `__init__.py`.

- **`vwap_ema_scalper.py`** — the original scalper, unwrapped as an adapter.
  **Zero rule changes**, except one: `rr_ratio` default `1.5 → 2.0` (see
  Decisions below — this one's real).
- **`crt_body_soup.py`** — the strategy actually offered in the sidebar. CRT
  (Candle Range Theory) range from the last completed H1/H4 candle + a
  two-candle liquidity trap (a "manipulator" candle whose body closes past the
  boundary, then a "trigger" candle breaking back through its opposite
  extreme). Two-target exit: half off at the range's 50% (TP1), stop to
  breakeven, runner to the opposite side (TP2). Ships three docs' worth of
  variants as parameters: `entry_mode` (confirmed vs. immediate), `target_mode`
  (opposite-side vs. fixed-R), `enable_htf_bias` (H4/H1 EMA trend filter).
- **`crt_tbs.py`** — the *first* CRT attempt (wick sweep, closes back inside
  same candle). **Archived, not in the sidebar** — see Decisions.
- **`base.py`** also has `build_htf_series()`: folds M1 bars into H1/H4/M5/M15
  candles causally (wall-clock buckets, so a data gap yields no candle rather
  than a stitched one). Any future higher-timeframe strategy should use this
  rather than reimplementing candle aggregation.

`backtest.py` is now strategy-agnostic (takes a `strategy=` argument, defaults
to the scalper) and understands TP1 partial closes. `run_backtest.py` and the
new `run_sweep.py` (parameter grid search) both take `--strategy`.

### News filter (`trading_bot/news_filter.py`)

ForexFactory has **no official API and issues no API keys** — the owner found
a paid third-party wrapper and asked about it; the answer is it re-serves FF's
own free public JSON. This module reads that JSON directly
(`https://nfs.faireconomy.media/ff_calendar_thisweek.json`), no key needed.
Findings baked into the code: only `thisweek` is reliably published (last/next
week 404 a lot), the endpoint 429s under repeated hits (has backoff + retry),
and a failed fetch must never overwrite a good cache (merges instead of
replacing). Wired into `run_live_auto_bot.py` as a shield: blocks entries
±30 min around High-impact USD/EUR/GBP releases, refreshes every 6h.

**For the MQL5 EA, this module is irrelevant** — use MT5's native
`CalendarValueHistory()` instead. It is **not exposed in the Python package**
(checked directly: `[a for a in dir(mt5) if 'calendar' in a.lower()]` → empty).

### Risk sizing tied to the live account

`run_live_auto_bot.py` used to hardcode `max_daily_loss_usd=500.0` regardless
of balance — meaningless on a small account (the owner is forward-testing on a
**$100 demo balance**, deliberately small, before committing $100 real).
Circuit breakers now read `10% of live balance` (floored at $5) unless the
dashboard has saved an override, and startup prints a sizing warning when the
strategy's worst-case stop exceeds 3% of equity at the configured lot size —
which it does here: **0.01 lots (broker minimum) × a $12 stop = 12% of a $100
account.** This is not fixable in code; it needs a bigger balance or a broker
with sub-0.01 lots. Said explicitly in the session — see Open Questions.

### Sidebar persistence (the last commit, `68215a1`)

Every sidebar control (strategy params, Max Daily Loss, Max Consec Losses,
Magic Number) now round-trips through the SQLite `settings` table:
`param.<strategy_key>.<param_key>` for strategy tunables,
`risk.max_daily_loss_usd` / `risk.max_consecutive_losses` / `risk.magic_number`
for the safety controls. **Before this commit, only the strategy picker
persisted** — every other control silently reverted to its literal default on
refresh, and the engine never read the dashboard's values at all (it always
traded its own hardcoded numbers regardless of what the sidebar showed).
`run_live_auto_bot.load_strategy_settings()` now resolves
`strategy defaults → LIVE_PARAM_OVERRIDES → saved dashboard settings`, in that
order, and reads its own risk caps the same way.

Verified live, not just by unit test: set a value via the Streamlit
`AppTest` harness, reloaded the page, confirmed the new value survived.

### Dashboard render tests (`test_dashboard_renders.py`)

Three separate runtime crashes shipped in this session before this existed —
an `AttributeError` on a scalper-only param when the sweep strategy was
selected, then a `NameError` inside the auto-refreshing engine-status fragment
(`long_st` used before it was assigned). **All three passed `py_compile`.**
`streamlit.testing.v1.AppTest` now actually renders the dashboard for every
registered strategy, including a rerun (fragments re-execute independently,
which is where the `NameError` was hiding), and this runs with the rest of the
suite. **If you touch `streamlit_app.py`, `py_compile` proves nothing — run
the test suite, or load the page yourself.**

### One-command launcher (`scripts/start_bot.ps1` / `stop_bot.ps1`)

"Start the bot" means three separate programs: MT5 (`terminal64.exe`), the
Streamlit dashboard, and the headless engine, none of which start each other.
`start_bot.ps1` brings up whichever of the three isn't already running (MT5 by
process name, dashboard by its port, engine by its SQLite heartbeat) and is
idempotent — run it twice, nothing double-starts. PIDs land in `.run/`
(gitignored) so `stop_bot.ps1` targets exactly what it started rather than
guessing at process names. **Tested live in this session**, including the
idempotency case (ran it twice back to back, second run touched nothing).

```powershell
./scripts/start_bot.ps1                                  # engine follows the sidebar
./scripts/start_bot.ps1 -Strategy crt_body_soup           # pin a strategy
./scripts/start_bot.ps1 -DashboardPort 8502
./scripts/stop_bot.ps1                                    # leaves MT5 running
./scripts/stop_bot.ps1 -Mt5Too                            # closes MT5 too
```

One gotcha hit and fixed while building this: PowerShell mangles embedded
double-quotes when they cross into a native process's argv (`python -c "..."`
with `"` inside silently loses the quotes). Fixed by writing the Python
one-liners to small throwaway scripts under `.run/` instead of passing them
inline, and setting `$env:PYTHONPATH` so those scripts (living outside the
package tree) can still `import trading_bot`.

## Extensive backtesting — the honest result

The owner asked, repeatedly, whether the sweep strategy actually works before
trusting it. It was tested against **three independent real datasets**:

1. 50,000 broker M1 bars (mid-Jul–Sep, ~35 days) — CRT+TBS wick-sweep passed
   the noise gate on some configs. **This is the one that didn't replicate.**
2. 100,000 broker M1 bars (late-May–Sep, ~3.5 months, pulled via
   `mt5.copy_rates_range` in monthly chunks — `copy_rates_from_pos` maxes out
   around 50k bars regardless of the count requested).
3. 75,402 bars, **January–September 2026**, downloaded from **Dukascopy's free
   public tick feed** (`scripts/fetch_dukascopy_m1.py` — the broker only
   retains ~3 months of M1 server-side; Dukascopy has years, no key needed,
   just rate-limited and needs retry passes to fill in throttled hours).

Finding: nothing held up across all three. A config that passed the noise gate
with PF 1.15–1.93 on one window flipped to PF 0.77–0.94 on another, more than
once (the wick-sweep CRT, an HTF-bias filter, a fixed-R exit experiment all did
this in turn). At ~100–300 trades and PF hovering near 1.0, that's the
signature of overfitting to one window, not a real edge — see
`docs/MT5_BACKTEST_GUIDE.md` for the full tables. Also corrected an earlier
mid-session claim: a 51.5% win rate quoted for `rr_ratio=2.0` came from a tiny
33-trade *synthetic* sample with three other parameters changed at once, not
from the actual real-data test of the change as shipped (which came in at
34.5% win rate, PF 1.05 — still an improvement, just a smaller one than first
reported).

**Consequence for the sidebar:** the wick-sweep `crt_tbs.py` is intentionally
**not** in `REGISTRY` (removed from the dashboard/live-engine picker) — it
tested PF 0.72–0.88 in every configuration tried. It's kept in `ARCHIVED` so
old scripts/settings resolve without crashing, but it will never appear as a
choice again unless someone deliberately re-adds it.

## Decisions worth keeping (candidates for `decisions.md`)

- **`rr_ratio` 1.5 → 2.0 on the scalper.** At 1.5, win rate sat ~1.4 points
  below the break-even threshold implied by spread+commission (~$3.20/trade,
  ~78% of it spread). At 2.0 the same real-data window flipped from −$763 to
  +$720 net. `run_live_auto_bot.py`'s override was independently pinning it
  back to 1.5 — fixed there too, or the dashboard default would've been
  silently overridden in production.
- **Circuit breakers scale to balance, not a fixed dollar figure.** A fixed
  $500 cap is not a safety net on a $100 account.
- **The wick-sweep CRT variant is archived, not deleted.** Every real-data
  backtest of it lost; keeping a known-losing strategy one click from a live
  account is a hazard.
- **News filter has no vendor lock-in and no key to manage** — it's a public
  JSON endpoint. Don't go looking for an API key; there isn't one.
- **MQL5's `CalendarValueHistory` is not in the Python `MetaTrader5` package.**
  Confirmed by direct inspection, not assumption. The Python news filter and
  the MQL5 EA are two separate implementations for this reason.

## Not done yet / open questions for the next session

- **No strategy has a demonstrated edge on real data.** The scalper is
  ~break-even (PF ~1.00–1.05 depending on window); the sweep loses more often
  than it wins across independent windows. The owner's own next step, agreed
  in-session: **forward-test on the $100 demo, not deposit real money yet.**
  Don't let a future session skip this because the code "looks done."
- **$100 is too small for the sweep strategy at the broker's 0.01-lot
  minimum** — one stop can be 12% of the account. This is a sizing problem,
  not a bug; it needs either a bigger demo balance or accepting outsized
  per-trade risk while forward-testing. Flagged loudly at every engine
  startup; not silently fixed.
- **The Dukascopy downloader (`scripts/fetch_dukascopy_m1.py`) needs multiple
  passes to fill in throttled hours** — a single run left March/June/August
  mostly empty; a second run with lower concurrency and more retry passes
  filled the gaps. If you re-run it for a longer window, budget for that.
- **`crt_body_soup.py`'s HTF-bias filter is a coin flip, not a fix** — see the
  backtest section above. Whichever way you set `enable_htf_bias`, expect it
  to look right on one dataset and wrong on another until there's a lot more
  data to test against.
- **The circuit-breaker state is still per-process and in-memory** — this was
  flagged in the 2026-09-01 handoff and remains true; a restart of the engine
  (including via `start_bot.ps1`) resets the daily-loss/consecutive-loss
  counters to zero. Still not persisted.
- **`.run/` PID files don't survive a machine reboot** cleanly if the
  processes get killed out from under the script by something else — `stop_bot
  .ps1` handles "PID not running" gracefully, but there's no supervisor/
  auto-restart if the engine crashes mid-session. Worth a `-Watch` mode later
  if unattended forward-testing runs need it.
