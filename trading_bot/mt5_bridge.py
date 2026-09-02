"""
MetaTrader5 Connector & Execution Bridge.

Features:
1. Native MT5 Integration on Windows systems with terminal installed.
2. Realistic Simulation Mode Fallback for testing, backtesting, and development on non-Windows/Linux.
3. Strict Pre-Trade Verification on EVERY order attempt:
   - Queries account_info().trade_mode == ACCOUNT_TRADE_MODE_DEMO (refuses LIVE accounts).
   - Checks terminal_info().trade_allowed (AlgoTrading toggle).
   - Enforces unique Magic Number tagging on all orders.
   - Queries exact broker spread and point values via symbol_info().
"""

import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

# Try importing native MetaTrader5 if available (Windows)
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    mt5 = None


@dataclass
class AccountInfo:
    """Standardized account information."""
    login: int
    trade_mode: str  # "DEMO", "REAL", "CONTEST"
    is_demo: bool
    balance: float
    equity: float
    margin: float
    free_margin: float
    leverage: int
    currency: str
    server: str
    company: str


@dataclass
class SymbolInfo:
    """Standardized symbol specifications."""
    name: str
    bid: float
    ask: float
    spread_points: float
    spread_usd: float
    point: float
    digits: int
    volume_min: float
    volume_max: float
    volume_step: float
    trade_contract_size: float


