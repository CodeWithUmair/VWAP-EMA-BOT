"""
Streamlit Desktop Live Dashboard for XAU/USD Triple Filter Scalper Bot.
Run locally with: streamlit run trading_bot/streamlit_app.py
"""

import os
import sys
import html
from datetime import datetime, timezone
import json

# Ensure project root is on path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    import streamlit as st
except ImportError:
    st = None

from trading_bot.strategy import (
    StrategyParameters,
    evaluate_checklist_at_bar,
    calculate_ema,
    calculate_session_vwap,
    calculate_atr,
    calculate_sl_tp,
    detect_order_blocks_causal
)
from trading_bot.strategies import DEFAULT_STRATEGY_KEY, list_strategies
from trading_bot.backtest import run_causal_backtest
from trading_bot.circuit_breakers import CircuitBreakerConfig, CircuitBreakerManager
from trading_bot.storage import BotStorage
from trading_bot.mt5_bridge import MT5Bridge
from trading_bot.data_feed import generate_realistic_gold_data


# --------------------------------------------------------------------------
# Presentation helpers. These touch styling / layout only — no trading logic.
# CSS is defensive: every rule is "nice to have", so if a Streamlit release
# renames a selector the rule simply stops applying, the app never breaks.
# --------------------------------------------------------------------------

_THEME_CSS = """
<style>
  /* hide Streamlit chrome: Deploy button, hamburger menu, coloured top bar */
  [data-testid="stAppDeployButton"],
  [data-testid="stToolbar"],
  [data-testid="stDecoration"],
  #MainMenu, footer { display: none !important; visibility: hidden !important; }
  header[data-testid="stHeader"] { background: transparent !important; height: 0 !important; }

  /* kill the ~5rem default top padding */
  [data-testid="stMainBlockContainer"], .block-container {
    padding-top: 1.25rem !important;
    padding-bottom: 2.5rem !important;
    max-width: 1440px;
  }

  /* brand header */
  .gx-hero {
    display: flex; align-items: center; gap: 14px;
    padding: 15px 20px; margin: 0 0 16px;
    border-radius: 14px;
    background: linear-gradient(120deg, #1c1810 0%, #241d10 42%, #13161e 100%);
    border: 1px solid rgba(230, 180, 80, .22);
  }
  .gx-hero .gx-badge {
    font-size: 24px; line-height: 1;
    background: rgba(230, 180, 80, .12);
    border: 1px solid rgba(230, 180, 80, .32);
    padding: 9px 12px; border-radius: 12px;
  }
  .gx-hero h1 { margin: 0; font-size: 1.3rem; font-weight: 700; letter-spacing: .2px; color: #F4D48A; }
  .gx-hero p  { margin: 3px 0 0; font-size: .8rem; color: #9aa3b2; }

  /* metric cards */
  [data-testid="stMetric"] {
    background: #161A22; border: 1px solid #262b36;
    border-radius: 12px; padding: 12px 14px 10px;
  }
  [data-testid="stMetricLabel"] p {
    color: #8a93a3 !important; font-size: .7rem !important;
    text-transform: uppercase; letter-spacing: .6px;
  }
  [data-testid="stMetricValue"] { font-size: 1.3rem !important; }

  /* equal-height metric cards in the top status bar (some carry a delta line,
     some don't — stretch every card to match the tallest) */
  .st-key-gx_topmetrics [data-testid="stHorizontalBlock"] { align-items: stretch; }
  .st-key-gx_topmetrics [data-testid="stColumn"] { display: flex; }
  .st-key-gx_topmetrics [data-testid="stColumn"] > div,
  .st-key-gx_topmetrics [data-testid="stColumn"] [data-testid="stVerticalBlock"],
  .st-key-gx_topmetrics [data-testid="stColumn"] [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"] {
    width: 100%; height: 100%;
  }
  .st-key-gx_topmetrics [data-testid="stMetric"] {
    height: 100%; display: flex; flex-direction: column; justify-content: flex-start;
  }

  /* auto-trade toggle: pin to the far right edge of its row */
  .st-key-gx_autotrade { display: flex; flex-direction: column; align-items: flex-end; }
  .st-key-gx_autotrade [data-testid="stWidgetLabel"] { justify-content: flex-end; }
  .st-key-gx_autotrade [data-testid="stCaptionContainer"] { text-align: right; }

  /* tabs */
  [data-testid="stTabs"] { margin-top: 16px; }
  [data-testid="stTabs"] [role="tablist"] { gap: 4px; border-bottom: 1px solid #262b36; }
  [data-testid="stTabs"] [role="tab"] {
    padding: 7px 16px; border-radius: 9px 9px 0 0; font-weight: 600; font-size: .85rem;
  }
  [data-testid="stTabs"] [role="tab"][aria-selected="true"] {
    background: rgba(230, 180, 80, .10); color: #F4D48A !important;
  }

  /* sidebar */
  [data-testid="stSidebar"] { background: #12151c; border-right: 1px solid #22262f; }
  [data-testid="stSidebar"] h2 { font-size: .92rem; letter-spacing: .4px; color: #F4D48A; }

  .stButton > button { border-radius: 10px; font-weight: 600; }
  hr { margin: .9rem 0 !important; border-color: #262b36 !important; }

  /* strategy checklist rows */
  .gx-row {
    display: flex; align-items: flex-start; gap: 10px;
    padding: 9px 12px; margin: 5px 0; border-radius: 10px;
    background: #151922; border: 1px solid #232833;
  }
  .gx-row.ok   { border-left: 3px solid #2ecc71; }
  .gx-row.fail { border-left: 3px solid #e74c3c; }
  .gx-pill {
    font-size: .68rem; font-weight: 700; padding: 3px 9px;
    border-radius: 999px; white-space: nowrap; margin-top: 1px;
  }
  .gx-pill.ok   { background: rgba(46, 204, 113, .15); color: #54e08c; }
  .gx-pill.fail { background: rgba(231, 76, 60, .14); color: #ff7a6b; }
  .gx-row .gx-t { font-size: .85rem; font-weight: 600; color: #dfe3ea; }
  .gx-row .gx-d { font-size: .75rem; color: #8a93a3; margin-top: 2px; }
  .gx-side { font-size: .95rem; font-weight: 700; letter-spacing: .3px; margin: 2px 0 6px; }
  .gx-side.buy  { color: #54e08c; }
  .gx-side.sell { color: #ff7a6b; }

  /* live pulse (open trades / engine tick) */
  @keyframes gxpulse {
    0%   { box-shadow: 0 0 0 0 rgba(255, 92, 158, .55); }
    70%  { box-shadow: 0 0 0 9px rgba(255, 92, 158, 0); }
    100% { box-shadow: 0 0 0 0 rgba(255, 92, 158, 0); }
  }
  @keyframes gxblink { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
  .gx-dot {
    display: inline-block; width: 9px; height: 9px; border-radius: 50%;
    background: #ff5c9e; animation: gxpulse 1.4s infinite; margin-right: 7px;
    vertical-align: middle;
  }
  .gx-dot.live { background: #2ecc71; animation: gxpulse 1.6s infinite; }
  .gx-tick { color: #54e08c; font-variant-numeric: tabular-nums; animation: gxblink 1s infinite; }

  /* trade history table */
  .gx-wrap { max-height: 460px; overflow: auto; border: 1px solid #232833; border-radius: 12px; }
  .gx-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: .8rem; }
  .gx-table th {
    text-align: left; padding: 9px 11px; color: #8a93a3; font-weight: 600;
    text-transform: uppercase; letter-spacing: .5px; font-size: .66rem;
    border-bottom: 1px solid #262b36; position: sticky; top: 0; background: #10141c; z-index: 1;
  }
  .gx-table td {
    padding: 8px 11px; border-bottom: 1px solid #1b202a; color: #dfe3ea;
    white-space: nowrap; font-variant-numeric: tabular-nums;
  }
  .gx-table tr:hover td { background: #141922; }
  .gx-table tr.gx-open td { background: rgba(255, 92, 158, .07); }
  .gx-table tr.gx-open td:first-child { border-left: 3px solid #ff5c9e; }
  /* element-qualified so these beat the base `.gx-table td` colour rule */
  .gx-table td.gx-b   { color: #4ade80 !important; font-weight: 700; }
  .gx-table td.gx-s   { color: #f87171 !important; font-weight: 700; }
  .gx-table td.gx-pos { color: #4ade80 !important; font-weight: 700; }
  .gx-table td.gx-neg { color: #f87171 !important; font-weight: 700; }
  .gx-table td.gx-mut { color: #6b7686 !important; }
  .gx-pos { color: #4ade80; } .gx-neg { color: #f87171; } .gx-mut { color: #6b7686; }
  .gx-b { color: #4ade80; font-weight: 700; } .gx-s { color: #f87171; font-weight: 700; }
  .gx-tag {
    font-size: .64rem; font-weight: 700; padding: 2px 7px; border-radius: 999px;
    background: rgba(255, 92, 158, .16); color: #ff86b6; letter-spacing: .4px;
  }
</style>
"""


