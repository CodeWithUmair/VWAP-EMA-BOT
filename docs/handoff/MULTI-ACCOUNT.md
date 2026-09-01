# Running this bot against multiple Exness accounts

Goal (owner, 2026-09-01): run several instances of this bot at once, each on a
different Exness **demo** account.

## The blocker

The Python `MetaTrader5` package talks to **one running MT5 terminal per process**, and
that terminal is logged into **one account**. `run_live_auto_bot.py` as written:

- creates `MT5Bridge(symbol="XAUUSDm")` and calls `.connect()` with **no `path`** →
  `mt5.initialize()` attaches to whatever single terminal Windows finds;
- hardcodes the **symbol** (`XAUUSDm`) and **volume** (0.10);
- creates `BotStorage()` with **no `db_path`** → every instance writes to
  `./trading_bot_data.sqlite` in its working directory.

So "just run it twice" doesn't give you two accounts — both processes hit the same
terminal/account and both write the same SQLite file.

`MT5Bridge.connect(path=...)` *does* accept a terminal path, but nothing passes one,
and it has no `login`/`password`/`server` params.

## Options (owner said don't edit the bot's code — so these work around it)

### A. Sequential — one account at a time (zero new code)

Log the single MT5 terminal into account 1, run the bot, `Ctrl-C`, log into account 2,
run again. Fine for *comparing* accounts or spot-checking; no real concurrency.

### B. One MT5 install + one repo copy per account (recommended for 2–3 accounts)

1. Install MT5 **N times into N separate folders** (the installer's "portable"/custom
   path option, or copy an install folder). Log each into its own Exness demo account,
   enable Allow Algo Trading in each.
2. Make **N copies of this repo folder** (`VWAP-EMA-BOT-acct1`, `-acct2`, …). Each copy
   gets its own `venv/` (or share one) and, because `BotStorage` uses a relative path,
   its **own `trading_bot_data.sqlite`** automatically when run from that folder.
3. In each copy you still need the process to attach to *that account's* terminal. The
   only no-edit way is to launch each MT5 terminal, then start its bot **immediately
   after** so `mt5.initialize()` picks up the most-recently-launched terminal — this is
   racy and not guaranteed. Better: see C.

### C. A thin launcher wrapper (one NEW file — not an edit)

Add `run_account.py` at the repo root that does what `run_live_auto_bot.py` does but
lets you pass a terminal path / login. It **imports** `MT5Bridge`, the strategy
functions, `CircuitBreakerManager`, `BotStorage` — it does not modify them. It
reimplements the ~30-line scan/execute loop (copy it from `run_live_auto_bot.py`) with:

```python
bridge = MT5Bridge(symbol=args.symbol)
mt5.initialize(path=args.mt5_path, login=args.login,
               password=args.password, server=args.server)   # full login, not bridge.connect()
storage = BotStorage(db_path=args.db)                          # per-account SQLite
```

Then run one per account:

```
python run_account.py --mt5-path "C:\MT5\acct1\terminal64.exe" --login 111 --password ... --server Exness-MT5Trial16 --db acct1.sqlite
python run_account.py --mt5-path "C:\MT5\acct2\terminal64.exe" --login 222 --password ... --server Exness-MT5Trial9  --db acct2.sqlite
```

This is the cleanest concurrent setup. It duplicates a small amount of the live loop;
keep the wrapper thin and let the strategy/bridge modules stay the single source of
truth. **Credentials go in a `.env` / CLI, never committed.**

### D. Ask for a 20-line change to `run_live_auto_bot.py` (cleanest, needs owner OK)

Add `argparse` for `--mt5-path`, `--symbol`, `--volume`, `--login/--password/--server`,
`--db`, and thread them into the two constructors. That's the real fix. It's a small,
additive change (no strategy logic touched) — worth asking the owner whether that
counts as "changing the bot" given it makes their multi-account goal actually work.

## Whichever you pick

- **Separate SQLite file per instance** (own working directory, or `--db`). Sharing one
  file across accounts corrupts the trade history.
- **`PYTHONUTF8=1`** on every headless launch (see `README.md`).
- **Different Streamlit port per dashboard** (`--server.port 8502`, `8503`, …).
- **Magic number stays `9212001` for all of them** — that's fine, they're on different
  accounts so there's no collision; only matters if two instances ever share one
  account, which you should never do.
- Watch **margin** if you ever move off demo — N accounts × 0.10 lot is N× the exposure.