class MT5Bridge:
    """Bridge for interacting with MetaTrader5 or Simulation Engine."""

    def __init__(self, magic_number: int = 9212001, symbol: str = "XAUUSD"):
        self.magic_number = magic_number
        self.symbol = symbol
        self.is_connected = False
        self.is_simulation = not MT5_AVAILABLE
        self.sim_balance = 10000.0
        self.sim_equity = 10000.0
        self.sim_positions: Dict[int, Dict[str, Any]] = {}
        self.ticket_counter = 5000000

    def connect(self, path: Optional[str] = None) -> Tuple[bool, str]:
        """Initializes connection to MetaTrader5 terminal."""
        if not MT5_AVAILABLE:
            self.is_connected = True
            self.is_simulation = True
            return True, "Running in High-Fidelity Simulation Mode (Native MT5 requires Windows + MT5 Terminal)"

        try:
            init_kwargs = {}
            if path:
                init_kwargs["path"] = path

            if not mt5.initialize(**init_kwargs):
                err = mt5.last_error()
                self.is_connected = False
                return False, f"MT5 initialization failed: {err}"

            self.is_connected = True
            self.is_simulation = False
            return True, "Connected to MetaTrader 5 Terminal successfully"
        except Exception as e:
            self.is_connected = False
            return False, f"Exception connecting to MT5: {str(e)}"

    def disconnect(self):
        """Shuts down MT5 connection."""
        if MT5_AVAILABLE and self.is_connected and not self.is_simulation:
            mt5.shutdown()
        self.is_connected = False

    def get_account_info(self) -> AccountInfo:
        """
        Fetches live account info.
        CRITICAL: Re-reads trade_mode on EVERY call directly from terminal.
        """
        if self.is_simulation or not MT5_AVAILABLE or not self.is_connected:
            return AccountInfo(
                login=99887766,
                trade_mode="DEMO",
                is_demo=True,
                balance=self.sim_balance,
                equity=self.sim_equity,
                margin=0.0,
                free_margin=self.sim_balance,
                leverage=100,
                currency="USD",
                server="MetaQuotes-Demo",
                company="MetaQuotes Software Corp."
            )

        acc = mt5.account_info()
        if acc is None:
            raise RuntimeError(f"Failed to get account info from MT5: {mt5.last_error()}")

        is_demo = (acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO)
        mode_str = "DEMO" if is_demo else ("REAL" if acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL else "CONTEST")

        return AccountInfo(
            login=acc.login,
            trade_mode=mode_str,
            is_demo=is_demo,
            balance=acc.balance,
            equity=acc.equity,
            margin=acc.margin,
            free_margin=acc.margin_free,
            leverage=acc.leverage,
            currency=acc.currency,
            server=acc.server,
            company=acc.company
        )

    def is_algo_trading_enabled(self) -> bool:
        """Checks if AlgoTrading toggle in MT5 terminal is active."""
        if self.is_simulation or not MT5_AVAILABLE or not self.is_connected:
            return True

        term = mt5.terminal_info()
        if term is None:
            return False
        return bool(term.trade_allowed)

    def is_algo_trading_allowed(self) -> bool:
        """Alias for is_algo_trading_enabled."""
        return self.is_algo_trading_enabled()

    def get_symbol_info(self, symbol: Optional[str] = None) -> SymbolInfo:
        """Fetches symbol specs and live spread."""
        sym = symbol or self.symbol

        if self.is_simulation or not MT5_AVAILABLE or not self.is_connected:
            # Default realistic Gold specs: Bid ~ 2380.50, Ask ~ 2380.75 (0.25 spread)
            return SymbolInfo(
                name=sym,
                bid=2380.50,
                ask=2380.75,
                spread_points=25,
                spread_usd=0.25,
                point=0.01,
                digits=2,
                volume_min=0.01,
                volume_max=100.0,
                volume_step=0.01,
                trade_contract_size=100.0
            )

        info = mt5.symbol_info(sym)
        if info is None:
            raise RuntimeError(f"Symbol {sym} not found in MT5: {mt5.last_error()}")

        if not info.visible:
            mt5.symbol_select(sym, True)
            info = mt5.symbol_info(sym)

        spread_usd = info.spread * info.point
        return SymbolInfo(
            name=info.name,
            bid=info.bid,
            ask=info.ask,
            spread_points=float(info.spread),
            spread_usd=round(spread_usd, 2),
            point=info.point,
            digits=info.digits,
            volume_min=info.volume_min,
            volume_max=info.volume_max,
            volume_step=info.volume_step,
            trade_contract_size=info.trade_contract_size
        )

    def get_rates(self, symbol: Optional[str] = None, count: int = 150) -> Any:
        """
        Fetches rates and returns them in named bar format or dict format compatible with Streamlit app.
        """
        bars_dict = self.fetch_recent_bars(symbol=symbol, count=count)
        
        class BarObj:
            def __init__(self, time_val, o, h, l, c, v):
                self.time = time_val
                self.open = o
                self.high = h
                self.low = l
                self.close = c
                self.tick_volume = v

        return [
            BarObj(
                bars_dict["times"][i],
                bars_dict["opens"][i],
                bars_dict["highs"][i],
                bars_dict["lows"][i],
                bars_dict["closes"][i],
                bars_dict["volumes"][i]
            )
            for i in range(len(bars_dict["closes"]))
        ]

    def fetch_recent_bars(self, symbol: Optional[str] = None, count: int = 300, timeframe_str: str = "M1") -> Dict[str, List]:
        """Fetches recent OHLCV bars for M1, M5, or M15."""
        sym = symbol or self.symbol

        if self.is_simulation or not MT5_AVAILABLE or not self.is_connected:
            from trading_bot.data_feed import generate_realistic_gold_data
            return generate_realistic_gold_data(num_bars=count)

        tf_map = {
            "M1": getattr(mt5, "TIMEFRAME_M1", 1),
            "M5": getattr(mt5, "TIMEFRAME_M5", 5),
            "M15": getattr(mt5, "TIMEFRAME_M15", 15),
            "H1": getattr(mt5, "TIMEFRAME_H1", 60),
        }
        mt5_tf = tf_map.get(timeframe_str.upper(), getattr(mt5, "TIMEFRAME_M1", 1))

        rates = mt5.copy_rates_from_pos(sym, mt5_tf, 0, count)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Failed to fetch {timeframe_str} rates for {sym}: {mt5.last_error()}")

        times = [datetime.fromtimestamp(r['time'], tz=timezone.utc).isoformat() for r in rates]
        opens = [float(r['open']) for r in rates]
        highs = [float(r['high']) for r in rates]
        lows = [float(r['low']) for r in rates]
        closes = [float(r['close']) for r in rates]
        volumes = [float(r['tick_volume']) for r in rates]

        return {
            "times": times,
            "opens": opens,
            "highs": highs,
            "lows": lows,
            "closes": closes,
            "volumes": volumes
        }

    def fetch_htf_bars(self, symbol: Optional[str] = None, count: int = 100, timeframe: str = "M15") -> Dict[str, List]:
        """Fetches higher timeframe bars (M15 or M5) for macro trend alignment."""
        return self.fetch_recent_bars(symbol=symbol, count=count, timeframe_str=timeframe)


    def send_order(
        self,
        direction: str,  # "BUY" or "SELL"
        volume: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        sl_price: Optional[float] = None,
        tp_price: Optional[float] = None,
        magic_number: Optional[int] = None,
        comment: str = "TripleFilter_Scalper"
    ) -> Tuple[bool, int, str]:
        """
        Sends an order with rigorous safety checks. Supports both stop_loss/take_profit and sl_price/tp_price kwargs.
        Always returns a 3-tuple (bool, int, str).
        """
        try:
            sl = stop_loss if stop_loss is not None else (sl_price or 0.0)
            tp = take_profit if take_profit is not None else (tp_price or 0.0)
            magic = magic_number if magic_number is not None else self.magic_number

            # Safety Check 1: DEMO Verification
            acc = self.get_account_info()
            if not acc.is_demo:
                return False, 0, "ORDER REFUSED: Account is LIVE. Trading bot is restricted to DEMO accounts only."

            # Safety Check 2: AlgoTrading enabled
            if not self.is_algo_trading_enabled():
                return False, 0, "ORDER REFUSED: AlgoTrading is disabled in MetaTrader5."

            sym_info = self.get_symbol_info()

            if self.is_simulation or not MT5_AVAILABLE or not self.is_connected:
                # Simulate fill
                self.ticket_counter += 1
                ticket = self.ticket_counter
                fill_price = sym_info.ask if direction == "BUY" else sym_info.bid
                self.sim_positions[ticket] = {
                    "ticket": ticket,
                    "direction": direction,
                    "volume": volume,
                    "entry_price": fill_price,
                    "stop_loss": round(sl, 2),
                    "take_profit": round(tp, 2),
                    "open_time": datetime.now(timezone.utc).isoformat(),
                    "magic": magic,
                    "comment": comment
                }
                return True, ticket, f"Simulated {direction} order {ticket} filled at ${fill_price:.2f} (SL: ${sl:.2f}, TP: ${tp:.2f})"

            # Real MT5 Order Execution
            order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
            price = sym_info.ask if direction == "BUY" else sym_info.bid
            
            # Round stops to digits
            sl_rounded = round(sl, sym_info.digits) if sl > 0 else 0.0
            tp_rounded = round(tp, sym_info.digits) if tp > 0 else 0.0

            # Auto-detect supported filling mode
            filling_mode = mt5.ORDER_FILLING_IOC
            if hasattr(mt5, "symbol_info") and sym_info:
                raw_sym = mt5.symbol_info(self.symbol)
                if raw_sym and hasattr(raw_sym, "filling_mode"):
                    if raw_sym.filling_mode & 1:  # FOK
                        filling_mode = mt5.ORDER_FILLING_FOK
                    elif raw_sym.filling_mode & 2:  # IOC
                        filling_mode = mt5.ORDER_FILLING_IOC
                    else:
                        filling_mode = mt5.ORDER_FILLING_RETURN

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": float(volume),
                "type": order_type,
                "price": float(price),
                "sl": float(sl_rounded),
                "tp": float(tp_rounded),
                "deviation": 20,  # Max 20 points slippage tolerance
                "magic": int(magic),
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_mode,
            }

            result = mt5.order_send(request)
            if result is None:
                err_msg = mt5.last_error() if hasattr(mt5, "last_error") else "Unknown MT5 error"
                return False, 0, f"order_send returned None: {err_msg}"

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return False, 0, f"Order rejected (retcode {result.retcode}): {result.comment}"

            return True, result.order, f"Order {result.order} executed successfully at {result.price} (SL: {sl_rounded}, TP: {tp_rounded})"

        except Exception as e:
            return False, 0, f"Order dispatch exception: {str(e)}"

    def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns all open positions for the target symbol."""
        sym = symbol or self.symbol
        if self.is_simulation or not MT5_AVAILABLE or not self.is_connected:
            return list(self.sim_positions.values())

        positions = mt5.positions_get(symbol=sym)
        if positions is None:
            return []

        res = []
        for pos in positions:
            res.append({
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "direction": "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
                "volume": pos.volume,
                "entry_price": pos.price_open,
                "current_price": pos.price_current,
                "sl": pos.sl,
                "tp": pos.tp,
                "profit": pos.profit,
                "magic": pos.magic,
                "comment": pos.comment,
                "open_time": datetime.fromtimestamp(pos.time, tz=timezone.utc).isoformat()
            })
        return res

    def get_closed_deals(self, from_timestamp: int) -> List[Dict[str, Any]]:
        """Fetches closed trade deals since from_timestamp."""
        if self.is_simulation or not MT5_AVAILABLE or not self.is_connected:
            return []

        from_dt = datetime.fromtimestamp(from_timestamp, tz=timezone.utc)
        to_dt = datetime.now(timezone.utc)
        deals = mt5.history_deals_get(from_dt, to_dt)
        if deals is None:
            return []

        closed = []
        for d in deals:
            # Entry out means closed position
            if d.entry == 1 or d.entry == mt5.DEAL_ENTRY_OUT:
                closed.append({
                    "ticket": d.position_id,
                    "order": d.order,
                    "symbol": d.symbol,
                    "profit": float(d.profit),
                    "close_price": float(d.price),
                    "volume": float(d.volume),
                    "time": datetime.fromtimestamp(d.time, tz=timezone.utc).isoformat()
                })
        return closed

    def modify_position_sl(self, ticket: int, new_sl: float) -> bool:
        """Modifies Stop Loss of an active position (e.g., for Break-Even)."""
        if self.is_simulation or not MT5_AVAILABLE or not self.is_connected:
            if ticket in self.sim_positions:
                self.sim_positions[ticket]["stop_loss"] = new_sl
                return True
            return False

        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            return False

        p = pos[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": p.symbol,
            "sl": float(round(new_sl, 2)),
            "tp": float(p.tp),
        }
        res = mt5.order_send(request)
        return res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