def _inject_theme():
    st.markdown(_THEME_CSS, unsafe_allow_html=True)


# One-line subtitle per strategy, so the banner describes what is actually armed.
_HERO_BLURBS = {
    "vwap_ema_scalper": "Session VWAP &middot; EMA 9/21 crossover &middot; causal order blocks "
                        "&middot; pullback &middot; candle confirmation",
    "crt_body_soup": "H1 candle range &middot; body-close liquidity sweep &middot; "
                     "confirmation trigger &middot; H4 trend bias &middot; equilibrium + far-side targets",
    "crt_tbs": "H1 candle range &middot; wick sweep &middot; close back inside &middot; "
               "equilibrium + far-side targets",
}


def _hero(strategy=None):
    """Banner for whichever strategy is selected - never a hard-coded name."""
    if strategy is None:
        title, blurb = "XAU / USD &middot; MT5 Trading Bot", "Select a strategy in the sidebar"
    else:
        title = f"XAU / USD &middot; {html.escape(strategy.label)}"
        blurb = _HERO_BLURBS.get(strategy.key, html.escape(strategy.description))
    st.markdown(
        '<div class="gx-hero">'
        '<div class="gx-badge">🏆</div>'
        f'<div><h1>{title}</h1>'
        f'<p>{blurb}</p></div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _check_row(n: int, title: str, passed: bool, detail: str):
    cls = "ok" if passed else "fail"
    label = "PASS" if passed else "FAIL"
    st.markdown(
        f'<div class="gx-row {cls}">'
        f'<span class="gx-pill {cls}">{label}</span>'
        f'<div style="flex:1"><div class="gx-t">{n}. {html.escape(title)}</div>'
        f'<div class="gx-d">{html.escape(detail or "")}</div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# Sidebar persistence.
#
# Streamlit rebuilds every widget from scratch on each rerun, so a widget whose
# value comes from a literal default silently discards whatever the user typed
# the moment the page refreshes. Every control that changes how the bot trades
# is therefore backed by the SQLite settings table: the stored value seeds the
# widget, and any change is written straight back. The headless engine reads
# the same table, so a change here reaches the trader without a restart.
# --------------------------------------------------------------------------

def _remember(storage, setting_key, current, previous):
    """Persist a widget's value when it actually changed."""
    if current != previous:
        try:
            storage.set_setting(setting_key, current)
        except Exception:
            # A locked DB must never take the dashboard down; the value simply
            # stays for this session and is re-saved on the next interaction.
            pass
    return current


def _persisted_number(storage, setting_key, label, lo, hi, default, step, **kw):
    saved = storage.get_setting(setting_key, default)
    cast = float if isinstance(default, float) else int
    try:
        saved = cast(saved)
    except (TypeError, ValueError):
        saved = cast(default)
    saved = min(max(saved, cast(lo)), cast(hi))
    value = st.sidebar.number_input(label, cast(lo), cast(hi), saved, cast(step),
                                    key=f"set_{setting_key}", **kw)
    return _remember(storage, setting_key, value, saved)


def _persisted_text_list(storage, setting_key, label, default=(), **kw):
    """A persisted sidebar text box holding a comma-separated list (e.g. manual
    news-blackout windows). Stored as a real list under the setting key; edited
    as one comma-separated string, same convention the rest of the app uses for
    lists a person types by hand.
    """
    saved = storage.get_setting(setting_key, list(default)) or []
    prev_text = ", ".join(str(x) for x in saved)
    chosen_text = st.sidebar.text_input(label, value=prev_text, key=f"set_{setting_key}", **kw)
    chosen_list = [w.strip() for w in chosen_text.split(",") if w.strip()]
    return _remember(storage, setting_key, chosen_list, saved)


def _render_param_controls(strategy, storage=None):
    """Build the sidebar tunables from the selected strategy's own declarations.

    Each strategy publishes a list of ParamSpecs, so adding a knob to a strategy
    puts it on the sidebar without touching this file. Widget keys are namespaced
    per strategy so switching strategies never carries one's values into another.
    """
    values = {}
    for spec in strategy.param_specs():
        widget_key = f"p_{strategy.key}_{spec.key}"
        # Settings are namespaced per strategy, so tuning one never leaks into
        # another and each keeps its own remembered values.
        setting_key = f"param.{strategy.key}.{spec.key}"
        saved = storage.get_setting(setting_key, spec.default) if storage else spec.default

        if spec.kind == "toggle":
            prev = bool(saved)
            values[spec.key] = _remember(storage, setting_key, st.sidebar.toggle(
                spec.label, value=prev, key=widget_key, help=spec.help or None), prev)                 if storage else st.sidebar.toggle(
                    spec.label, value=prev, key=widget_key, help=spec.help or None)
        elif spec.kind == "select":
            options = spec.options or []
            prev = saved if saved in options else spec.default
            index = options.index(prev) if prev in options else 0
            chosen = st.sidebar.selectbox(spec.label, options, index=index,
                                          key=widget_key, help=spec.help or None)
            values[spec.key] = _remember(storage, setting_key, chosen, prev) if storage else chosen
        elif spec.kind == "slider":
            try:
                prev = float(saved)
            except (TypeError, ValueError):
                prev = float(spec.default)
            prev = min(max(prev, float(spec.min_value)), float(spec.max_value))
            chosen = st.sidebar.slider(
                spec.label, float(spec.min_value), float(spec.max_value),
                prev, float(spec.step or 0.1), key=widget_key, help=spec.help or None)
            values[spec.key] = _remember(storage, setting_key, chosen, prev) if storage else chosen
        elif spec.kind == "text":
            prev = str(saved if saved is not None else spec.default)
            chosen = st.sidebar.text_input(spec.label, value=prev, key=widget_key,
                                           help=spec.help or None)
            values[spec.key] = _remember(storage, setting_key, chosen, prev) if storage else chosen
        else:  # "number"
            cast = float if isinstance(spec.default, float) else int
            try:
                prev = cast(saved)
            except (TypeError, ValueError):
                prev = cast(spec.default)
            prev = min(max(prev, cast(spec.min_value)), cast(spec.max_value))
            chosen = st.sidebar.number_input(
                spec.label, cast(spec.min_value), cast(spec.max_value), prev,
                cast(spec.step or 1), key=widget_key, help=spec.help or None)
            values[spec.key] = _remember(storage, setting_key, chosen, prev) if storage else chosen
    return values


def _render_checklist(evaluation):
    """Render whatever checks the active strategy reported, in its own order."""
    for n, step in enumerate(evaluation.steps, start=1):
        _check_row(n, step.name, step.passed, step.detail)


def _levels_caption(evaluation):
    """One-line SL / TP summary, including TP1 for two-target strategies."""
    parts = [f"SL: **${evaluation.suggested_sl:.2f}**"]
    if evaluation.suggested_tp1:
        parts.append(f"TP1 (equilibrium): **${evaluation.suggested_tp1:.2f}**")
    parts.append(f"TP{'2' if evaluation.suggested_tp1 else ''}: **${evaluation.suggested_tp:.2f}**")
    return "  |  ".join(parts)


def _f(v, fmt="{:.2f}", dash="—"):
    """Format a possibly-None numeric cell."""
    try:
        if v is None or v == "":
            return dash
        return fmt.format(float(v))
    except (TypeError, ValueError):
        return html.escape(str(v))


def _short_time(v):
    if not v:
        return "—"
    s = str(v).replace("T", " ")
    return html.escape(s[:19])


def _render_trade_history(storage, mt5_bridge, magic_num, limit=100):
    """Detailed trade-history table. Rows still open (no exit recorded, or matched to
    a live MT5 position) get a pulsing pink marker and their P/L is pulled live from
    MT5 rather than the stale SQLite value."""
    rows = storage.get_all_trades(limit) or []

    live = {}
    try:
        for p in (mt5_bridge.get_open_positions() or []):
            if p.get("magic") == magic_num:
                live[int(p["ticket"])] = p
    except Exception:
        pass

    open_now = len(live)
    st.markdown(
        f'<div style="margin:2px 0 10px;font-size:.85rem">'
        f'<span class="gx-dot"></span><b>{open_now}</b> position(s) live'
        + (f' &nbsp;·&nbsp; <span class="gx-mut">{len(rows)} in history</span>' if rows else '')
        + '</div>',
        unsafe_allow_html=True,
    )

    if not rows and not live:
        st.info("No trades recorded yet. Rows appear here once the engine (or a manual button) places one.")
        return

    head = ("", "Ticket", "Side", "Lot", "Entry", "SL", "TP", "Exit", "P/L $", "R", "Reason", "Opened", "Closed")
    body = []

    seen = set()
    for r in rows:
        tk = r.get("ticket")
        try:
            tk_i = int(tk) if tk is not None else None
        except (TypeError, ValueError):
            tk_i = None
        seen.add(tk_i)
        lp = live.get(tk_i)
        # Authoritative "open" = the ticket is in MT5's live positions right now.
        # A null exit in SQLite just means the close was never reconciled, not that
        # it's still running — don't pulse those.
        is_open = lp is not None

        side = (r.get("direction") or "").upper()
        side_cls = "gx-b" if side == "BUY" else "gx-s"
        reconciled = is_open or r.get("exit_price") is not None or bool(r.get("exit_reason"))
        pnl = lp["profit"] if lp else r.get("net_pnl_usd")
        pnl_cls = "gx-pos" if (pnl or 0) > 0 else ("gx-neg" if (pnl or 0) < 0 else "gx-mut")
        pnl_txt = _f(pnl, "{:+.2f}") if reconciled else "—"

        # R-multiple: P&L divided by dollars risked from entry to the original
        # stop. The engine leaves pnl_r_multiple at 0 for most rows (only the
        # backtester's Trade dataclass fills it in), so compute it live here
        # instead of trusting a column that's usually empty.
        r_txt = "—"
        if reconciled and pnl is not None:
            try:
                _risk_usd = abs(float(r.get("entry_price")) - float(r.get("stop_loss") or r.get("sl"))) \
                    * 100.0 * float(r.get("lot_size") or r.get("volume") or 0.01)
                if _risk_usd > 0:
                    r_txt = "{:+.2f}".format(pnl / _risk_usd)
            except (TypeError, ValueError):
                r_txt = "—"
        marker = '<span class="gx-dot"></span>' if is_open else ''
        reason_html = ('<span class="gx-tag">LIVE</span>' if is_open
                       else html.escape(str(r.get("exit_reason") or "unreconciled")))

        body.append(
            f'<tr class="{"gx-open" if is_open else ""}">'
            f'<td>{marker}</td>'
            f'<td class="gx-mut">{html.escape(str(tk or "—"))}</td>'
            f'<td class="{side_cls}">{html.escape(side or "—")}</td>'
            f'<td>{_f(r.get("lot_size"), "{:.2f}")}</td>'
            f'<td>{_f(r.get("entry_price"))}</td>'
            f'<td>{_f(r.get("stop_loss"))}</td>'
            f'<td>{_f(r.get("take_profit"))}</td>'
            f'<td>{"—" if is_open else _f(r.get("exit_price"))}</td>'
            f'<td class="{pnl_cls if reconciled else "gx-mut"}">{pnl_txt}</td>'
            f'<td class="{pnl_cls if reconciled else "gx-mut"}">{r_txt}</td>'
            f'<td>{reason_html}</td>'
            f'<td class="gx-mut">{_short_time(r.get("entry_time"))}</td>'
            f'<td class="gx-mut">{"—" if is_open else _short_time(r.get("exit_time"))}</td>'
            f'</tr>'
        )

    # Live positions with no SQLite row yet (engine placed it, close not reconciled).
    for tk_i, p in live.items():
        if tk_i in seen:
            continue
        side = (p.get("direction") or "").upper()
        side_cls = "gx-b" if side == "BUY" else "gx-s"
        pnl = p.get("profit", 0.0)
        pnl_cls = "gx-pos" if pnl > 0 else ("gx-neg" if pnl < 0 else "gx-mut")
        body.insert(0,
            f'<tr class="gx-open">'
            f'<td><span class="gx-dot"></span></td>'
            f'<td class="gx-mut">{html.escape(str(tk_i))}</td>'
            f'<td class="{side_cls}">{html.escape(side)}</td>'
            f'<td>{_f(p.get("volume"), "{:.2f}")}</td>'
            f'<td>{_f(p.get("entry_price"))}</td>'
            f'<td>{_f(p.get("sl"))}</td>'
            f'<td>{_f(p.get("tp"))}</td>'
            f'<td>—</td>'
            f'<td class="{pnl_cls}">{_f(pnl, "{:+.2f}")}</td>'
            f'<td class="gx-mut">—</td>'
            f'<td><span class="gx-tag">LIVE</span></td>'
            f'<td class="gx-mut">{_short_time(p.get("open_time"))}</td>'
            f'<td class="gx-mut">—</td>'
            f'</tr>'
        )

    st.markdown(
        '<div class="gx-wrap"><table class="gx-table"><thead><tr>'
        + "".join(f"<th>{h}</th>" for h in head)
        + "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>",
        unsafe_allow_html=True,
    )


def main():
    if st is None:
        print("Streamlit is not installed. Install via: pip install streamlit")
        return

    st.set_page_config(
        page_title="XAU/USD MT5 Trading Bot",
        page_icon="🏆",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    _inject_theme()

    # Initialize persistence and managers in session state
    if "storage" not in st.session_state:
        st.session_state.storage = BotStorage()
    if "cb_manager" not in st.session_state:
        cb_cfg = CircuitBreakerConfig(bypass_noise_gate_for_demo=True)
        st.session_state.cb_manager = CircuitBreakerManager(config=cb_cfg)
    if "mt5_bridge" not in st.session_state or not hasattr(st.session_state.mt5_bridge, "get_rates"):
        st.session_state.mt5_bridge = MT5Bridge(symbol="XAUUSDm")
        st.session_state.mt5_bridge.connect()

    storage = st.session_state.storage
    cb_manager = st.session_state.cb_manager
    # Ensure noise gate bypass is always active for demo testing
    cb_manager.config.bypass_noise_gate_for_demo = True
    mt5_bridge = st.session_state.mt5_bridge

    # SIDEBAR: Strategy selection, parameters & safety controls
    #
    # The picker is the first thing in the sidebar because everything under it —
    # the tunables, the checklist, the backtest, and which engine the headless
    # bot runs — follows from it. The choice is written to SQLite so the live
    # engine (a separate process) picks up the same strategy on its next loop.
    strategies = list_strategies()
    saved_key = storage.get_setting("active_strategy", DEFAULT_STRATEGY_KEY)
    keys = [s.key for s in strategies]
    start_index = keys.index(saved_key) if saved_key in keys else 0

    st.sidebar.header("🧠 Strategy")
    chosen_label = st.sidebar.radio(
        "Active strategy",
        [s.label for s in strategies],
        index=start_index,
        key="strategy_picker",
        help="Switches the live checklist, the backtest and the headless auto-engine.",
    )
    strategy = next(s for s in strategies if s.label == chosen_label)
    if strategy.key != saved_key:
        storage.set_setting("active_strategy", strategy.key)

    st.sidebar.caption(f"**{strategy.timeframe}** · {strategy.description}")

    # Banner comes after the picker so it can name the live strategy.
    _hero(strategy)

    st.sidebar.markdown("---")
    st.sidebar.header("⚙️ Strategy Parameters")
    param_values = _render_param_controls(strategy, storage)
    params = strategy.build_params(param_values)

    st.sidebar.markdown("---")
    st.sidebar.header("🛡️ Safety & Circuit Breakers")
    # Default to 10% of the live balance so this agrees with the headless
    # engine, which scales its own cap the same way. A fixed $200 default was
    # larger than the entire account on a small balance - i.e. no cap at all.
    _bal = st.session_state.get("acct_balance_hint", 0.0) or 0.0
    _default_daily = round(max(_bal * 0.10, 5.0), 2) if _bal else 20.0
    max_daily_loss = _persisted_number(
        storage, "risk.max_daily_loss_usd", "Max Daily Loss ($)",
        5.0, 1000.0, min(_default_daily, 1000.0), 5.0,
        help="Engine stops trading for the day past this loss. Defaults to 10% of balance.")
    max_consec_losses = _persisted_number(
        storage, "risk.max_consecutive_losses", "Max Consec Losses", 1, 10, 3, 1)
    magic_num = _persisted_number(
        storage, "risk.magic_number", "Magic Number", 100000, 9999999, 9212001, 1)
    max_spread_usd = _persisted_number(
        storage, "risk.max_spread_usd", "Max Spread ($, 0 = off)", 0.0, 5.0, 0.60, 0.05,
        help="Engine refuses new entries when the live spread exceeds this.")
    stale_bar_secs = _persisted_number(
        storage, "risk.stale_bar_secs", "Stale-Feed Halt (sec, 0 = off)", 0, 600, 180, 30,
        help="Engine halts new entries if the newest M1 bar is older than this.")
    news_windows = _persisted_text_list(
        storage, "risk.news_blackout_windows", "Manual News Windows (UTC, comma-sep HH:MM-HH:MM)",
        help="e.g. 12:25-12:45, 13:55-14:15 — no new entries inside these windows. "
             "Supplements the automatic ForexFactory calendar filter, doesn't replace it.")

    cb_manager.config.max_daily_loss_usd = max_daily_loss
    cb_manager.config.max_consecutive_losses = max_consec_losses
    cb_manager.config.magic_number = magic_num

    # Sidebar Manual Refresh Button
    st.sidebar.markdown("---")
    if st.sidebar.button("🔄 Refresh Live Market Data", key="btn_refresh_market_data", use_container_width=True):
        st.rerun()

    # 1. Fetch LIVE Rates directly from MT5
    raw_bars = mt5_bridge.get_rates(count=150)
    opens = [b.open for b in raw_bars]
    highs = [b.high for b in raw_bars]
    lows = [b.low for b in raw_bars]
    closes = [b.close for b in raw_bars]
    times = [b.time for b in raw_bars]
    volumes = [b.tick_volume for b in raw_bars]

    # Check terminal & account status
    acc = mt5_bridge.get_account_info()
    # Cache the balance for the sidebar, which renders before this runs and so
    # can only size its default risk caps from the previous pass.
    st.session_state.acct_balance_hint = acc.balance
    if hasattr(mt5_bridge, "is_algo_trading_enabled"):
        algo_enabled = mt5_bridge.is_algo_trading_enabled()
    elif hasattr(mt5_bridge, "is_algo_trading_allowed"):
        algo_enabled = mt5_bridge.is_algo_trading_allowed()
    else:
        algo_enabled = True

    sym_info = mt5_bridge.get_symbol_info()

    # Top Status Bar — keyed container so the CSS can force every metric card to
    # the same height regardless of whether it carries a delta line.
    with st.container(key="gx_topmetrics"):
        col1, col2, col3 = st.columns(3)
        col1.metric("Account Mode", acc.trade_mode, "DEMO ONLY" if acc.is_demo else "LIVE - BLOCKED")
        col2.metric("Balance", f"${acc.balance:,.2f}")
        col3.metric(f"Gold ({mt5_bridge.symbol}) Bid/Ask", f"${sym_info.bid:.2f} / ${sym_info.ask:.2f}", f"Spread: ${sym_info.spread_usd:.2f}")

        col4, col5, col6 = st.columns(3)
        col4.metric("Consecutive Losses", f"{cb_manager.state.consecutive_losses} / {max_consec_losses}")
        col5.metric("Daily Net PnL", f"${cb_manager.state.daily_pnl_usd:+,.2f}")

        # Live win rate from actual closed trades in SQLite (not the synthetic backtest).
        closed_trades = [
            t for t in (storage.get_all_trades(500) or [])
            if t.get("exit_time") and t.get("net_pnl_usd") is not None
        ]
        if closed_trades:
            wins = sum(1 for t in closed_trades if t["net_pnl_usd"] > 0)
            win_rate = wins / len(closed_trades) * 100
            col6.metric("Live Win Rate", f"{win_rate:.1f}%", f"{wins}W / {len(closed_trades) - wins}L")
        else:
            col6.metric("Live Win Rate", "—", "No closed trades yet")

    # ---- AUTO-ENGINE + OPEN POSITION status --------------------------------------
    # How the "LIVE" light works: the headless auto-trader (run_live_auto_bot.py)
    # writes an `engine_heartbeat` ISO timestamp into the SQLite settings table on
    # every ~3s loop. This fragment re-runs once per second — Streamlit pushes the
    # rerun over its browser<->server WebSocket — recomputes "age = now - heartbeat"
    # and repaints. So the counter below ticks 0,1,2,0,1,2… while the engine is
    # alive, and climbs without resetting the moment it stops. The dashboard never
    # trades; it only reads the heartbeat + live MT5 positions.
    st.markdown("---")

    @st.fragment(run_every="1s")
    def _engine_status_row():
        sc1, sc2, sc3 = st.columns([1, 2, 1])

        hb_raw = storage.get_setting("engine_heartbeat", None)
        hb_age = None
        if hb_raw:
            try:
                hb_age = (datetime.now(timezone.utc) - datetime.fromisoformat(hb_raw)).total_seconds()
            except (TypeError, ValueError):
                hb_age = None
        with sc1:
            if hb_age is not None and hb_age < 20:
                st.markdown(
                    f'<div style="padding:9px 12px;border-radius:10px;'
                    f'background:rgba(46,204,113,.10);border:1px solid rgba(46,204,113,.35)">'
                    f'<span class="gx-dot live"></span><b>AUTO-ENGINE: LIVE</b> '
                    f'<span class="gx-mut">· updated {hb_age:.0f}s ago</span></div>',
                    unsafe_allow_html=True,
                )
            elif hb_age is not None:
                st.markdown(
                    f'<div style="padding:9px 12px;border-radius:10px;'
                    f'background:rgba(231,76,60,.10);border:1px solid rgba(231,76,60,.35)">'
                    f'🔴 <b>AUTO-ENGINE: STOPPED</b> '
                    f'<span class="gx-mut">· last seen {hb_age / 60:.1f} min ago</span></div>',
                    unsafe_allow_html=True,
                )
            else:
                st.warning("🤖 AUTO-ENGINE: not running · only the manual buttons below work")

        with sc2:
            try:
                open_pos = mt5_bridge.get_open_positions() if hasattr(mt5_bridge, "get_open_positions") else []
            except Exception:
                open_pos = []
            bot_pos = [p for p in open_pos if p.get("magic") == magic_num]
            if bot_pos:
                p = bot_pos[0]
                extra = f" &nbsp;(+{len(bot_pos) - 1} more)" if len(bot_pos) > 1 else ""
                pnl = p.get("profit", 0.0)
                pnl_cls = "gx-pos" if pnl >= 0 else "gx-neg"
                st.markdown(
                    f'<div style="padding:9px 12px;border-radius:10px;'
                    f'background:rgba(255,92,158,.08);border:1px solid rgba(255,92,158,.35)">'
                    f'<span class="gx-dot"></span><b>POSITION OPEN</b> — '
                    f'<span class="{"gx-b" if p["direction"] == "BUY" else "gx-s"}">{p["direction"]}</span> '
                    f'{p["volume"]} {mt5_bridge.symbol} @ ${p["entry_price"]:.2f} '
                    f'<span class="gx-mut">· SL ${p["sl"]:.2f} · TP ${p["tp"]:.2f}</span> · '
                    f'<span class="{pnl_cls}">P/L ${pnl:+,.2f}</span>{extra}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.info("📌 No open position — the engine will take the next signal "
                        "that passes every check.")

        with sc3, st.container(key="gx_autotrade"):
            auto_trade_on = storage.get_setting("auto_trade_enabled", True)
            toggled = st.toggle(
                "Auto-Trade",
                value=auto_trade_on,
                key="auto_trade_enabled_toggle",
                help="ON: the headless engine opens trades on its own full-checklist signals. "
                     "OFF: it keeps managing any open position (break-even, P&L) but "
                     "won't open new ones — use the Manual Order Dispatch buttons below instead.",
            )
            if toggled != auto_trade_on:
                storage.set_setting("auto_trade_enabled", toggled)
                st.rerun()
            st.caption("🟢 Auto" if toggled else "🟡 Manual-only")

    _engine_status_row()

    # Tabs
    tab1, tab2, tab3, tab4 = st.tabs(["📋 Live Setup Checklist", "📊 Causal Backtest & Noise Gate", "📈 Live Market & Indicators", "📜 Trade Logs & SQLite"])

    # Evaluate the current bar through whichever strategy is selected.
    live_data = {"opens": opens, "highs": highs, "lows": lows,
                 "closes": closes, "times": times, "volumes": volumes}
    curr_idx = len(closes) - 1
    checklist = strategy.evaluate(live_data, curr_idx, params,
                                  strategy.prepare(live_data, params))
    long_st = checklist["LONG"]
    short_st = checklist["SHORT"]

    with tab1:
        st.subheader(f"📋 Live Checklist — {strategy.label} ({long_st.step_count} steps)")
        st.caption(f"Evaluated on the latest closed bar · {strategy.timeframe}")
        
        # Noise gate banner
        if not cb_manager.state.noise_gate_verified:
            st.info(f"ℹ️ **DEMO TRADING ACTIVE:** Noise gate is set to demo-bypass mode (p={cb_manager.state.noise_gate_p_value:.4f}). Orders will execute with full circuit-breaker safety.")
        else:
            st.success(f"✅ **NOISE-CONTROL GATE PASSED:** Validated with Monte Carlo p-value: {cb_manager.state.noise_gate_p_value:.4f} <= 0.05. Ready for execution.")

        c_long, c_short = st.columns(2)

        with c_long:
            st.markdown('<div class="gx-side buy">🟢 BUY / LONG SETUP</div>', unsafe_allow_html=True)
            st.caption(f"Market ${long_st.close_price:.2f}  ·  "
                       f"{long_st.passed_count}/{long_st.step_count} checks  ·  "
                       f"Signal: {long_st.signal or 'NO SIGNAL'}")

            _render_checklist(long_st)

            if long_st.all_passed:
                tp1_line = f"\n- TP1 (equilibrium): ${long_st.suggested_tp1:.2f}" if long_st.suggested_tp1 else ""
                st.success(
                    f"🎯 **ALL {long_st.step_count} CRITERIA MET FOR BUY ENTRY**\n"
                    f"- Entry: ${long_st.suggested_entry:.2f}\n"
                    f"- SL: ${long_st.suggested_sl:.2f} (Risk: ${long_st.risk_points:.2f})"
                    f"{tp1_line}\n"
                    f"- Final TP: ${long_st.suggested_tp:.2f} (Reward: ${long_st.reward_points:.2f})")
            else:
                st.caption(_levels_caption(long_st))

        with c_short:
            st.markdown('<div class="gx-side sell">🔴 SELL / SHORT SETUP</div>', unsafe_allow_html=True)
            st.caption(f"Market ${short_st.close_price:.2f}  ·  "
                       f"{short_st.passed_count}/{short_st.step_count} checks  ·  "
                       f"Signal: {short_st.signal or 'NO SIGNAL'}")

            _render_checklist(short_st)

            if short_st.all_passed:
                tp1_line = f"\n- TP1 (equilibrium): ${short_st.suggested_tp1:.2f}" if short_st.suggested_tp1 else ""
                st.error(
                    f"🎯 **ALL {short_st.step_count} CRITERIA MET FOR SELL ENTRY**\n"
                    f"- Entry: ${short_st.suggested_entry:.2f}\n"
                    f"- SL: ${short_st.suggested_sl:.2f} (Risk: ${short_st.risk_points:.2f})"
                    f"{tp1_line}\n"
                    f"- Final TP: ${short_st.suggested_tp:.2f} (Reward: ${short_st.reward_points:.2f})")
            else:
                st.caption(_levels_caption(short_st))

        st.markdown("---")
        st.subheader("⚡ Manual Order Dispatch")
        st.caption("One-click entry at the strategy's swing SL / TP. The headless auto-engine "
                   "trades on its own — these buttons are for manual overrides only.")

        cb_manager.config.bypass_noise_gate_for_demo = True
        can_trade, reason = cb_manager.can_open_trade(
            is_demo_account=acc.is_demo if hasattr(acc, "is_demo") else True,
            algo_trading_enabled=algo_enabled,
            current_balance=acc.balance
        )
        col_b1, col_b2, col_b3 = st.columns([2, 2, 2])
        
        with col_b1:
            if st.button("🚀 Trigger Strategy BUY Order", key="btn_trigger_buy_order_main", type="primary", use_container_width=True):
                ok, ticket, msg = mt5_bridge.send_order(
                    direction="BUY",
                    volume=0.01,
                    sl_price=long_st.suggested_sl,
                    tp_price=long_st.suggested_tp,
                    magic_number=magic_num,
                    comment=f"{strategy.key[:12]}_BUY"
                )
                if ok:
                    st.success(msg)
                    storage.record_trade({
                        "order_id": ticket,
                        "symbol": mt5_bridge.symbol,
                        "direction": "BUY",
                        "volume": 0.01,
                        "entry_price": long_st.close_price,
                        "sl": long_st.suggested_sl,
                        "tp": long_st.suggested_tp,
                        "status": "OPEN",
                        "opened_at": datetime.now(timezone.utc).isoformat()
                    })
                else:
                    st.error(msg)

        with col_b2:
            if st.button("🔻 Trigger Strategy SELL Order", key="btn_trigger_sell_order_main", type="secondary", use_container_width=True):
                ok, ticket, msg = mt5_bridge.send_order(
                    direction="SELL",
                    volume=0.01,
                    sl_price=short_st.suggested_sl,
                    tp_price=short_st.suggested_tp,
                    magic_number=magic_num,
                    comment=f"{strategy.key[:12]}_SELL"
                )
                if ok:
                    st.success(msg)
                    storage.record_trade({
                        "order_id": ticket,
                        "symbol": mt5_bridge.symbol,
                        "direction": "SELL",
                        "volume": 0.01,
                        "entry_price": short_st.close_price,
                        "sl": short_st.suggested_sl,
                        "tp": short_st.suggested_tp,
                        "status": "OPEN",
                        "opened_at": datetime.now(timezone.utc).isoformat()
                    })
                else:
                    st.error(msg)

        with col_b3:
            if cb_manager.state.is_consec_loss_tripped or cb_manager.state.is_daily_loss_tripped:
                if st.button("🔄 Reset Circuit Breakers", key="btn_reset_circuit_breakers_main", use_container_width=True):
                    cb_manager.manual_reset_consecutive_losses()
                    st.success("Circuit breakers reset!")
                    st.rerun()

        if not can_trade:
            st.warning(f"Order Dispatch Guardrail Active: {reason}")

    with tab2:
        st.subheader("📊 Causal Backtesting & Noise-Control Monte Carlo Gate")
        st.write("Strict zero-lookahead backtest with separate In-Sample (75%) vs Out-of-Sample (25%) splits and 100-shuffle Monte Carlo noise testing.")
        st.caption(f"Testing: **{strategy.label}** with the sidebar parameters.")

        bt_source = st.radio(
            "Price data",
            ["Live MT5 history (real)", "Synthetic random walk"],
            horizontal=True,
            key="radio_bt_source",
            help="Real M1 bars pulled from the running terminal are the only ones "
                 "whose result means anything. The random walk only exercises the plumbing.",
        )
        bt_bars = st.slider("Historical Bars to Test", 500, 50000, 5000, 500, key="slider_bt_bars")
        if st.button("▶️ Execute Full Backtest & Gate Verification", key="btn_run_full_backtest"):
            with st.spinner("Running causal simulation and permutation tests..."):
                if bt_source.startswith("Live"):
                    try:
                        bt_data = mt5_bridge.fetch_recent_bars(count=bt_bars, timeframe_str="M1")
                    except Exception as exc:
                        bt_data = None
                        st.error(f"Could not pull M1 history from MT5: {exc}")
                    if not bt_data or len(bt_data.get("closes", [])) < 200:
                        st.warning("MT5 returned too little M1 history — falling back to synthetic bars. "
                                   "Open an M1 chart for the symbol and scroll back to force a download.")
                        bt_data = generate_realistic_gold_data(num_bars=bt_bars, seed=101)
                else:
                    bt_data = generate_realistic_gold_data(num_bars=bt_bars, seed=101)

                res = run_causal_backtest(
                    bt_data["opens"], bt_data["highs"], bt_data["lows"], bt_data["closes"],
                    bt_data["times"], bt_data["volumes"], params,
                    initial_balance=10000.0, split_ratio=0.75, spread_points=sym_info.spread_usd,
                    num_noise_shuffles=100, strategy=strategy
                )
                st.session_state.bt_result = res
                cb_manager.set_noise_gate_status(
                    res.overall_metrics.noise_gate_passed,
                    res.overall_metrics.noise_p_value
                )

        if "bt_result" in st.session_state:
            res = st.session_state.bt_result
            
            m_is = res.in_sample_metrics
            m_oos = res.out_of_sample_metrics
            m_all = res.overall_metrics

            st.markdown("### Performance Breakdown")
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("#### 📘 In-Sample (75%)")
                st.metric("Trades", m_is.total_trades)
                st.metric("Win Rate", f"{m_is.win_rate_pct:.1f}%")
                st.metric("Profit Factor", m_is.profit_factor)
                st.metric("Expectancy (R)", f"{m_is.expectancy_r:+.2f}R")
                st.metric("Net PnL", f"${m_is.total_net_pnl_usd:+,.2f}")
                st.metric("Max Drawdown", f"${m_is.max_drawdown_usd:,.2f} ({m_is.max_drawdown_pct:.1f}%)")

            with c2:
                st.markdown("#### 📙 Out-of-Sample (25%)")
                st.metric("Trades", m_oos.total_trades)
                st.metric("Win Rate", f"{m_oos.win_rate_pct:.1f}%")
                st.metric("Profit Factor", m_oos.profit_factor)
                st.metric("Expectancy (R)", f"{m_oos.expectancy_r:+.2f}R")
                st.metric("Net PnL", f"${m_oos.total_net_pnl_usd:+,.2f}")
                st.metric("Max Drawdown", f"${m_oos.max_drawdown_usd:,.2f} ({m_oos.max_drawdown_pct:.1f}%)")

            with c3:
                st.markdown("#### 🌐 Overall Dataset")
                st.metric("Trades", m_all.total_trades)
                st.metric("Win Rate", f"{m_all.win_rate_pct:.1f}%")
                st.metric("Profit Factor", m_all.profit_factor)
                st.metric("Expectancy (R)", f"{m_all.expectancy_r:+.2f}R")
                st.metric("Net PnL", f"${m_all.total_net_pnl_usd:+,.2f}")
                st.metric("Noise Gate p-value", f"{m_all.noise_p_value:.4f}")

            if m_all.noise_gate_passed:
                st.success(f"🎉 **STRATEGY PASSED NOISE GATE** (p = {m_all.noise_p_value:.4f} <= 0.05, Z-score = {m_all.z_score:.2f})")
            else:
                st.error(f"🛑 **STRATEGY FAILED NOISE GATE** (p = {m_all.noise_p_value:.4f} > 0.05). Edge is not distinguishable from noise.")

    with tab3:
        st.subheader("📈 Live Market & Indicators")

        # Every strategy reports the levels it actually trades off in the
        # evaluation's `context`, so this panel follows the picker instead of
        # hard-coding one strategy's indicators.
        ctx = long_st.context or {}
        if ctx.get("crt_high") is not None or strategy.key.startswith("crt_"):
            st.write("The CRT range from the last completed reference candle, and where price sits in it.")
            if ctx.get("crt_high") is not None:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("CRT High (BSL)", f"${ctx['crt_high']:.2f}")
                m2.metric("CRT Equilibrium", f"${ctx['crt_eq']:.2f}")
                m3.metric("CRT Low (SSL)", f"${ctx['crt_low']:.2f}")
                m4.metric("Range", f"${ctx['crt_high'] - ctx['crt_low']:.2f}")
                st.caption(f"Reference candle opened {ctx.get('crt_start', '—')} · "
                           f"latest close ${closes[-1]:.2f}")
            else:
                st.info("Waiting for a completed reference candle — pull more history "
                        "or give the terminal a moment.")
        else:
            st.write("Visualized indicator values, Order Blocks, and VWAP levels.")
            ema9_vals = calculate_ema(closes, params.ema_fast_period)
            ema21_vals = calculate_ema(closes, params.ema_slow_period)
            vwap_vals = calculate_session_vwap(times, highs, lows, closes, volumes,
                                               params.vwap_anchor_hour_utc)
            st.write(f"Latest 1m Bar Close: **${closes[-1]:.2f}** | EMA9: **${ema9_vals[-1]:.2f}** "
                     f"| EMA21: **${ema21_vals[-1]:.2f}** | VWAP: **${vwap_vals[-1]:.2f}**")

        with st.expander(f"📄 {strategy.label} — full strategy spec"):
            doc = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "strategies", strategy.doc_file)
            if os.path.exists(doc):
                with open(doc, "r", encoding="utf-8") as fh:
                    st.markdown(fh.read())
            else:
                st.caption(f"Spec file not found: {doc}")

    with tab4:
        st.subheader("📜 Trade History")
        st.caption("Closed trades from the local SQLite store, plus any position open right now "
                   "(pink pulse · live P/L from MT5). Refreshes every 3s.")

        @st.fragment(run_every="3s")
        def _trade_history_panel():
            _render_trade_history(storage, mt5_bridge, magic_num, limit=100)

        _trade_history_panel()

        with st.expander("Raw JSON (latest 20)"):
            _raw = storage.get_all_trades(20)
            st.json(_raw if _raw else [{"info": "No persistent trades executed yet."}])

if __name__ == "__main__":
    main()