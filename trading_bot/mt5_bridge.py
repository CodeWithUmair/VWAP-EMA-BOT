"""
MetaTrader 5 Bridge for Live Market Data and Order Execution.
Supports real Windows MetaTrader 5 and mock fallback for non-Windows environments.
"""

import os
import sys
import time
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
    HAS_MT5 = True
except (ImportError, Exception):
    mt5 = None
    MT5_AVAILABLE = False
    HAS_MT5 = False


@dataclass
class BarData:
    time: int
    open: float
    high: float
    low: float
    close: float
    tick_volume: int


@dataclass
class SymbolInfo:
    symbol: str
    bid: float
    ask: float
    spread_usd: float
    point: float
    digits: int


@dataclass
class AccountInfo:
    login: int
    trade_mode: str
    balance: float
    equity: float
    margin: float
    free_margin: float
    currency: str
    server: str
    is_demo: bool


class MT5Bridge:
    def __init__(self, symbol: str = "XAUUSDm", timeframe: int = 1):
        self.symbol = symbol
        self.timeframe = timeframe
        self.is_connected = False
        self.is_simulation = not MT5_AVAILABLE

    def connect(self, login: Optional[int] = None, password: Optional[str] = None, server: Optional[str] = None) -> bool:
        """Connects to MT5 terminal."""
        if not HAS_MT5:
            print("[INFO] MetaTrader5 package not available. Running in simulated fallback mode.")
            self.is_connected = True
            return True

        if not mt5.initialize():
            print(f"[ERROR] MT5 initialize failed: {mt5.last_error()}")
            self.is_connected = False
            return False

        if login and password and server:
            authorized = mt5.login(login=login, password=password, server=server)
            if not authorized:
                print(f"[ERROR] MT5 login failed: {mt5.last_error()}")
                self.is_connected = False
                return False

        self.is_connected = True
        # Ensure target symbol is selected
        mt5.symbol_select(self.symbol, True)
        print(f"[SUCCESS] Connected to MetaTrader 5. Target symbol: {self.symbol}")
        return True

    def disconnect(self):
        """Shuts down MT5 connection."""
        if HAS_MT5 and self.is_connected:
            mt5.shutdown()
        self.is_connected = False

    def get_symbol_info(self, symbol: Optional[str] = None) -> SymbolInfo:
        """Retrieves real-time bid, ask, and spread from MT5."""
        sym = symbol or self.symbol
        if not self.is_connected or not HAS_MT5:
            return SymbolInfo(
                symbol=sym, bid=4432.35, ask=4432.61,
                spread_usd=0.26, point=0.01, digits=2
            )

        # Make sure symbol is selected in Market Watch
        if not mt5.symbol_select(sym, True):
            for alt in ["XAUUSDm", "XAUUSD", "GOLD", "XAUUSD.m", "XAUUSD_i"]:
                if mt5.symbol_select(alt, True):
                    sym = alt
                    self.symbol = alt
                    break

        info = mt5.symbol_info(sym)
        if info is None:
            return SymbolInfo(
                symbol=sym, bid=4432.35, ask=4432.61,
                spread_usd=0.26, point=0.01, digits=2
            )

        tick = mt5.symbol_info_tick(sym)
        bid = tick.bid if tick and tick.bid > 0 else info.bid
        ask = tick.ask if tick and tick.ask > 0 else info.ask
        spread_usd = round(ask - bid, info.digits or 2)

        return SymbolInfo(
            symbol=sym,
            bid=bid,
            ask=ask,
            spread_usd=spread_usd,
            point=info.point,
            digits=info.digits
        )

    def get_rates(self, count: int = 150, symbol: Optional[str] = None) -> List[BarData]:
        """
        Retrieves real-time candlestick data (M1) from live MT5 terminal.
        """
        sym = symbol or self.symbol
        if not self.is_connected or not HAS_MT5:
            return self._generate_simulated_rates(count)

        mt5.symbol_select(sym, True)
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, count)

        if rates is None or len(rates) == 0:
            print(f"[WARNING] MT5 returned 0 rates for {sym}, error: {mt5.last_error()}")
            return self._generate_simulated_rates(count)

        bars = []
        for r in rates:
            bars.append(
                BarData(
                    time=int(r['time']),
                    open=float(r['open']),
                    high=float(r['high']),
                    low=float(r['low']),
                    close=float(r['close']),
                    tick_volume=int(r['tick_volume'])
                )
            )
        return bars

    def get_account_info(self) -> AccountInfo:
        """Retrieves live account balance and demo verification."""
        if not self.is_connected or not HAS_MT5:
            return AccountInfo(
                login=9928120, trade_mode="DEMO", balance=10000.0,
                equity=10000.0, margin=0.0, free_margin=10000.0,
                currency="USD", server="Exness-Demo", is_demo=True
            )

        acc = mt5.account_info()
        if acc is None:
            return AccountInfo(
                login=0, trade_mode="UNKNOWN", balance=0.0,
                equity=0.0, margin=0.0, free_margin=0.0,
                currency="USD", server="Unknown", is_demo=False
            )

        is_demo = (acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO)
        mode_str = "DEMO" if is_demo else "LIVE"

        return AccountInfo(
            login=acc.login,
            trade_mode=mode_str,
            balance=acc.balance,
            equity=acc.equity,
            margin=acc.margin,
            free_margin=acc.margin_free,
            currency=acc.currency,
            server=acc.server,
            is_demo=is_demo
        )

    def is_algo_trading_allowed(self) -> bool:
        """Verifies MT5 terminal automated trading toggle."""
        if not HAS_MT5 or not self.is_connected:
            return True
        term_info = mt5.terminal_info()
        return term_info.trade_allowed if term_info else False

    # Alias taake dono naamo se call ho sake
    def is_algo_trading_enabled(self) -> bool:
        return self.is_algo_trading_allowed()

    def send_order(
        self,
        direction: str,
        volume: float,
        sl_price: float,
        tp_price: float,
        magic_number: int = 9212001,
        comment: str = "EMA_VWAP_Scalper"
    ) -> Tuple[bool, Optional[int], str]:
        """
        Dispatches causal order with properly rounded SL and TP to MT5.
        Auto-detects broker filling mode and validates minimum stop distances.
        """
        if not self.is_connected or not HAS_MT5:
            ticket = 12345678
            msg = f"Simulated {direction} order of {volume} lots executed at market with SL=${sl_price:.2f}, TP=${tp_price:.2f}"
            return True, ticket, msg

        sym = self.symbol
        mt5.symbol_select(sym, True)
        info = mt5.symbol_info(sym)
        tick = mt5.symbol_info_tick(sym)

        if info is None or tick is None:
            return False, None, f"Could not get tick info for {sym}"

        digits = info.digits if info.digits is not None else 2
        point = info.point if info.point is not None else 0.01
        is_buy = (direction.upper() == "BUY")
        
        price = tick.ask if is_buy else tick.bid
        price = round(price, digits)

        # Minimum stop distance calculation ($1.00 min distance on gold)
        min_stop_dist = max(1.0, 100 * point)

        # Validate SL / TP
        if is_buy:
            if sl_price <= 0 or sl_price >= price:
                sl_price = price - max(2.5, min_stop_dist)
            if tp_price <= 0 or tp_price <= price:
                tp_price = price + max(3.75, min_stop_dist * 1.5)
        else:
            if sl_price <= 0 or sl_price <= price:
                sl_price = price + max(2.5, min_stop_dist)
            if tp_price <= 0 or tp_price >= price:
                tp_price = price - max(3.75, min_stop_dist * 1.5)

        sl_price = round(sl_price, digits)
        tp_price = round(tp_price, digits)

        # Auto-detect broker filling mode
        filling_type = mt5.ORDER_FILLING_FOK
        if hasattr(info, 'filling_mode'):
            if info.filling_mode & mt5.ORDER_FILLING_IOC:
                filling_type = mt5.ORDER_FILLING_IOC
            elif info.filling_mode & mt5.ORDER_FILLING_FOK:
                filling_type = mt5.ORDER_FILLING_FOK
            else:
                filling_type = mt5.ORDER_FILLING_RETURN

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": float(price),
            "sl": float(sl_price),
            "tp": float(tp_price),
            "deviation": 50,
            "magic": int(magic_number),
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }
        
    def get_closed_deals(self, from_timestamp: int) -> List[Dict[str, Any]]:
        """
        Fetches closed deals from MT5 history since a given timestamp.
        """
        if not self.is_connected or not HAS_MT5:
            return []

        from_date = datetime.fromtimestamp(from_timestamp, tz=timezone.utc)
        to_date = datetime.now(timezone.utc)

        deals = mt5.history_deals_get(from_date, to_date)
        if deals is None or len(deals) == 0:
            return []

        closed_list = []
        for d in deals:
            # Entry 1 = OUT (Deal closing a position)
            if d.entry == mt5.DEAL_ENTRY_OUT or d.entry == 1:
                closed_list.append({
                    "ticket": d.order,
                    "deal_id": d.ticket,
                    "symbol": d.symbol,
                    "profit": float(d.profit),
                    "commission": float(d.commission),
                    "swap": float(d.swap),
                    "close_price": float(d.price),
                    "volume": float(d.volume),
                    "close_time": datetime.fromtimestamp(d.time, tz=timezone.utc).isoformat(),
                    "magic": d.magic,
                    "comment": d.comment
                })
        return closed_list




        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            ret_code = result.retcode if result else mt5.last_error()
            comment_err = result.comment if result else ""
            err_msg = f"Order failed. MT5 Retcode: {ret_code} ({comment_err})"
            print(f"[ERROR] {err_msg} | Request: Price={price}, SL={sl_price}, TP={tp_price}, Fill={filling_type}")
            return False, None, err_msg

        success_msg = f"Order #{result.order} EXECUTED: {direction} {result.volume} lots at ${result.price:.2f} (SL: ${sl_price:.2f}, TP: ${tp_price:.2f})"
        print(f"[SUCCESS] {success_msg}")
        return True, result.order, success_msg


    def _generate_simulated_rates(self, count: int) -> List[BarData]:
        """Fallback rates generator matching current gold price."""
        now = int(time.time())
        bars = []
        base_price = 4432.00
        for i in range(count):
            t = now - (count - i) * 60
            o = base_price + ((i % 10) - 5) * 0.2
            h = o + 0.4
            l = o - 0.3
            c = (o + h + l) / 3
            bars.append(BarData(time=t, open=o, high=h, low=l, close=c, tick_volume=100))
        return bars