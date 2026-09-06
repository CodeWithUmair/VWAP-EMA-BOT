"""
Download real XAU/USD M1 history from Dukascopy's free public tick feed.

Your broker only keeps a few months of M1 bars on the server (Exness demo cuts
off around 3 months back), so a longer backtest needs a different source.
Dukascopy publishes raw tick data going back years, for free, no key required.

This pulls hourly tick files, folds them into M1 OHLCV bars, and writes a CSV
that ``trading_bot.run_backtest --csv`` reads directly.

    python scripts/fetch_dukascopy_m1.py --from 2026-01-01 --to 2026-09-06 \
        --out data/XAUUSD_M1_2026.csv

Notes on the format: each ``.bi5`` file is LZMA-compressed, holding 20-byte tick
records of (ms offset into the hour, ask, bid, ask volume, bid volume). Prices
are integers scaled by 1000 for gold. Missing hours (weekends, holidays) return
404 or an empty body and are simply skipped.
"""

import argparse
import csv
import lzma
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

BASE_URL = "https://datafeed.dukascopy.com/datafeed/{symbol}/{y:04d}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"
TICK_STRUCT = struct.Struct(">3I2f")   # ms offset, ask, bid, ask vol, bid vol

# Dukascopy stores prices as integers; gold quotes carry 3 decimals.
POINT_DIVISOR = {"XAUUSD": 1000.0}


class HourResult:
    """Outcome of one hourly fetch: the ticks, and whether the hour is genuinely empty.

    The distinction matters. A 404 means Dukascopy has no session that hour
    (weekend, holiday) and re-requesting it forever is pointless. A timeout or a
    throttle response means we simply failed, and those hours must be retried or
    the dataset ends up with month-sized holes that look like real market gaps.
    """

    __slots__ = ("blob", "definite")

    def __init__(self, blob, definite):
        self.blob = blob
        self.definite = definite   # True = authoritative answer (data or a real 404)


def fetch_hour(symbol, when, retries=4):
    """Fetch one hour of ticks. Returns an :class:`HourResult`."""
    url = BASE_URL.format(symbol=symbol, y=when.year, m=when.month - 1,
                          d=when.day, h=when.hour)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                raw = response.read()
            if not raw:
                return HourResult(b"", True)      # served, but no ticks that hour
            # Dukascopy writes LZMA-alone streams with no known output size.
            return HourResult(
                lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(raw), True)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):            # no session that hour
                return HourResult(b"", True)
            time.sleep(1.5 * (attempt + 1))       # throttled: back off and retry
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return HourResult(b"", False)                 # gave up - caller should retry later


def ticks_to_minutes(blob, hour_start, divisor):
    """Fold one hour of ticks into {minute_datetime: [o, h, l, c, tick_count]}."""
    minutes = {}
    for offset in range(0, len(blob) - len(blob) % TICK_STRUCT.size, TICK_STRUCT.size):
        ms, ask_i, bid_i, _, _ = TICK_STRUCT.unpack_from(blob, offset)
        # Bid is what MT5 charts plot, so bars line up with the terminal.
        price = bid_i / divisor
        if price <= 0:
            continue
        stamp = hour_start + timedelta(milliseconds=ms)
        key = stamp.replace(second=0, microsecond=0)
        bar = minutes.get(key)
        if bar is None:
            minutes[key] = [price, price, price, price, 1]
        else:
            if price > bar[1]:
                bar[1] = price
            if price < bar[2]:
                bar[2] = price
            bar[3] = price
            bar[4] += 1
    return minutes


def download(symbol, start, end, workers=12, passes=4, existing=None):
    """Fetch every hour in [start, end), retrying hours that failed rather than
    silently leaving holes. Returns {minute_datetime: [o, h, l, c, ticks]}.

    Hours already present in ``existing`` are skipped, so a re-run tops up a
    partial CSV instead of starting over.
    """
    hours = []
    cursor = start
    while cursor < end:
        hours.append(cursor)
        cursor += timedelta(hours=1)

    have_hours = set()
    if existing:
        have_hours = {m.replace(minute=0) for m in existing}
    pending = [h for h in hours if h not in have_hours]

    divisor = POINT_DIVISOR.get(symbol.upper(), 100000.0)
    all_minutes = dict(existing or {})
    if have_hours:
        print(f"  resuming: {len(have_hours):,} hours already on disk, "
              f"{len(pending):,} to fetch", flush=True)

    for attempt in range(1, passes + 1):
        if not pending:
            break
        failed = []
        done = 0
        # Ease off a little each pass, but stay parallel: the gaps come from
        # request latency and timeouts, not from rate limiting.
        pass_workers = max(8, int(workers / (1 + 0.35 * (attempt - 1))))
        print(f"  pass {attempt}: {len(pending):,} hours at {pass_workers} workers",
              flush=True)

        with ThreadPoolExecutor(max_workers=pass_workers) as pool:
            for when, result in zip(pending, pool.map(lambda h: fetch_hour(symbol, h), pending)):
                done += 1
                if result.blob:
                    all_minutes.update(ticks_to_minutes(result.blob, when, divisor))
                elif not result.definite:
                    failed.append(when)
                if done % 240 == 0 or done == len(pending):
                    print(f"    {done:>5}/{len(pending)}  ->  {len(all_minutes):,} M1 bars"
                          f"  ({len(failed)} to retry)", flush=True)

        pending = failed
        if pending:
            time.sleep(5)   # let the far end breathe before the next pass

    if pending:
        print(f"  WARNING: {len(pending)} hours still unfetched after {passes} passes",
              flush=True)
    return all_minutes


def read_existing_csv(path):
    """Load a previously written CSV so a re-run can resume instead of refetching."""
    if not path or not os.path.exists(path):
        return {}
    bars = {}
    with open(path, "r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            stamp = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            bars[stamp] = [float(row["open"]), float(row["high"]),
                           float(row["low"]), float(row["close"]), int(float(row["volume"]))]
    return bars


def write_csv(minutes, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time", "open", "high", "low", "close", "volume"])
        for key in sorted(minutes):
            o, h, l, c, v = minutes[key]
            writer.writerow([key.strftime("%Y-%m-%d %H:%M:%S"),
                             f"{o:.2f}", f"{h:.2f}", f"{l:.2f}", f"{c:.2f}", v])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Download Dukascopy M1 history as CSV.")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD (exclusive)")
    ap.add_argument("--out", required=True, help="output CSV path")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--passes", type=int, default=4,
                    help="retry rounds for hours that failed (not real 404s)")
    ap.add_argument("--resume", action="store_true",
                    help="keep bars already in --out and only fetch what is missing")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    print(f"Downloading {args.symbol} M1 from {start.date()} to {end.date()} "
          f"({int((end - start).total_seconds() // 3600):,} hourly files)...")
    prior = read_existing_csv(args.out) if args.resume else {}
    bars = download(args.symbol, start, end, workers=args.workers,
                    passes=args.passes, existing=prior)
    if not bars:
        sys.exit("No data returned - check the symbol name and date range.")

    write_csv(bars, args.out)
    keys = sorted(bars)
    print(f"\nWrote {len(bars):,} M1 bars to {args.out}")
    print(f"  {keys[0]}  ->  {keys[-1]}")
