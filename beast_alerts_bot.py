"""
FATHER V15.4 - Stability & Signal Integrity
SQLite ONLY | No PostgreSQL | No MT5 | No Exness connection
Manual research only. No automated order placement, modification or closure.

Render start command:
python -u beast_alerts_bot.py

Required environment variable:
BOT_TOKEN

Optional:
PORT=10000
DB_PATH=father_v15.sqlite3
TWELVE_DATA_API_KEY=...

IMPORTANT:
- Market feeds use daily candles, not guaranteed live broker quotes.
- Twelve Data cache: 15 minutes. Yahoo cache: 5 minutes.
- The 60-second Hunter loop is an analysis cycle, not a live-price guarantee.
- Render Free local storage is temporary and may be lost on restart/redeploy.
"""

import os
import re
import asyncio
import logging
import sqlite3
import threading
import math
import time
import json
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.error import RetryAfter, BadRequest, Forbidden, TimedOut, NetworkError
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", "10000"))
DB_PATH = os.environ.get("DB_PATH", "father_v15.sqlite3").strip()
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing.")
if not 1 <= PORT <= 65535:
    raise RuntimeError("Invalid PORT.")
if not DB_PATH:
    raise RuntimeError("DB_PATH cannot be empty.")

VERSION = "15.4"
SCAN_INTERVAL_SECONDS = 60
TWELVE_DATA_REFRESH_SECONDS = 15 * 60
YAHOO_CACHE_SECONDS = 5 * 60
HUNTER_COOLDOWN_SECONDS = 6 * 3600
HUNTER_PENDING_LEASE_SECONDS = 10 * 60
MAX_TELEGRAM_LENGTH = 3800
MAX_MARKET_DATA_AGE_DAYS = 7
HUNTER_TICKERS = ("BTC-USD", "TLT")

STOP_ATR_MULTIPLE = 1.5
REWARD_RISK_RATIO = 2.0
RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0
MAX_STOP_PERCENT_WARNING = 8.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("FATHER_V15_4")

_known_users = set()
_known_users_lock = threading.Lock()
_market_cache = {}
_market_lock = threading.Lock()
_hunter_cycle_lock = asyncio.Lock()


def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def local_time(value=None):
    if not value:
        dt = utc_now()
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            dt = utc_now()
    try:
        dt = dt.astimezone(ZoneInfo("Africa/Kampala"))
    except Exception:
        dt = dt.astimezone(timezone(timedelta(hours=3)))
    return dt.strftime("%d %b %Y, %H:%M EAT")


def finite_number(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def validate_number(value):
    if not finite_number(value):
        raise ValueError("Invalid number.")
    return float(value)


# ============================================================
# FLASK HEALTH SERVER
# ============================================================

flask_app = Flask(__name__)


@flask_app.get("/")
def home():
    return f"FATHER V{VERSION} - MANUAL ONLY - SQLITE", 200


@flask_app.get("/health")
def health():
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
        return jsonify({
            "status": "ok",
            "version": VERSION,
            "db_ok": True,
            "mode": "manual_only",
            "twelve_data_configured": bool(TWELVE_DATA_API_KEY),
        }), 200
    except Exception:
        logger.exception("Health check failed")
        return jsonify({
            "status": "unhealthy",
            "version": VERSION,
            "db_ok": False,
        }), 503


def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False)


# ============================================================
# DATABASE
# ============================================================

