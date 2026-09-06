"""
Economic-calendar news filter for XAU/USD.

Gold reacts violently to a handful of scheduled releases (NFP, CPI, FOMC, and
the ECB/BoE headline rates). Those minutes produce both the biggest liquidity
sweeps and the biggest losses: spreads blow out, stops slip, and a setup that
looked clean on the chart fills tens of dollars away. Standing aside around
them is one of the cheapest edges available to a sweep strategy.

Where the data comes from
-------------------------
ForexFactory has **no official API and issues no API keys**. It does publish
its calendar as free public JSON, which is what this module reads:

    https://nfs.faireconomy.media/ff_calendar_thisweek.json
    https://nfs.faireconomy.media/ff_calendar_nextweek.json
    https://nfs.faireconomy.media/ff_calendar_lastweek.json

No account, no key, no rate limit to speak of. Third-party "Forex Factory API"
services simply re-serve these same files.

Live vs backtest
----------------
Those three URLs only cover a three-week window, which is fine for live trading
and useless for backtesting a year. For historical runs, load a CSV of past
events instead (:func:`load_events_csv`); the MQL5 EA has a better option still
and should use MT5's native ``CalendarValueHistory``, which carries full history
and works inside the Strategy Tester.
"""

import csv
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

FF_URLS = {
    "lastweek": "https://nfs.faireconomy.media/ff_calendar_lastweek.json",
    "thisweek": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "nextweek": "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
}

# Currencies whose releases actually move gold. Gold is priced in USD, so USD
# events dominate; EUR/GBP majors matter mainly through the dollar index.
GOLD_RELEVANT_CURRENCIES = ("USD", "EUR", "GBP", "ALL")

DEFAULT_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "news_events.json")


@dataclass(frozen=True)
class NewsEvent:
    """One scheduled release, normalised to UTC."""
    when: datetime          # always timezone-aware UTC
    currency: str
    impact: str             # "High" / "Medium" / "Low" / "Holiday"
    title: str

    def to_dict(self) -> Dict[str, str]:
        return {"when": self.when.isoformat(), "currency": self.currency,
                "impact": self.impact, "title": self.title}

    @staticmethod
    def from_dict(d: Dict[str, str]) -> "NewsEvent":
        return NewsEvent(_parse_dt(d["when"]), d.get("currency", ""),
                         d.get("impact", ""), d.get("title", ""))


