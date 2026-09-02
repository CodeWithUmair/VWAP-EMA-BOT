# Migrating `scalping_bot` from Postgres → SQLite

The full proposal lives in the sibling repo:
**`d:/mine/Bots/scalping_bot/docs/sqlite-migration-proposal.md`**
(added 2026-09-02). This file is just the pointer + the short version.

## Short version

**This repo (`VWAP-EMA-BOT`) is the existence proof.** It persists everything —
trades, settings, logs — in a single committed `trading_bot_data.sqlite` via
`trading_bot/storage.py`, using only Python's stdlib `sqlite3`. No service to
install, no `docker compose up`, no `PGPASSWORD`. Moving it to another machine is a
file copy.

`scalping_bot` uses a local Postgres 18, and `start_bot.ps1` assumes one is already
running (it never starts it). That's the blocker for handing the bot to a friend or
running it on a second machine.

**Verdict: worth doing.** ~80% of `scalping_bot/db.py` ports mechanically
(`JSONB`→TEXT+json, `ON CONFLICT` is the same syntax, cursors→`sqlite3.Row`). The
~20% that needs real care:

- **`db.order_placement_lock()`** — a Postgres advisory lock that stops two
  processes double-placing an order on one signal (it happened live once, stacked
  risk 2-3×). SQLite replacement is a `BEGIN IMMEDIATE` single-writer transaction;
  its two-process mutual-exclusion test must pass before shipping.
- `ensure_schema()`'s advisory lock, the `trades_reject_excluded` PL/pgSQL trigger
  (→ `CREATE TRIGGER ... SELECT RAISE(IGNORE)`), `TIMESTAMPTZ` (→ ISO-8601 text,
  parsed on read — `db.stats()` needs real `datetime`s), `NUMERIC` (→ `REAL`), and
  deleting the whole `DATABASE_URL`/`PG*`/Neon multi-machine path.
- Set `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000` on every connection —
  two processes (`app.py` + `trader.py`) share the file.

**Effort:** ~one focused day, most of it on the lock and rewriting the ~7
Postgres-coupled tests in `tests/test_db.py`. Persistence-only — no strategy,
execution, or MT5 code changes. Every `db.*` signature stays identical so callers
are untouched.

See the full doc for the feature-by-feature table, the lock code sketch, the
one-time `pg_to_sqlite.py` data-migration script, the file-by-file change list, and
the ordered execution plan.