@contextmanager
def db():
    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                uid INTEGER PRIMARY KEY,
                alert_enabled INTEGER NOT NULL DEFAULT 0,
                risk_limit REAL NOT NULL DEFAULT 1000.0,
                lot_size REAL NOT NULL DEFAULT 0.01,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                logged_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                decision TEXT NOT NULL,
                price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'generated',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hunter_state (
                ticker TEXT NOT NULL,
                direction TEXT NOT NULL,
                price REAL NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (ticker, direction)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hunter_deliveries (
                uid INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                direction TEXT NOT NULL,
                status TEXT NOT NULL,
                price REAL NOT NULL,
                claimed_at TEXT NOT NULL,
                sent_at TEXT,
                alert_id INTEGER,
                PRIMARY KEY (uid, ticker, direction)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hunter_messages (
                uid INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_signal_at TEXT,
                PRIMARY KEY (uid, ticker)
            )
        """)
        cols = {
            r["name"] for r in conn.execute(
                "PRAGMA table_info(alert_stats)"
            ).fetchall()
        }
        if "status" not in cols:
            conn.execute("""
                ALTER TABLE alert_stats
                ADD COLUMN status TEXT NOT NULL DEFAULT 'generated'
            """)
        if "updated_at" not in cols:
            conn.execute("""
                ALTER TABLE alert_stats
                ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_alert_stats_uid_created
            ON alert_stats(uid, created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_alert_stats_status
            ON alert_stats(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trade_log_uid
            ON trade_log(uid)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_hunter_delivery_status
            ON hunter_deliveries(status, claimed_at)
        """)
        existing = conn.execute("SELECT uid FROM users").fetchall()
    with _known_users_lock:
        _known_users.update(int(row["uid"]) for row in existing)
    logger.info("Database initialized.")


def ensure_user(uid):
    uid = int(uid)
    with _known_users_lock:
        if uid in _known_users:
            return
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(uid, created_at) VALUES (?, ?)",
            (uid, utc_iso()),
        )
    with _known_users_lock:
        _known_users.add(uid)


def get_user(uid):
    ensure_user(uid)
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE uid=?", (int(uid),)
        ).fetchone()
        return dict(row)


def update_user(uid, field, value):
    if field not in ("alert_enabled", "risk_limit", "lot_size"):
        raise ValueError("Unsupported setting.")
    if isinstance(value, (int, float)) and not finite_number(value):
        raise ValueError("Invalid number.")
    ensure_user(uid)
    with db() as conn:
        conn.execute(
            f"UPDATE users SET {field}=? WHERE uid=?",
            (value, int(uid)),
        )


def add_trade(uid, symbol, side, quantity, price):
    quantity = validate_number(quantity)
    price = validate_number(price)
    if quantity <= 0 or price <= 0:
        raise ValueError("Quantity and price must be positive.")
    if side not in ("BUY", "SELL"):
        raise ValueError("Invalid side.")
    ensure_user(uid)
    with db() as conn:
        conn.execute("""
            INSERT INTO trade_log
            (uid, symbol, side, quantity, entry_price, logged_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (int(uid), symbol, side, quantity, price, utc_iso()))


def get_trades(uid, limit=20):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM trade_log
            WHERE uid=? ORDER BY id DESC LIMIT ?
        """, (int(uid), int(limit))).fetchall()


def clear_user_history(uid):
    with db() as conn:
        conn.execute("DELETE FROM trade_log WHERE uid=?", (int(uid),))
        conn.execute("DELETE FROM alert_stats WHERE uid=?", (int(uid),))
        conn.execute("DELETE FROM hunter_deliveries WHERE uid=?", (int(uid),))


# ============================================================
# ALERTS AND HUNTER DATABASE
# ============================================================

def create_alert_generated(conn, uid, ticker, direction, price):
    now = utc_iso()
    cur = conn.execute("""
        INSERT INTO alert_stats
        (uid, symbol, decision, price, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'generated', ?, ?)
    """, (uid, ticker, direction, price, now, now))
    return cur.lastrowid


def mark_alert_status(alert_id, status):
    if status not in ("delivered", "failed"):
        raise ValueError("Invalid status.")
    with db() as conn:
        conn.execute("""
            UPDATE alert_stats SET status=?, updated_at=? WHERE id=?
        """, (status, utc_iso(), alert_id))


def get_alert_stats(uid):
    with db() as conn:
        row = conn.execute("""
            SELECT COUNT(*) total,
              SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) delivered,
              SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed,
              SUM(CASE WHEN status='generated' THEN 1 ELSE 0 END) generated,
              SUM(CASE WHEN status='delivered' AND decision='BUY' THEN 1 ELSE 0 END) buys,
              SUM(CASE WHEN status='delivered' AND decision='SELL' THEN 1 ELSE 0 END) sells
            FROM alert_stats WHERE uid=?
        """, (int(uid),)).fetchone()
        return tuple(int(v or 0) for v in row)


def claim_hunter_delivery(uid, ticker, direction, price):
    now = utc_now()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""
            SELECT * FROM hunter_deliveries
            WHERE uid=? AND ticker=? AND direction=?
        """, (uid, ticker, direction)).fetchone()
        if row:
            if row["status"] == "delivered" and row["sent_at"]:
                sent = datetime.fromisoformat(row["sent_at"])
                if (now - sent).total_seconds() < HUNTER_COOLDOWN_SECONDS:
                    return None
            if row["status"] == "pending" and row["claimed_at"]:
                claimed = datetime.fromisoformat(row["claimed_at"])
                if (now - claimed).total_seconds() < HUNTER_PENDING_LEASE_SECONDS:
                    return None
        alert_id = create_alert_generated(conn, uid, ticker, direction, price)
        conn.execute("""
            INSERT INTO hunter_deliveries
            (uid, ticker, direction, status, price, claimed_at, sent_at, alert_id)
            VALUES (?, ?, ?, 'pending', ?, ?, NULL, ?)
            ON CONFLICT(uid, ticker, direction) DO UPDATE SET
              status='pending', price=excluded.price,
              claimed_at=excluded.claimed_at, sent_at=NULL,
              alert_id=excluded.alert_id
        """, (uid, ticker, direction, price, utc_iso(), alert_id))
        return alert_id


def finish_hunter_delivery(uid, ticker, direction, alert_id, success):
    status = "delivered" if success else "failed"
    now = utc_iso()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            UPDATE hunter_deliveries
            SET status=?, sent_at=?
            WHERE uid=? AND ticker=? AND direction=? AND alert_id=?
        """, (status, now if success else None, uid, ticker, direction, alert_id))
        conn.execute("""
            UPDATE alert_stats SET status=?, updated_at=?
            WHERE id=? AND uid=?
        """, (status, now, alert_id, uid))


def get_hunter_message(uid, ticker):
    with db() as conn:
        row = conn.execute("""
            SELECT * FROM hunter_messages WHERE uid=? AND ticker=?
        """, (uid, ticker)).fetchone()
        return dict(row) if row else None


def save_hunter_message(uid, ticker, chat_id, message_id, direction, signal_time=None):
    with db() as conn:
        conn.execute("""
            INSERT INTO hunter_messages
            (uid, ticker, chat_id, message_id, direction, updated_at, last_signal_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid, ticker) DO UPDATE SET
              chat_id=excluded.chat_id, message_id=excluded.message_id,
              direction=excluded.direction, updated_at=excluded.updated_at,
              last_signal_at=COALESCE(
                  excluded.last_signal_at, hunter_messages.last_signal_at
              )
        """, (uid, ticker, chat_id, message_id, direction, utc_iso(), signal_time))


def update_hunter_message_state(uid, ticker, direction):
    with db() as conn:
        conn.execute("""
            UPDATE hunter_messages SET direction=?, updated_at=?
            WHERE uid=? AND ticker=?
        """, (direction, utc_iso(), uid, ticker))


def delete_hunter_message(uid, ticker):
    with db() as conn:
        conn.execute(
            "DELETE FROM hunter_messages WHERE uid=? AND ticker=?",
            (uid, ticker),
        )


def delete_hunter_message_by_id(uid, chat_id, message_id):
    with db() as conn:
        conn.execute("""
            DELETE FROM hunter_messages
            WHERE uid=? AND chat_id=? AND message_id=?
        """, (uid, chat_id, message_id))


# ============================================================
# MARKET SECTORS
# ============================================================

SECTORS = {
    "bonds": {"name": "🏦 Bonds & Treasury", "tickers": ["TLT", "IEF", "SHY"]},
    "treasury": {"name": "👑 Treasury Tier", "tickers": ["TLT"]},
    "crypto": {"name": "₿ Crypto", "tickers": ["BTC-USD", "ETH-USD", "SOL-USD"]},
    "gold": {"name": "🥇 Gold & Metals", "tickers": ["GC=F", "SI=F", "GLD"]},
    "tech": {"name": "💻 Tech", "tickers": ["AAPL", "NVDA", "MSFT"]},
    "energy": {"name": "⛽ Energy", "tickers": ["CL=F", "XOM", "NG=F"]},
    "forex": {"name": "💱 Forex", "tickers": ["EURUSD=X", "GBPUSD=X", "USDJPY=X"]},
    "stocks": {"name": "📈 US Stocks", "tickers": ["SPY", "QQQ", "DIA"]},
    "nasdaq": {"name": "📊 Nasdaq", "tickers": ["QQQ", "AAPL", "TSLA"]},
    "sp500": {"name": "🏛️ S&P 500", "tickers": ["SPY", "VOO"]},
    "commodities": {"name": "🌾 Commodities", "tickers": ["CL=F", "GC=F", "SI=F"]},
    "defense": {"name": "🛡️ Defense", "tickers": ["LMT", "RTX", "NOC"]},
    "ai": {"name": "🤖 AI & Chips", "tickers": ["NVDA", "AMD", "AVGO"]},
}

TICKER_TO_SECTORS = {}
for _sector in SECTORS.values():
    for _ticker in _sector["tickers"]:
        TICKER_TO_SECTORS.setdefault(_ticker, []).append(_sector["name"])


# ============================================================
# MARKET DATA: TWELVE DATA + YAHOO
# ============================================================

def normalize_ohlcv(frame):
    if frame is None or frame.empty:
        return None
    wanted = {"OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"}
    if not isinstance(frame.columns, pd.MultiIndex):
        result = frame.copy()
        result.columns = [str(c).strip().upper() for c in result.columns]
        return result
    result = {}
    for i, column in enumerate(frame.columns):
        for part in (column if isinstance(column, tuple) else (column,)):
            name = str(part).strip().upper()
            if name in wanted and name not in result:
                result[name] = frame.iloc[:, i]
                break
    return pd.DataFrame(result, index=frame.index) if result else None


def fetch_twelve_data(ticker):
    if not TWELVE_DATA_API_KEY:
        return None, "Twelve Data API key not configured."
    symbol = "BTC/USD" if ticker == "BTC-USD" else ticker
    now = time.time()
    cache_key = ("twelve", ticker)
    with _market_lock:
        cached = _market_cache.get(cache_key)
        if cached and now - cached["fetched_at"] < TWELVE_DATA_REFRESH_SECONDS:
            return cached["frame"].copy(), None
    params = urllib.parse.urlencode({
        "symbol": symbol, "interval": "1day", "outputsize": "250",
        "apikey": TWELVE_DATA_API_KEY, "format": "JSON",
    })
    url = "https://api.twelvedata.com/time_series?" + params
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "FATHER-V15.4"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("status") == "error" or "values" not in payload:
            message = payload.get("message", "No values returned.")
            logger.warning("Twelve Data %s: %s", ticker, message)
            return None, str(message)
        frame = pd.DataFrame(payload["values"])
        frame["datetime"] = pd.to_datetime(
            frame["datetime"], utc=True, errors="coerce"
        )
        frame = frame.dropna(subset=["datetime"]).set_index("datetime")
        for col in ("open", "high", "low", "close", "volume"):
            if col not in frame.columns:
                frame[col] = 0.0 if col == "volume" else float("nan")
        frame = frame.rename(columns={
            "open": "OPEN", "high": "HIGH", "low": "LOW",
            "close": "CLOSE", "volume": "VOLUME",
        })
        for col in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.sort_index()
        if frame.empty:
            return None, "No usable candles."
        with _market_lock:
            _market_cache[cache_key] = {"frame": frame.copy(), "fetched_at": now}
        return frame, None
    except Exception as exc:
        logger.warning("Twelve Data failed for %s: %s", ticker, exc)
        return None, str(exc)


def fetch_yahoo(ticker):
    now = time.time()
    cache_key = ("yahoo", ticker)
    with _market_lock:
        cached = _market_cache.get(cache_key)
        if cached and now - cached["fetched_at"] < YAHOO_CACHE_SECONDS:
            return cached["frame"].copy()
    last_error = None
    for delay, period in ((0, "3y"), (2, "18mo")):
        if delay:
            time.sleep(delay)
        try:
            frame = yf.download(
                ticker, period=period, interval="1d",
                progress=False, auto_adjust=True,
                threads=False, timeout=15,
            )
            if frame is not None and not frame.empty:
                normalized = normalize_ohlcv(frame)
                if normalized is not None and not normalized.empty:
                    with _market_lock:
                        _market_cache[cache_key] = {
                            "frame": normalized.copy(), "fetched_at": time.time()
                        }
                    return normalized
            last_error = "Empty Yahoo response."
        except Exception as exc:
            last_error = str(exc)
            logger.warning("Yahoo failed for %s: %s", ticker, exc)
    logger.error("Yahoo failed for %s: %s", ticker, last_error)
    return None


def fetch_market_data(ticker):
    if ticker in HUNTER_TICKERS:
        frame, error = fetch_twelve_data(ticker)
        if frame is not None:
            return frame, "Twelve Data", None
        yahoo = fetch_yahoo(ticker)
        if yahoo is not None:
            return yahoo, "Yahoo Finance fallback", error
        return None, "Unavailable", error or "Both providers failed."
    yahoo = fetch_yahoo(ticker)
    if yahoo is not None:
        return yahoo, "Yahoo Finance", None
    return None, "Unavailable", "Yahoo Finance returned no data."


# ============================================================
# INDICATORS AND SIGNAL CLASSIFICATION
# ============================================================

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()
    avg_loss = loss.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    result = 100 - (100 / (1 + rs))
    result = result.mask((avg_loss == 0) & (avg_gain > 0), 100)
    result = result.mask((avg_gain == 0) & (avg_loss > 0), 0)
    return result


def calc_indicators(frame, ticker):
    frame = normalize_ohlcv(frame)
    if frame is None or frame.empty:
        return None, "Missing market data."
    if not {"HIGH", "LOW", "CLOSE"}.issubset(frame.columns):
        return None, "Missing required price data."
    close = pd.to_numeric(frame["CLOSE"], errors="coerce").dropna()
    if len(close) < 50:
        return None, "Not enough price history."
    high = pd.to_numeric(frame["HIGH"], errors="coerce").reindex(close.index)
    low = pd.to_numeric(frame["LOW"], errors="coerce").reindex(close.index)
    if high.isna().any() or low.isna().any() or (close <= 0).any():
        return None, "Invalid price data."
    try:
        latest = pd.Timestamp(close.index[-1])
        latest = (
            latest.tz_localize("UTC")
            if latest.tzinfo is None else latest.tz_convert("UTC")
        )
        age_days = (utc_now() - latest.to_pydatetime()).total_seconds() / 86400
        if age_days < -1:
            return None, "Market data timestamp is in the future."
        if age_days > MAX_MARKET_DATA_AGE_DAYS:
            return None, f"Stale market data: {age_days:.1f} days old."
    except Exception:
        return None, "Could not check data time."

    n = len(close)
    ema200_state = (
        "calculated" if n >= 400
        else "provisional" if n >= 210
        else "unavailable"
    )
    ema20_series = close.ewm(span=20, adjust=False).mean()
    ema50_series = close.ewm(span=50, adjust=False).mean()
    ema200_series = close.ewm(span=200, adjust=False).mean()
    ema200_value = (
        ema200_series.iloc[-1]
        if ema200_state != "unavailable" else float("nan")
    )
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_signal
    previous = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - previous).abs(),
        (low - previous).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    rsi_series = rsi(close)
    is_fx = ticker.endswith("=X")
    if "VOLUME" not in frame.columns:
        volume_state = "missing"
        volume = pd.Series(0.0, index=close.index)
    else:
        volume = pd.to_numeric(
            frame["VOLUME"], errors="coerce"
        ).reindex(close.index)
        if volume.isna().all() or (
            (volume.fillna(0) == 0).all() and is_fx
        ):
            volume_state = "missing"
            volume = pd.Series(0.0, index=close.index)
        elif (volume.fillna(0) == 0).all():
            volume_state = "zero"
        else:
            volume_state = "normal"
            volume = volume.fillna(0)

    avg_volume = volume.rolling(20).mean()
    vol_ratio = (
        float(volume.iloc[-1] / avg_volume.iloc[-1])
        if pd.notna(avg_volume.iloc[-1]) and avg_volume.iloc[-1] > 0
        else 0.0
    )
    values = {
        "price": close.iloc[-1],
        "previous_price": close.iloc[-2],
        "rsi": rsi_series.iloc[-1],
        "rsi_prev": rsi_series.iloc[-2],
        "ema20": ema20_series.iloc[-1],
        "ema50": ema50_series.iloc[-1],
        "ema200": ema200_value,
        "macd": macd.iloc[-1],
        "macd_signal": macd_signal.iloc[-1],
        "macd_hist": macd_hist.iloc[-1],
        "macd_hist_prev": macd_hist.iloc[-2],
        "atr": atr.iloc[-1],
        "vol": volume.iloc[-1],
        "vol_ratio": vol_ratio,
        "high": high.tail(20).max(),
        "low": low.tail(20).min(),
    }
    for key, value in values.items():
        if key == "ema200" and ema200_state == "unavailable":
            continue
        if not finite_number(value):
            return None, f"Invalid indicator: {key}"
    if values["price"] <= 0 or values["atr"] <= 0:
        return None, "Invalid price or ATR."
    result = {
        k: float(v) for k, v in values.items()
        if k != "ema200" or ema200_state != "unavailable"
    }
    result["ema200"] = (
        float(ema200_value)
        if ema200_state != "unavailable" else float("nan")
    )
    result.update({
        "ema200_state": ema200_state,
        "volume_state": volume_state,
        "bars": n,
        "age_days": age_days,
        "candle_time": latest.isoformat(),
    })
    return result, None


def promptguard_check(data):
    if data["price"] <= 0 or data["atr"] <= 0:
        return False, "Blocked: invalid price or ATR."
    if data["volume_state"] == "zero":
        return False, "Blocked: zero volume."
    if data["volume_state"] == "missing":
        return True, "Pass: volume unavailable; not used as confirmation."
    return True, "Pass."


def multipath_vote(data):
    """
    Three evidence pillars, not three independent predictions:
    1. Trend: price/EMA alignment.
    2. Momentum: RSI and MACD combined as ONE pillar.
    3. Participation: volume plus MACD direction.
    Confidence is a rule score, not a probability of profit.
    """
    votes = {"BUY": 0, "SELL": 0, "HOLD": 0}

    if data["ema200_state"] != "unavailable":
        if (
            data["price"] > data["ema50"]
            and data["ema20"] > data["ema50"]
            and data["price"] > data["ema200"]
        ):
            votes["BUY"] += 1
        elif (
            data["price"] < data["ema50"]
            and data["ema20"] < data["ema50"]
            and data["price"] < data["ema200"]
        ):
            votes["SELL"] += 1
        else:
            votes["HOLD"] += 1
    else:
        if data["price"] > data["ema50"] and data["ema20"] > data["ema50"]:
            votes["BUY"] += 1
        elif data["price"] < data["ema50"] and data["ema20"] < data["ema50"]:
            votes["SELL"] += 1
        else:
            votes["HOLD"] += 1

    if data["rsi"] >= 50 and data["macd_hist"] > 0:
        votes["BUY"] += 1
    elif data["rsi"] < 50 and data["macd_hist"] < 0:
        votes["SELL"] += 1
    else:
        votes["HOLD"] += 1

    if data["volume_state"] == "missing":
        votes["HOLD"] += 1
    elif data["vol_ratio"] >= 1 and data["macd_hist"] > 0:
        votes["BUY"] += 1
    elif data["vol_ratio"] >= 1 and data["macd_hist"] < 0:
        votes["SELL"] += 1
    else:
        votes["HOLD"] += 1
    return votes


def classify_signal(data, votes):
    """
    Direction and entry quality are separated.
    Extreme RSI blocks chasing an entry in the same direction.
    Reversal watch requires improving momentum, not RSI alone.
    """
    if data["rsi"] <= RSI_OVERSOLD:
        improving = (
            data["rsi"] > data["rsi_prev"]
            and data["macd_hist"] > data["macd_hist_prev"]
        )
        return (
            "REVERSAL WATCH" if improving else "OVERSOLD",
            "Downward pressure is extended. Avoid chasing a fresh SELL; "
            "wait for a bounce and bearish rejection."
            if not improving else
            "Oversold momentum is improving. Possible reversal, but wait "
            "for price confirmation before considering a BUY."
        )

    if data["rsi"] >= RSI_OVERBOUGHT:
        weakening = (
            data["rsi"] < data["rsi_prev"]
            and data["macd_hist"] < data["macd_hist_prev"]
        )
        return (
            "REVERSAL WATCH" if weakening else "OVERBOUGHT",
            "Upward pressure is extended. Avoid chasing a fresh BUY; "
            "wait for a pullback and bullish support."
            if not weakening else
            "Overbought momentum is weakening. Possible reversal, but wait "
            "for price confirmation before considering a SELL."
        )

    maximum = max(votes.values())
    if maximum < 2 or votes["HOLD"] == maximum:
        return "HOLD", "No clear entry. Wait for stronger agreement."
    winner = "BUY" if votes["BUY"] > votes["SELL"] else "SELL"
    if winner == "BUY" and votes["BUY"] < 2:
        return "HOLD", "Bullish evidence is not strong enough."
    if winner == "SELL" and votes["SELL"] < 2:
        return "HOLD", "Bearish evidence is not strong enough."
    return winner, "Trend and entry checks agree. Confirm broker price and risk."


def analyze_ticker(ticker):
    try:
        frame, source, fetch_error = fetch_market_data(ticker)
        data, error = calc_indicators(frame, ticker)
        if data is None:
            return {
                "ticker": ticker, "blocked": True,
                "block_reason": error or fetch_error or "No reliable data.",
                "d": None, "votes": None, "winner": "BLOCKED",
                "shadow": "BLOCK", "pg": "BLOCK",
                "entry": None, "stop": None, "target": None,
                "source": source, "analysis_time": utc_iso(),
            }

        prompt_ok, prompt_message = promptguard_check(data)
        votes = multipath_vote(data)
        signal, signal_reason = classify_signal(data, votes)
        blocked = not prompt_ok

        if blocked:
            return {
                "ticker": ticker, "blocked": True,
                "block_reason": prompt_message,
                "d": data, "votes": votes, "winner": "BLOCKED",
                "shadow": "BLOCK", "shadow_msg": prompt_message,
                "pg": prompt_message, "signal_reason": signal_reason,
                "entry": None, "stop": None, "target": None,
                "source": source, "analysis_time": utc_iso(),
            }

        entry = stop = target = None
        if signal in ("BUY", "SELL"):
            entry = data["price"]
            risk_distance = STOP_ATR_MULTIPLE * data["atr"]
            if signal == "BUY":
                stop = entry - risk_distance
                target = entry + REWARD_RISK_RATIO * risk_distance
            else:
                stop = entry + risk_distance
                target = entry - REWARD_RISK_RATIO * risk_distance

            if (
                not finite_number(entry) or not finite_number(stop)
                or not finite_number(target)
                or entry <= 0 or stop <= 0 or target <= 0
            ):
                return {
                    "ticker": ticker, "blocked": True,
                    "block_reason": "Invalid calculated entry, stop or target.",
                    "d": data, "votes": votes, "winner": "BLOCKED",
                    "shadow": "BLOCK", "shadow_msg": "Risk check failed.",
                    "pg": prompt_message, "entry": None,
                    "stop": None, "target": None, "source": source,
                    "analysis_time": utc_iso(),
                }

        return {
            "ticker": ticker, "blocked": False, "block_reason": None,
            "d": data, "votes": votes, "winner": signal,
            "shadow": "PASS" if signal in ("BUY", "SELL") else "WATCH",
            "shadow_msg": signal_reason, "pg": prompt_message,
            "signal_reason": signal_reason,
            "entry": entry, "stop": stop, "target": target,
            "source": source, "analysis_time": utc_iso(),
        }
    except Exception:
        logger.exception("Analysis failed for %s", ticker)
        return {
            "ticker": ticker, "blocked": True,
            "block_reason": "Analysis error.",
            "d": None, "votes": None, "winner": "BLOCKED",
            "shadow": "BLOCK", "pg": "BLOCK",
            "entry": None, "stop": None, "target": None,
            "source": "Unavailable", "analysis_time": utc_iso(),
        }


# ============================================================
# MESSAGE FORMATTING
# ============================================================

def format_one(result, sector_name):
    ticker = result.get("ticker", "?")
    source = result.get("source", "Unknown")
    analysis_time = result.get("analysis_time", utc_iso())
    if not result or result.get("d") is None:
        return (
            f"⛔ BLOCKED: {ticker}\n"
            f"Reason: {result.get('block_reason', 'No reliable data.')}\n"
            f"Source: {source}\n"
            f"Checked: {local_time(analysis_time)}\n"
            "No approved signal."
        )

    data = result["d"]
    if result.get("blocked"):
        return (
            f"⛔ BLOCKED: {ticker}\n"
            f"Reason: {result.get('block_reason')}\n"
            f"Provider: {source}\n"
            f"Latest candle: {local_time(data['candle_time'])}\n"
            f"Checked: {local_time(analysis_time)}\n"
            "No approved signal."
        )

    action = result["winner"]
    emoji = {
        "BUY": "🟢", "SELL": "🔴", "OVERSOLD": "🟠",
        "OVERBOUGHT": "🟠", "REVERSAL WATCH": "👀", "HOLD": "⏸️",
    }.get(action, "⛔")

    ema200_text = (
        "N/A" if data["ema200_state"] == "unavailable"
        else f"{data['ema200']:.5g}"
        + (" (provisional)" if data["ema200_state"] == "provisional" else "")
    )
    volume_text = (
        "Unavailable" if data["volume_state"] == "missing"
        else f"{data['vol_ratio']:.2f}x"
    )
    lines = [
        f"{emoji} FATHER | {sector_name}",
        f"Symbol: {ticker} | Daily candles",
        f"Decision: {action}",
        f"Reason: {result.get('signal_reason', '')}",
        f"Price: {data['price']:.5g}",
        f"RSI: {data['rsi']:.1f}",
        f"EMA 20/50/200: {data['ema20']:.5g} / "
        f"{data['ema50']:.5g} / {ema200_text}",
        f"MACD histogram: {data['macd_hist']:.5g}",
        f"ATR: {data['atr']:.5g}",
        f"Volume ratio: {volume_text}",
        f"Evidence pillars: {result['votes']}",
    ]
    if result["stop"] is not None:
        distance = abs(result["entry"] - result["stop"])
        pct = distance / result["entry"] * 100
        warning = " Review position size." if pct > MAX_STOP_PERCENT_WARNING else ""
        lines.extend([
            f"Reference entry: {result['entry']:.5g}",
            f"Stop loss: {result['stop']:.5g} ({pct:.2f}% away).{warning}",
            f"Take profit: {result['target']:.5g}",
            "Risk plan: 1.5 ATR stop / 2:1 target",
        ])
    lines.extend([
        f"Provider: {source}",
        f"Latest candle: {local_time(data['candle_time'])}",
        f"Analysis time: {local_time(analysis_time)}",
        f"Last updated: {local_time()}",
        "",
        "Daily data is not a live broker quote.",
        "Check your broker price and spread. Manual trading only.",
        "No order was placed. Signals are unvalidated research rules.",
    ])
    return "\n".join(lines)


def format_hunter_state(ticker, result, state):
    sector = "Crypto" if ticker == "BTC-USD" else "Bonds & Treasury"
    text = "🔔 FATHER HUNTER\n" + format_one(result, sector)
    return text + f"\nLast updated: {local_time()}"


# ============================================================
# TELEGRAM MENUS AND SAFE MESSAGE HELPERS
# ============================================================

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔎 Scan a sector", callback_data="menu_scan"),
            InlineKeyboardButton("📊 Scan all", callback_data="sector_all"),
        ],
        [
            InlineKeyboardButton("🔔 Alerts", callback_data="menu_alerts"),
            InlineKeyboardButton("📈 Statistics", callback_data="menu_stats"),
        ],
        [InlineKeyboardButton("ℹ️ Help", callback_data="menu_help")],
    ])


def back_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Back", callback_data="menu_back"),
        InlineKeyboardButton("🏠 Home", callback_data="menu_home"),
    ]])


def sector_keyboard():
    rows, row = [], []
    for key, sector in SECTORS.items():
        row.append(InlineKeyboardButton(
            sector["name"], callback_data=f"sector_{key}"
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(
        "📊 Scan all 13", callback_data="sector_all"
    )])
    rows.append([
        InlineKeyboardButton("⬅️ Back", callback_data="menu_back"),
        InlineKeyboardButton("🏠 Home", callback_data="menu_home"),
    ])
    return InlineKeyboardMarkup(rows)


def alerts_keyboard(enabled):
    label = "🔕 Turn alerts OFF" if enabled else "🔔 Turn alerts ON"
    action = "alerts_off" if enabled else "alerts_on"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=action)],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="menu_back"),
            InlineKeyboardButton("🏠 Home", callback_data="menu_home"),
        ],
    ])


async def retry_after_delay(exc):
    delay = exc.retry_after
    if hasattr(delay, "total_seconds"):
        delay = delay.total_seconds()
    await asyncio.sleep(float(delay) + 1)


async def safe_send(bot, chat_id, text, reply_markup=None):
    text = text or "No message."
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > MAX_TELEGRAM_LENGTH:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:MAX_TELEGRAM_LENGTH])
            line = line[MAX_TELEGRAM_LENGTH:]
        candidate = current + "\n" + line if current else line
        if len(candidate) > MAX_TELEGRAM_LENGTH:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    sent = None
    for index, chunk in enumerate(chunks):
        while True:
            try:
                sent = await bot.send_message(
                    chat_id=chat_id, text=chunk,
                    reply_markup=reply_markup if index == len(chunks) - 1 else None,
                )
                break
            except RetryAfter as exc:
                await retry_after_delay(exc)
    return sent


async def safe_reply(message, text, reply_markup=None):
    return await safe_send(
        message.get_bot(), message.chat_id, text, reply_markup
    )


async def safe_edit(query, text, reply_markup=None):
    try:
        await query.edit_message_text(
            text=text[:MAX_TELEGRAM_LENGTH],
            reply_markup=reply_markup,
        )
        return True
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return True
        logger.warning("Button edit failed: %s", exc)
        return False
    except (Forbidden, TimedOut, NetworkError):
        logger.exception("Telegram edit failed.")
        return False


async def scan_sector(message, key):
    sector = SECTORS[key]
    await safe_reply(message, f"Scanning {sector['name']}...")
    for ticker in sector["tickers"]:
        result = await asyncio.to_thread(analyze_ticker, ticker)
        await safe_reply(
            message, format_one(result, sector["name"]), back_keyboard()
        )


async def scan_all(message):
    await safe_reply(message, "Scanning all 13 sectors...")
    results = {}
    for ticker in TICKER_TO_SECTORS:
        results[ticker] = await asyncio.to_thread(analyze_ticker, ticker)
    for sector in SECTORS.values():
        for ticker in sector["tickers"]:
            await safe_reply(
                message, format_one(results[ticker], sector["name"]),
                back_keyboard(),
            )
    await safe_reply(message, "Scan complete.", main_menu_keyboard())


# ============================================================
# COMMANDS
# ============================================================

async def setup_commands(app):
    await app.bot.set_my_commands([
        BotCommand("start", "Open FATHER"),
        BotCommand("scan", "Analyse a market"),
        BotCommand("alerts", "Manage research alerts"),
        BotCommand("history", "View trade history"),
        BotCommand("help", "How to use FATHER"),
    ])


async def start_cmd(update, context):
    ensure_user(update.effective_user.id)
    await safe_reply(
        update.message,
        f"👑 FATHER V{VERSION}\nChoose what you want to do.\n\n"
        "FATHER analyses. You make the trading decisions.",
        main_menu_keyboard(),
    )


async def help_cmd(update, context):
    await safe_reply(
        update.message,
        "FATHER HELP\n\n"
        "/scan - choose a market sector\n"
        "/scan all - scan all sectors\n"
        "/alerts - manage Hunter alerts\n"
        "/history - view your trade log\n\n"
        "Advanced commands remain available: /auto, /benchmark, "
        "/logtrade, /setlimit, /setlot, /pnl, /alertstats.\n\n"
        "Hunter watches BTC-USD and TLT. Manual trading only.",
        back_keyboard(),
    )


async def scan_cmd(update, context):
    if context.args:
        key = context.args[0].lower()
        if key == "all":
            await scan_all(update.message)
            return
        if key in SECTORS:
            await scan_sector(update.message, key)
            return
    await safe_reply(update.message, "Choose a sector:", sector_keyboard())


async def all_cmd(update, context):
    await scan_all(update.message)


async def alerts_cmd(update, context):
    enabled = bool(get_user(update.effective_user.id)["alert_enabled"])
    await safe_reply(
        update.message,
        f"Research alerts: {'ON' if enabled else 'OFF'}.\n"
        "Hunter watches BTC-USD and TLT.",
        alerts_keyboard(enabled),
    )


async def auto_cmd(update, context):
    uid = update.effective_user.id
    if not context.args or context.args[0].lower() not in ("on", "off"):
        current = get_user(uid)["alert_enabled"]
        await safe_reply(
            update.message,
            f"Alerts are {'ON' if current else 'OFF'}. Use /auto on or /auto off.",
            back_keyboard(),
        )
        return
    enabled = int(context.args[0].lower() == "on")
    update_user(uid, "alert_enabled", enabled)
    await safe_reply(
        update.message, f"Research alerts are now {'ON' if enabled else 'OFF'}.",
        back_keyboard(),
    )


async def myalerts_cmd(update, context):
    enabled = get_user(update.effective_user.id)["alert_enabled"]
    await safe_reply(
        update.message,
        f"Research alerts: {'ON' if enabled else 'OFF'}.\n"
        "Hunter watches BTC-USD and TLT.",
        back_keyboard(),
    )


async def alertstats_cmd(update, context):
    vals = get_alert_stats(update.effective_user.id)
    await safe_reply(
        update.message,
        "HUNTER STATISTICS\n"
        f"Total: {vals[0]}\nDelivered: {vals[1]}\n"
        f"Failed: {vals[2]}\nGenerated: {vals[3]}\n"
        f"BUY: {vals[4]}\nSELL: {vals[5]}",
        back_keyboard(),
    )


async def benchmark_cmd(update, context):
    output = ["FATHER RESEARCH CHECK"]
    for ticker in ("TLT", "BTC-USD", "SPY", "GC=F"):
        result = await asyncio.to_thread(analyze_ticker, ticker)
        output.append(
            f"{ticker}: {result['winner']} | Evidence: {result['votes']} | "
            f"Provider: {result.get('source')}"
        )
    output.append("\nThis is not a historical profitability test.")
    await safe_reply(update.message, "\n".join(output), back_keyboard())


async def logtrade_cmd(update, context):
    try:
        if len(context.args) != 4:
            raise ValueError()
        symbol = context.args[0].upper()
        side = context.args[1].upper()
        quantity = validate_number(context.args[2])
        price = validate_number(context.args[3])
        add_trade(update.effective_user.id, symbol, side, quantity, price)
        await safe_reply(
            update.message, f"Recorded: {side} {quantity:g} {symbol} @ {price:g}",
            back_keyboard(),
        )
    except Exception:
        await safe_reply(update.message, "Use: /logtrade EURUSD BUY 0.01 1.0850")


async def history_cmd(update, context):
    uid = update.effective_user.id
    if context.args and context.args[0].lower() == "clear":
        if len(context.args) < 2 or context.args[1].lower() != "confirm":
            await safe_reply(
                update.message,
                "This clears your trade and alert history.\n"
                "Confirm with /history clear confirm",
                back_keyboard(),
            )
            return
        clear_user_history(uid)
        await safe_reply(update.message, "Your history has been cleared.")
        return
    rows = get_trades(uid)
    if not rows:
        await safe_reply(update.message, "No manual trades recorded.", back_keyboard())
        return
    lines = ["YOUR TRADE HISTORY"]
    for row in rows:
        lines.append(
            f"#{row['id']} {row['side']} {row['quantity']:g} "
            f"{row['symbol']} @ {row['entry_price']:g} | "
            f"{local_time(row['logged_at'])}"
        )
    await safe_reply(update.message, "\n".join(lines), back_keyboard())


async def setlimit_cmd(update, context):
    try:
        value = validate_number(context.args[0])
        if value <= 0:
            raise ValueError()
        update_user(update.effective_user.id, "risk_limit", value)
        await safe_reply(
            update.message,
            f"Reference limit set to {value:g}. It does not control broker risk.",
            back_keyboard(),
        )
    except Exception:
        await safe_reply(update.message, "Use: /setlimit 500")


async def setlot_cmd(update, context):
    try:
        value = validate_number(context.args[0])
        if value <= 0:
            raise ValueError()
        update_user(update.effective_user.id, "lot_size", value)
        await safe_reply(
            update.message, f"Reference lot set to {value:g}. Check broker limits.",
            back_keyboard(),
        )
    except Exception:
        await safe_reply(update.message, "Use: /setlot 0.01")


async def pnl_cmd(update, context):
    rows = get_trades(update.effective_user.id, 10000)
    settings = get_user(update.effective_user.id)
    await safe_reply(
        update.message,
        f"Manual records: {len(rows)}\n"
        f"Reference limit: {settings['risk_limit']:g}\n"
        f"Lot size: {settings['lot_size']:g}\n"
        "Broker profit/loss is not connected.",
        back_keyboard(),
    )


async def autotrade_cmd(update, context):
    await safe_reply(
        update.message,
        "Automated trading is disabled.\nNo MT5 or Exness connection. No orders are placed.",
        back_keyboard(),
    )


async def mt5info_cmd(update, context):
    await safe_reply(
        update.message,
        "MT5 is not connected. Never send passwords through Telegram.",
        back_keyboard(),
    )


# ============================================================
# BUTTON ACTIONS
# ============================================================

async def callback_handler(update, context):
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except BadRequest:
        pass
    except Exception:
        logger.exception("Could not acknowledge callback.")

    data = query.data or ""
    uid = query.from_user.id
    message = query.message

    if message is None:
        return

    if data == "menu_back":
        try:
            delete_hunter_message_by_id(uid, message.chat_id, message.message_id)
            await message.delete()
        except BadRequest as exc:
            if "message to delete not found" not in str(exc).lower():
                logger.warning("Back delete failed: %s", exc)
        except Exception:
            logger.exception("Could not delete Back message.")
        return

    if data == "menu_home":
        await safe_edit(
            query, f"👑 FATHER V{VERSION}\nChoose what you want to do.",
            main_menu_keyboard(),
        )
        return

    if data == "menu_scan":
        await safe_edit(query, "Choose a sector:", sector_keyboard())
        return

    if data == "menu_alerts":
        enabled = bool(get_user(uid)["alert_enabled"])
        await safe_edit(
            query,
            f"Research alerts: {'ON' if enabled else 'OFF'}.\n"
            "Hunter watches BTC-USD and TLT.",
            alerts_keyboard(enabled),
        )
        return

    if data in ("alerts_on", "alerts_off"):
        enabled = int(data == "alerts_on")
        update_user(uid, "alert_enabled", enabled)
        await safe_edit(
            query,
            f"Research alerts are now {'ON' if enabled else 'OFF'}.\n"
            "Hunter watches BTC-USD and TLT.",
            alerts_keyboard(bool(enabled)),
        )
        return

    if data == "menu_stats":
        vals = get_alert_stats(uid)
        await safe_edit(
            query,
            "HUNTER STATISTICS\n"
            f"Total: {vals[0]}\nDelivered: {vals[1]}\n"
            f"Failed: {vals[2]}\nGenerated: {vals[3]}\n"
            f"BUY: {vals[4]}\nSELL: {vals[5]}",
            back_keyboard(),
        )
        return

    if data == "menu_help":
        await safe_edit(
            query,
            "FATHER analyses markets. You make all trading decisions.\n\n"
            "/scan - scan a sector\n/alerts - manage Hunter alerts\n"
            "/history - view manual trades\n\nManual trading only.",
            back_keyboard(),
        )
        return

    if data == "sector_all":
        await safe_edit(query, "Scanning all sectors...")
        await scan_all(message)
        return

    if data.startswith("sector_"):
        key = data[len("sector_"):]
        if key in SECTORS:
            await safe_edit(query, f"Scanning {SECTORS[key]['name']}...")
            await scan_sector(message, key)
        return


async def handle_text(update, context):
    message_text = (update.message.text or "").lower().strip()
    # Match complete sector words only. In particular, "ai" must not
    # match ordinary words such as "wait", "again", or "maintain".
    tokens = set(re.findall(r"[a-z0-9]+", message_text))
    for key, sector in SECTORS.items():
        name_tokens = set(re.findall(r"[a-z0-9]+", sector["name"].lower()))
        if key in tokens or (name_tokens and name_tokens.issubset(tokens)):
            await scan_sector(update.message, key)
            return
    await safe_reply(
        update.message,
        "Use /scan to choose a market sector.",
        main_menu_keyboard(),
    )


# ============================================================
# HUNTER: UPDATE EXISTING MESSAGES
# ============================================================

async def edit_or_send_hunter(app, uid, ticker, result):
    direction = result["winner"] if not result.get("blocked") else "BLOCKED"
    if direction not in (
        "BUY", "SELL", "OVERSOLD", "OVERBOUGHT", "REVERSAL WATCH", "HOLD"
    ):
        direction = "BLOCKED"

    text = format_hunter_state(ticker, result, direction)
    existing = get_hunter_message(uid, ticker)

    if existing:
        try:
            await app.bot.edit_message_text(
                chat_id=existing["chat_id"],
                message_id=existing["message_id"],
                text=text[:MAX_TELEGRAM_LENGTH],
                reply_markup=main_menu_keyboard(),
            )
            update_hunter_message_state(uid, ticker, direction)
            return
        except RetryAfter as exc:
            await retry_after_delay(exc)
            return
        except BadRequest as exc:
            error_text = str(exc).lower()
            if "message is not modified" in error_text:
                update_hunter_message_state(uid, ticker, direction)
                return
            if (
                "message to edit not found" in error_text
                or "message_id_invalid" in error_text
                or "message can't be edited" in error_text
            ):
                delete_hunter_message(uid, ticker)
                existing = None
            else:
                logger.warning(
                    "Could not edit Hunter message %s/%s: %s", uid, ticker, exc
                )
                return
        except Forbidden:
            logger.warning("User %s blocked the bot; disabling alerts.", uid)
            update_user(uid, "alert_enabled", 0)
            return
        except Exception:
            logger.exception("Hunter edit failed for %s/%s", uid, ticker)
            return

    # Only send a new message when there is a fresh actionable BUY or SELL.
    if direction not in ("BUY", "SELL"):
        return
    if not result.get("d"):
        return
    alert_id = claim_hunter_delivery(
        uid, ticker, direction, result["d"]["price"]
    )
    if alert_id is None:
        return
    try:
        sent = await app.bot.send_message(
            chat_id=uid, text=text[:MAX_TELEGRAM_LENGTH],
            reply_markup=main_menu_keyboard(),
        )
        save_hunter_message(
            uid, ticker, sent.chat_id, sent.message_id, direction, utc_iso()
        )
        finish_hunter_delivery(uid, ticker, direction, alert_id, True)
    except Forbidden:
        logger.warning("User %s blocked the bot; disabling alerts.", uid)
        update_user(uid, "alert_enabled", 0)
        finish_hunter_delivery(uid, ticker, direction, alert_id, False)
    except Exception:
        logger.exception("Hunter send failed for user %s ticker %s", uid, ticker)
        try:
            finish_hunter_delivery(uid, ticker, direction, alert_id, False)
        except Exception:
            logger.exception("Could not record delivery failure.")


async def hunter_loop(app):
    logger.info("Hunter started: %s-second analysis cycle.", SCAN_INTERVAL_SECONDS)
    while True:
        try:
            # One cycle at a time; no overlapping scans.
            async with _hunter_cycle_lock:
                with db() as conn:
                    opted_in = [
                        int(r["uid"]) for r in conn.execute(
                            "SELECT uid FROM users WHERE alert_enabled=1"
                        ).fetchall()
                    ]
                if opted_in:
                    for ticker in HUNTER_TICKERS:
                        result = await asyncio.to_thread(analyze_ticker, ticker)
                        for uid in opted_in:
                            await edit_or_send_hunter(app, uid, ticker, result)
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            logger.info("Hunter stopped.")
            raise
        except Exception:
            logger.exception("Hunter loop error.")
            await asyncio.sleep(60)


async def post_init(app):
    await setup_commands(app)
    app.create_task(hunter_loop(app))


# ============================================================
# START BOT
# ============================================================

def main():
    print(f"=== FATHER V{VERSION} STARTING ===", flush=True)
    print("SQLite only. Render Free storage is temporary.", flush=True)
    print("Manual trading only. No MT5 or Exness connection.", flush=True)
    print("Daily market candles are not guaranteed live broker quotes.", flush=True)

    init_db()
    threading.Thread(target=run_flask, daemon=True).start()

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    handlers = [
        ("start", start_cmd),
        ("help", help_cmd),
        ("scan", scan_cmd),
        ("all", all_cmd),
        ("alerts", alerts_cmd),
        ("auto", auto_cmd),
        ("myalerts", myalerts_cmd),
        ("alertstats", alertstats_cmd),
        ("benchmark", benchmark_cmd),
        ("logtrade", logtrade_cmd),
        ("history", history_cmd),
        ("setlimit", setlimit_cmd),
        ("setlot", setlot_cmd),
        ("pnl", pnl_cmd),
        ("autotrade", autotrade_cmd),
        ("mt5info", mt5info_cmd),
    ]
    for command, handler in handlers:
        app.add_handler(CommandHandler(command, handler))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    logger.info("FATHER V%s ready.", VERSION)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