def _parse_dt(value: str) -> Optional[datetime]:
    """Parse the several timestamp shapes the calendar sources produce, into UTC."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%m/%d/%Y %H:%M", "%d.%m.%Y %H:%M"):
        try:
            dt = datetime.fromisoformat(text) if fmt is None else datetime.strptime(text, fmt)
        except ValueError:
            continue
        # FF stamps carry a -04:00/-05:00 US Eastern offset; normalise to UTC.
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def fetch_forexfactory(weeks: Iterable[str] = ("thisweek",),
                       timeout: int = 30) -> List[NewsEvent]:
    """Download ForexFactory's public calendar JSON. No API key required.

    In practice only ``thisweek`` is reliably published - the last/next week
    files 404 much of the time - so a missing week is skipped rather than
    treated as an error. Refresh weekly (a cron or the live engine's startup)
    to keep the cache current.
    """
    events: List[NewsEvent] = []
    for week in weeks:
        url = FF_URLS.get(week)
        if not url:
            continue
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        payload = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = json.load(response)
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429:          # rate limited - back off and retry
                    time.sleep(5 * (attempt + 1))
                    continue
                break                        # 404: week not published
            except Exception:
                time.sleep(2 * (attempt + 1))
        if payload is None:
            continue   # this week unavailable; the others still count
        for row in payload:
            when = _parse_dt(row.get("date", ""))
            if when is None:
                continue
            events.append(NewsEvent(when, (row.get("country") or "").upper(),
                                    row.get("impact") or "", row.get("title") or ""))
    return sorted(events, key=lambda e: e.when)


def load_events_csv(path: str) -> List[NewsEvent]:
    """Load historical events from a CSV for backtesting.

    Wants columns ``date`` (or ``when``/``time``), ``currency`` (or ``country``),
    ``impact`` and ``title``. Anything the exporter adds is ignored.
    """
    events: List[NewsEvent] = []
    with open(path, "r", newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            low = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
            when = _parse_dt(low.get("date") or low.get("when") or low.get("time") or "")
            if when is None:
                continue
            events.append(NewsEvent(
                when,
                (low.get("currency") or low.get("country") or "").upper(),
                (low.get("impact") or "").title(),
                low.get("title") or low.get("event") or "",
            ))
    return sorted(events, key=lambda e: e.when)


def save_cache(events: List[NewsEvent], path: str = DEFAULT_CACHE) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([e.to_dict() for e in events], fh, indent=1)
    return path


def load_cache(path: str = DEFAULT_CACHE) -> List[NewsEvent]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return sorted((NewsEvent.from_dict(d) for d in json.load(fh)), key=lambda e: e.when)


class NewsFilter:
    """Answers one question: is this timestamp too close to a market-moving release?

    Built once and reused; lookups are a binary search over a sorted list, so it
    is cheap enough to call on every bar of a backtest.
    """

    def __init__(self, events: Optional[List[NewsEvent]] = None,
                 impacts: Tuple[str, ...] = ("High",),
                 currencies: Tuple[str, ...] = GOLD_RELEVANT_CURRENCIES,
                 minutes_before: int = 30,
                 minutes_after: int = 30):
        self.minutes_before = minutes_before
        self.minutes_after = minutes_after
        wanted_impacts = {i.lower() for i in impacts}
        wanted_ccy = {c.upper() for c in currencies}
        self.events = sorted(
            (e for e in (events or [])
             if e.impact.lower() in wanted_impacts and e.currency.upper() in wanted_ccy),
            key=lambda e: e.when)
        self._times = [e.when for e in self.events]

    def __len__(self) -> int:
        return len(self.events)

    @property
    def is_active(self) -> bool:
        """False when no events were loaded - so callers can avoid a silent no-op filter."""
        return bool(self.events)

    def blocking_event(self, when: datetime) -> Optional[NewsEvent]:
        """The release that blocks ``when``, or None if the window is clear."""
        if not self.events:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)

        import bisect
        before = timedelta(minutes=self.minutes_before)
        after = timedelta(minutes=self.minutes_after)
        # Any event in [when - after, when + before] blocks this timestamp.
        lo = bisect.bisect_left(self._times, when - after)
        hi = bisect.bisect_right(self._times, when + before)
        for event in self.events[lo:hi]:
            if (event.when - before) <= when <= (event.when + after):
                return event
        return None

    def is_blocked(self, when: datetime) -> bool:
        return self.blocking_event(when) is not None


def build_filter(csv_path: Optional[str] = None,
                 cache_path: str = DEFAULT_CACHE,
                 fetch_live: bool = False,
                 **kwargs) -> NewsFilter:
    """Assemble a filter from whichever source is available.

    Order of preference: an explicit CSV (historical backtests), then a live
    ForexFactory fetch, then the on-disk cache. Network problems degrade to the
    cache rather than raising, because a calendar outage must never stop trading.
    """
    events: List[NewsEvent] = []
    if csv_path:
        events = load_events_csv(csv_path)
    elif fetch_live:
        try:
            fresh = fetch_forexfactory(("lastweek", "thisweek", "nextweek"))
            # Merge into the cache so repeated weekly refreshes accumulate a
            # usable history instead of overwriting it with one week.
            merged = {(e.when, e.title): e for e in load_cache(cache_path)}
            merged.update({(e.when, e.title): e for e in fresh})
            events = sorted(merged.values(), key=lambda e: e.when)
            if events:
                save_cache(events, cache_path)
        except Exception:
            events = load_cache(cache_path)
    else:
        events = load_cache(cache_path)
    return NewsFilter(events, **kwargs)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Fetch the ForexFactory calendar (free, no API key) and cache it.")
    ap.add_argument("--weeks", nargs="*", default=["lastweek", "thisweek", "nextweek"],
                    choices=sorted(FF_URLS), help="which weeks to pull")
    ap.add_argument("--out", default=DEFAULT_CACHE, help="cache file to write")
    ap.add_argument("--impact", nargs="*", default=["High"], help="impacts to list")
    args = ap.parse_args()

    fetched = fetch_forexfactory(args.weeks)
    if not fetched:
        print("Fetched 0 events (rate-limited or unpublished) - cache left untouched.")
        fetched = load_cache(args.out)
        print(f"Using {len(fetched)} cached events from {args.out}")
    else:
        existing = {(e.when, e.title): e for e in load_cache(args.out)}
        existing.update({(e.when, e.title): e for e in fetched})
        fetched = sorted(existing.values(), key=lambda e: e.when)
        save_cache(fetched, args.out)
        print(f"Cache now holds {len(fetched)} events -> {args.out}")

    wanted = {i.lower() for i in args.impact}
    shown = [e for e in fetched
             if e.impact.lower() in wanted and e.currency in GOLD_RELEVANT_CURRENCIES]
    print(f"\n{len(shown)} gold-relevant {'/'.join(args.impact)} events:")
    for e in shown:
        print(f"  {e.when:%Y-%m-%d %H:%M} UTC  {e.currency:4} {e.impact:8} {e.title}")
