"""
SQLite Persistence Layer for XAU/USD Trading Bot.
Persists settings, closed trades, circuit breaker events, and logs across restarts.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from trading_bot.backtest import Trade
from trading_bot.circuit_breakers import CircuitBreakerConfig, CircuitBreakerState


class BotStorage:
    """Manages SQLite storage for the bot."""

    def __init__(self, db_path: str = "trading_bot_data.sqlite"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def update_closed_trade(self, ticket: int, exit_price: float, net_pnl: float, exit_reason: str = "SL/TP Hit"):
        """Updates trade outcome when MT5 closes the position."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).isoformat()
            cursor.execute("""
                UPDATE trades
                SET exit_price = ?,
                    net_pnl_usd = ?,
                    exit_reason = ?,
                    exit_time = ?
                WHERE ticket = ?
            """, (exit_price, net_pnl, exit_reason, now, ticket))
            conn.commit()

    def _init_db(self):
        """Initializes tables if they do not exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Trades table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket INTEGER,
                    direction TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    lot_size REAL NOT NULL,
                    exit_time TEXT,
                    exit_price REAL,
                    exit_reason TEXT,
                    net_pnl_usd REAL,
                    pnl_r_multiple REAL,
                    spread_paid_usd REAL,
                    commission_paid_usd REAL,
                    pattern_name TEXT,
                    is_demo INTEGER DEFAULT 1,
                    magic_number INTEGER,
                    created_at TEXT NOT NULL
                )
            """)

            # 2. Settings table (key-value store)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # 3. Logs & Events table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata_json TEXT
                )
            """)

            conn.commit()

    def save_trade(self, trade_data: Dict[str, Any]) -> int:
        """Saves a trade record to database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).isoformat()
            cursor.execute("""
                INSERT INTO trades (
                    ticket, direction, entry_time, entry_price, stop_loss, take_profit,
                    lot_size, exit_time, exit_price, exit_reason, net_pnl_usd,
                    pnl_r_multiple, spread_paid_usd, commission_paid_usd, pattern_name,
                    is_demo, magic_number, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data.get("ticket") or trade_data.get("order_id"),
                trade_data.get("direction"),
                trade_data.get("entry_time") or trade_data.get("opened_at", now),
                trade_data.get("entry_price"),
                trade_data.get("stop_loss") or trade_data.get("sl"),
                trade_data.get("take_profit") or trade_data.get("tp"),
                trade_data.get("lot_size") or trade_data.get("volume", 0.1),
                trade_data.get("exit_time"),
                trade_data.get("exit_price"),
                trade_data.get("exit_reason"),
                trade_data.get("net_pnl_usd", 0.0),
                trade_data.get("pnl_r_multiple", 0.0),
                trade_data.get("spread_paid_usd", 0.0),
                trade_data.get("commission_paid_usd", 0.0),
                trade_data.get("pattern_name", ""),
                1 if trade_data.get("is_demo", True) else 0,
                trade_data.get("magic_number", 9212001),
                now
            ))
            conn.commit()
            return cursor.lastrowid

    def record_trade(self, trade_data: Dict[str, Any]) -> int:
        """Alias for save_trade."""
        return self.save_trade(trade_data)

    def get_all_trades(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Retrieves recent trades sorted newest first."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def set_setting(self, key: str, value: Any):
        """Sets a configuration setting."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            val_str = json.dumps(value) if not isinstance(value, str) else value
            now = datetime.now(timezone.utc).isoformat()
            cursor.execute("""
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """, (key, val_str, now))
            conn.commit()

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Retrieves a setting value."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if not row:
                return default
            val = row["value"]
            try:
                return json.loads(val)
            except Exception:
                return val

    def log_event(self, level: str, category: str, message: str, meta: Optional[Dict[str, Any]] = None):
        """Logs an event to SQLite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).isoformat()
            cursor.execute("""
                INSERT INTO bot_logs (timestamp, level, category, message, metadata_json)
                VALUES (?, ?, ?, ?, ?)
            """, (now, level, category, message, json.dumps(meta or {})))
            conn.commit()

    def get_recent_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves recent log messages."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bot_logs ORDER BY id DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
