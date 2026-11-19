"""
FATHER V15.3 - Manual Trading Edition
SQLite ONLY | No PostgreSQL | No MT5 | No Exness connection
No automated order placement, modification or closure.

Render Start Command:
python -u beast_alerts_bot.py

Required environment:
BOT_TOKEN
Optional:
PORT=10000
DB_PATH=father_v15.sqlite3
TWELVE_DATA_API_KEY

Render Free local SQLite storage is TEMPORARY.
Twelve Data daily candles are not executable broker quotes.
"""

import os
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
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yfinance as yf
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter, BadRequest
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

VERSION = "15.3"
SCAN_INTERVAL_SECONDS = 60
TWELVE_DATA_REFRESH_SECONDS = 15 * 60
YAHOO_CACHE_SECONDS = 5 * 60
HUNTER_COOLDOWN_SECONDS = 6 * 3600
HUNTER_PENDING_LEASE_SECONDS = 10 * 60
MAX_TELEGRAM_LENGTH = 3800
MAX_MARKET_DATA_AGE_DAYS = 7
HUNTER_TICKERS = ("BTC-USD", "TLT")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("FATHER_V15_3")

def utc_now():
    return datetime.now(timezone.utc)

def utc_iso():
    return utc_now().isoformat()

def finite_number(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False

def validate_number(value):
    if not finite_number(value):
        raise ValueError("Invalid or non-finite number.")
    return float(value)

# ============================================================
# FLASK HEALTH SERVER
# ============================================================

flask_app = Flask(__name__)

@flask_app.get("/")
def home():
    return "FATHER V15.3 - MANUAL ONLY - SQLITE", 200

@flask_app.get("/health")
def health():
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
        return jsonify({
            "status": "ok", "version": VERSION,
            "db_ok": True, "mode": "manual_only",
            "twelve_data_configured": bool(TWELVE_DATA_API_KEY),
        }), 200
    except Exception:
        logger.exception("Health check failed")
        return jsonify({
            "status": "unhealthy", "version": VERSION,
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
                uid INTEGER NOT NULL, symbol TEXT NOT NULL,
                side TEXT NOT NULL, quantity REAL NOT NULL,
                entry_price REAL NOT NULL, logged_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid INTEGER NOT NULL, symbol TEXT NOT NULL,
                decision TEXT NOT NULL, price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'generated',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hunter_state (
                ticker TEXT NOT NULL, direction TEXT NOT NULL,
                price REAL NOT NULL, sent_at TEXT NOT NULL,
                PRIMARY KEY (ticker, direction)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hunter_deliveries (
                uid INTEGER NOT NULL, ticker TEXT NOT NULL,
                direction TEXT NOT NULL, status TEXT NOT NULL,
                price REAL NOT NULL, claimed_at TEXT NOT NULL,
                sent_at TEXT, alert_id INTEGER,
                PRIMARY KEY (uid, ticker, direction)
            )
        """)
        # One editable Telegram message per user and Hunter ticker.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hunter_messages (
                uid INTEGER NOT NULL, ticker TEXT NOT NULL,
                chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
                direction TEXT NOT NULL, updated_at TEXT NOT NULL,
                last_signal_at TEXT,
                PRIMARY KEY (uid, ticker)
            )
        """)
        # Safe migrations for databases created by earlier versions.
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(alert_stats)"
        ).fetchall()}
        if "status" not in cols:
            conn.execute(
                "ALTER TABLE alert_stats ADD COLUMN status TEXT NOT NULL DEFAULT 'generated'"
            )
        if "updated_at" not in cols:
            conn.execute(
                "ALTER TABLE alert_stats ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"
            )
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
    logger.info("Database initialized.")

def ensure_user(uid):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(uid, created_at) VALUES (?, ?)",
            (uid, utc_iso()),
        )

def get_user(uid):
    ensure_user(uid)
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
        return dict(row)

def update_user(uid, field, value):
    if field not in ("alert_enabled", "risk_limit", "lot_size"):
        raise ValueError("Unsupported setting.")
    if isinstance(value, (int, float)) and not finite_number(value):
        raise ValueError("Non-finite value rejected.")
    ensure_user(uid)
    with db() as conn:
        conn.execute(f"UPDATE users SET {field}=? WHERE uid=?", (value, uid))

def add_trade(uid, symbol, side, quantity, price):
    quantity, price = validate_number(quantity), validate_number(price)
    if quantity <= 0 or price <= 0:
        raise ValueError("Quantity and price must be positive.")
    if side not in ("BUY", "SELL"):
        raise ValueError("Side must be BUY or SELL.")
    ensure_user(uid)
    with db() as conn:
        conn.execute("""
            INSERT INTO trade_log
            (uid, symbol, side, quantity, entry_price, logged_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (uid, symbol, side, quantity, price, utc_iso()))

def get_trades(uid, limit=20):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM trade_log WHERE uid=?
            ORDER BY id DESC LIMIT ?
        """, (uid, limit)).fetchall()

def clear_user_history(uid):
    with db() as conn:
        conn.execute("DELETE FROM trade_log WHERE uid=?", (uid,))
        conn.execute("DELETE FROM alert_stats WHERE uid=?", (uid,))
        conn.execute("DELETE FROM hunter_deliveries WHERE uid=?", (uid,))
        # Keep hunter_messages so the bot can still edit the existing Telegram
        # message instead of unexpectedly creating a duplicate.

# ============================================================
# ALERT LIFECYCLE AND HUNTER CLAIMS
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
        raise ValueError("Invalid alert status.")
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
        """, (uid,)).fetchone()
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
            UPDATE hunter_deliveries SET status=?, sent_at=?
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
              last_signal_at=COALESCE(excluded.last_signal_at, hunter_messages.last_signal_at)
        """, (uid, ticker, chat_id, message_id, direction, utc_iso(), signal_time))

def update_hunter_message_state(uid, ticker, direction):
    with db() as conn:
        conn.execute("""
            UPDATE hunter_messages SET direction=?, updated_at=?
            WHERE uid=? AND ticker=?
        """, (direction, utc_iso(), uid, ticker))

def delete_hunter_message(uid, ticker):
    with db() as conn:
        conn.execute("DELETE FROM hunter_messages WHERE uid=? AND ticker=?", (uid, ticker))

# ============================================================
# SECTORS
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
for sector in SECTORS.values():
    for ticker in sector["tickers"]:
        TICKER_TO_SECTORS.setdefault(ticker, []).append(sector["name"])

# ============================================================
# MARKET DATA: TWELVE DATA + YAHOO, CACHED
# ============================================================

_market_cache = {}
_market_lock = threading.Lock()

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
        "symbol": symbol,
        "interval": "1day",
        "outputsize": "250",
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON",
    })
    url = "https://api.twelvedata.com/time_series?" + params
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "FATHER-V15.3"}
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("status") == "error" or "values" not in payload:
            message = payload.get("message", "No values in Twelve Data response.")
            logger.warning("Twelve Data %s: %s", ticker, message)
            return None, str(message)
        frame = pd.DataFrame(payload["values"])
        frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["datetime"]).set_index("datetime")
        for col in ("open", "high", "low", "close", "volume"):
            if col not in frame.columns:
                frame[col] = float("nan") if col != "volume" else 0.0
        frame = frame.rename(columns={
            "open": "OPEN", "high": "HIGH", "low": "LOW",
            "close": "CLOSE", "volume": "VOLUME",
        })
        for col in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.sort_index()
        if frame.empty:
            return None, "Twelve Data returned no usable candles."
        with _market_lock:
            _market_cache[cache_key] = {"frame": frame.copy(), "fetched_at": now}
        return frame, None
    except Exception as exc:
        logger.warning("Twelve Data request failed for %s: %s", ticker, exc)
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
                if normalized is not None:
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
    # Twelve Data primary for the two Hunter instruments. Yahoo is fallback.
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
# INDICATORS AND SIGNAL ENGINE
# ============================================================

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
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
        return None, "Missing required OHLC fields."
    close = pd.to_numeric(frame["CLOSE"], errors="coerce").dropna()
    if len(close) < 50:
        return None, "Insufficient price history."
    high = pd.to_numeric(frame["HIGH"], errors="coerce").reindex(close.index)
    low = pd.to_numeric(frame["LOW"], errors="coerce").reindex(close.index)
    if high.isna().any() or low.isna().any() or (close <= 0).any():
        return None, "Incomplete or invalid OHLC data."
    try:
        latest = pd.Timestamp(close.index[-1])
        latest = latest.tz_localize("UTC") if latest.tzinfo is None else latest.tz_convert("UTC")
        age_days = (utc_now() - latest.to_pydatetime()).total_seconds() / 86400
        if age_days < -1:
            return None, "Market data timestamp is in the future."
        if age_days > MAX_MARKET_DATA_AGE_DAYS:
            return None, f"Stale market data: latest candle is {age_days:.1f} days old."
    except Exception:
        return None, "Unable to validate market-data timestamp."

    n = len(close)
    ema200_state = "calculated" if n >= 400 else ("provisional" if n >= 210 else "unavailable")
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1] if ema200_state != "unavailable" else float("nan")
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_signal
    previous = close.shift(1)
    tr = pd.concat([
        (high-low).abs(), (high-previous).abs(), (low-previous).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()

    is_fx = ticker.endswith("=X")
    if "VOLUME" not in frame.columns:
        volume_state = "missing"
        volume = pd.Series(0.0, index=close.index)
    else:
        volume = pd.to_numeric(frame["VOLUME"], errors="coerce").reindex(close.index)
        if volume.isna().all() or ((volume.fillna(0) == 0).all() and is_fx):
            volume_state = "missing"
            volume = pd.Series(0.0, index=close.index)
        elif (volume.fillna(0) == 0).all():
            volume_state = "zero"
        else:
            volume_state = "normal"
            volume = volume.fillna(0)
    avg_volume = volume.rolling(20).mean()
    vol_ratio = float(volume.iloc[-1] / avg_volume.iloc[-1]) if pd.notna(avg_volume.iloc[-1]) and avg_volume.iloc[-1] > 0 else 0.0

    values = {
        "price": close.iloc[-1], "rsi": rsi(close).iloc[-1],
        "ema20": ema20.iloc[-1], "ema50": ema50.iloc[-1],
        "ema200": ema200, "macd": macd.iloc[-1],
        "macd_signal": macd_signal.iloc[-1], "macd_hist": macd_hist.iloc[-1],
        "atr": atr.iloc[-1], "vol": volume.iloc[-1],
        "vol_ratio": vol_ratio, "high": high.tail(20).max(),
        "low": low.tail(20).min(),
    }
    for key, value in values.items():
        if key == "ema200" and ema200_state == "unavailable":
            continue
        if not finite_number(value):
            return None, f"Invalid indicator: {key}"
    if values["price"] <= 0 or values["atr"] <= 0:
        return None, "Invalid price or ATR."
    result = {k: float(v) for k, v in values.items() if k != "ema200" or ema200_state != "unavailable"}
    result["ema200"] = float(ema200) if ema200_state != "unavailable" else float("nan")
    result.update({
        "ema200_state": ema200_state, "volume_state": volume_state,
        "bars": n, "age_days": age_days,
        "candle_time": latest.isoformat(),
    })
    return result, None

def promptguard_check(data):
    if data["price"] <= 0 or data["atr"] <= 0:
        return False, "BLOCK - invalid price or ATR."
    if data["volume_state"] == "missing":
        return True, "PASS - volume unavailable; participation vote is HOLD."
    if data["volume_state"] == "zero":
        return False, "BLOCK - zero volume on non-FX instrument."
    return True, "PASS"

def multipath_vote(data):
    votes = {"BUY": 0, "SELL": 0, "HOLD": 0}
    if data["ema200_state"] != "unavailable":
        if data["price"] > data["ema50"] and data["ema20"] > data["ema50"] and data["price"] > data["ema200"]:
            votes["BUY"] += 1
        elif data["price"] < data["ema50"] and data["ema20"] < data["ema50"] and data["price"] < data["ema200"]:
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

def shadow_check(votes):
    maximum = max(votes.values())
    if maximum < 2:
        return "BLOCK", "Shadow: no majority; fewer than two matching votes.", "HOLD"
    winner = next(k for k, v in votes.items() if v == maximum)
    if winner == "HOLD":
        return "BLOCK", "Shadow: HOLD majority; no trade.", "HOLD"
    return "PASS", "Shadow: majority check passed.", winner

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
        shadow, shadow_message, winner = shadow_check(votes)
        blocked = not prompt_ok or shadow == "BLOCK"
        if blocked:
            return {
                "ticker": ticker, "blocked": True,
                "block_reason": prompt_message if not prompt_ok else shadow_message,
                "d": data, "votes": votes, "winner": "BLOCKED",
                "shadow": shadow, "shadow_msg": shadow_message,
                "pg": prompt_message, "entry": None, "stop": None,
                "target": None, "source": source, "analysis_time": utc_iso(),
            }
        entry = data["price"]
        if winner == "BUY":
            stop = min(data["low"], entry - 1.5 * data["atr"])
            target = entry + 2 * (entry - stop)
        elif winner == "SELL":
            stop = max(data["high"], entry + 1.5 * data["atr"])
            target = entry - 2 * (stop - entry)
        else:
            stop = target = None
        return {
            "ticker": ticker, "blocked": False, "block_reason": None,
            "d": data, "votes": votes, "winner": winner,
            "shadow": shadow, "shadow_msg": shadow_message,
            "pg": prompt_message, "entry": entry, "stop": stop,
            "target": target, "source": source, "analysis_time": utc_iso(),
        }
    except Exception:
        logger.exception("Analysis failed for %s", ticker)
        return {
            "ticker": ticker, "blocked": True,
            "block_reason": "Analysis exception.", "d": None,
            "votes": None, "winner": "BLOCKED", "shadow": "BLOCK",
            "pg": "BLOCK", "entry": None, "stop": None,
            "target": None, "source": "Unavailable",
            "analysis_time": utc_iso(),
        }

# ============================================================
# FORMATTING
# ============================================================

def format_one(result, sector_name):
    ticker = result.get("ticker", "?")
    source = result.get("source", "Unknown")
    analysis_time = result.get("analysis_time", utc_iso())
    if not result or result.get("d") is None:
        return (
            f"⛔ BLOCKED - {ticker}\n"
            f"Reason: {result.get('block_reason', 'No reliable data.')}\n"
            f"Source: {source}\nAnalysis checked: {analysis_time}\n"
            "No signal approved. No entry, stop or target."
        )
    data = result["d"]
    if result.get("blocked"):
        return (
            f"⛔ BLOCKED - {ticker} | {sector_name}\n"
            f"Reason: {result.get('block_reason')}\n"
            f"Raw votes: {result.get('votes')}\n"
            f"PromptGuard: {result.get('pg')}\nShadow: {result.get('shadow')}\n"
            f"Bars: {data['bars']} | EMA200: {data['ema200_state']}\n"
            f"Volume: {data['volume_state']}\nProvider: {source}\n"
            f"Latest candle: {data['candle_time']}\n"
            f"Analysis checked: {analysis_time}\n"
            "NOT an approved signal. No entry/stop/target."
        )
    action = result["winner"]
    emoji = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⏸️"
    if data["ema200_state"] == "unavailable":
        ema200_text = "N/A"
    elif data["ema200_state"] == "provisional":
        ema200_text = f"{data['ema200']:.5g} (provisional)"
    else:
        ema200_text = f"{data['ema200']:.5g}"
    volume_text = "N/A - unavailable" if data["volume_state"] == "missing" else f"{data['vol_ratio']:.2f} ({data['volume_state']})"
    lines = [
        f"{emoji} FATHER V15.3 | {sector_name}",
        f"Symbol: {ticker} | Daily",
        "Sectors: " + ", ".join(TICKER_TO_SECTORS.get(ticker, [])),
        f"Price: {data['price']:.5g}",
        f"RSI: {data['rsi']:.1f}",
        f"EMA 20/50/200: {data['ema20']:.5g} / {data['ema50']:.5g} / {ema200_text}",
        f"MACD: {data['macd']:.5g} | Signal: {data['macd_signal']:.5g} | Hist: {data['macd_hist']:.5g}",
        f"ATR: {data['atr']:.5g}",
        f"Volume ratio: {volume_text}",
        f"Votes: {result['votes']}",
        f"Decision: {action}",
        f"PromptGuard: {result['pg']}",
        f"Shadow: {result['shadow']}",
    ]
    if action in ("BUY", "SELL") and result["stop"] is not None:
        distance = abs(result["entry"] - result["stop"])
        pct = distance / result["entry"] * 100
        atr_multiple = distance / data["atr"]
        warning = " WIDE STOP: review position size." if atr_multiple > 3 or pct > 8 else ""
        lines.extend([
            f"Reference entry: {result['entry']:.5g}",
            f"Reference stop: {result['stop']:.5g} ({pct:.2f}% away, {atr_multiple:.1f}x ATR).{warning}",
            f"Reference target: {result['target']:.5g}",
        ])
    lines.extend([
        f"Provider: {source}",
        f"Latest daily candle: {data['candle_time']}",
        f"Analysis checked: {analysis_time}",
        "", "RESEARCH ONLY.",
        "Daily candles are not guaranteed live prices or Exness bid/ask quotes.",
        "Confirm the live broker chart, spreads and specifications.",
        "No order was placed. Manual-only build.",
    ])
    return "\n".join(lines)

def format_hunter_state(ticker, result, state):
    sector = "Crypto" if ticker == "BTC-USD" else "Bonds & Treasury"
    if state == "BUY" or state == "SELL":
        return "🔔 FATHER HUNTER - " + format_one(result, sector)
    data = result.get("d")
    reason = result.get("block_reason") or "No qualifying directional majority."
    if data:
        reason = result.get("block_reason") or "Signal no longer qualifies; do not treat previous direction as active."
        candle = data.get("candle_time", "unknown")
        price = f"{data['price']:.5g}"
        source = result.get("source", "Unknown")
    else:
        candle, price, source = "unavailable", "unavailable", result.get("source", "Unavailable")
    return (
        f"⏸️ FATHER HUNTER UPDATE - {ticker}\n"
        f"State: {state}\nReason: {reason}\n"
        f"Latest candle: {candle}\nProvider: {source}\n"
        f"Price reference: {price}\nAnalysis checked: {result.get('analysis_time', utc_iso())}\n\n"
        "Previous directional signal is no longer confirmed. "
        "This is a research update, not an instruction to close or open a trade."
    )

# ============================================================
# TELEGRAM UI AND SCANNING
# ============================================================

async def reply_with_retry(message, text, reply_markup=None):
    while True:
        try:
            await message.reply_text(text, reply_markup=reply_markup)
            return
        except RetryAfter as exc:
            delay = exc.retry_after
            if hasattr(delay, "total_seconds"):
                delay = delay.total_seconds()
            await asyncio.sleep(float(delay) + 1)

async def safe_reply(message, text, reply_markup=None):
    if not text:
        return
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > MAX_TELEGRAM_LENGTH:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:MAX_TELEGRAM_LENGTH])
            line = line[MAX_TELEGRAM_LENGTH:]
        if not line:
            continue
        candidate = current + "\n" + line if current else line
        if len(candidate) > MAX_TELEGRAM_LENGTH:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    for i, chunk in enumerate(chunks):
        await reply_with_retry(message, chunk, reply_markup if i == len(chunks)-1 else None)

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Scan sectors", callback_data="menu_scan"),
         InlineKeyboardButton("📊 Scan all 13", callback_data="sector_all")],
        [InlineKeyboardButton("🔔 My alerts", callback_data="menu_alerts"),
         InlineKeyboardButton("📈 Alert stats", callback_data="menu_stats")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="menu_help")],
    ])

def sector_keyboard():
    rows, row = [], []
    for key, sector in SECTORS.items():
        row.append(InlineKeyboardButton(sector["name"], callback_data=f"sector_{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Main menu", callback_data="menu_home")])
    rows.append([InlineKeyboardButton("📊 SCAN ALL 13", callback_data="sector_all")])
    return InlineKeyboardMarkup(rows)

async def scan_sector(message, key):
    sector = SECTORS[key]
    await safe_reply(message, f"FATHER scanning {sector['name']}...")
    for index, ticker in enumerate(sector["tickers"]):
        result = await asyncio.to_thread(analyze_ticker, ticker)
        keyboard = main_menu_keyboard() if index == len(sector["tickers"])-1 else None
        await safe_reply(message, format_one(result, sector["name"]), keyboard)

async def scan_all(message):
    await safe_reply(message, "FATHER scanning all 13 sectors. Fetching unique tickers...")
    results = {}
    for ticker in TICKER_TO_SECTORS:
        results[ticker] = await asyncio.to_thread(analyze_ticker, ticker)
    for sector in SECTORS.values():
        for ticker in sector["tickers"]:
            await safe_reply(message, format_one(results[ticker], sector["name"]))
    await safe_reply(message, "Scan finished.", main_menu_keyboard())

# ============================================================
# COMMANDS
# ============================================================

async def start_cmd(update, context):
    ensure_user(update.effective_user.id)
    text = (
        "👑 FATHER V15.3 - MANUAL EDITION\n\n"
        "FATHER analyses. YOU place trades.\n\n"
        "/help - commands\n/scan - choose a sector\n/scan all - scan all sectors\n"
        "/all - scan all sectors\n/auto on|off - Hunter alerts\n"
        "/myalerts - alert preference\n/alertstats - Hunter statistics\n"
        "/benchmark - sample research scan\n/logtrade SYMBOL BUY|SELL QTY PRICE\n"
        "/history - manual trade log\n/history clear - deletion confirmation\n"
        "/history clear confirm - confirm deletion\n/setlimit AMOUNT\n/setlot SIZE\n"
        "/pnl - manual record count\n/autotrade - disabled\n/mt5info - MT5 status\n\n"
        "Hunter watches BTC-USD and TLT only.\n"
        "Analysis refresh: 60 seconds. Twelve Data cache: 15 minutes.\n"
        "Market candles may not change every minute.\n"
        "SQLite only; Render Free storage is temporary.\n"
        "No MT5, Exness login or automated orders."
    )
    await safe_reply(update.message, text, main_menu_keyboard())

async def help_cmd(update, context):
    await start_cmd(update, context)

async def scan_cmd(update, context):
    if context.args:
        key = context.args[0].lower()
        if key == "all":
            await scan_all(update.message)
            return
        if key in SECTORS:
            await scan_sector(update.message, key)
            return
    await safe_reply(update.message, "Choose a research sector:", sector_keyboard())

async def all_cmd(update, context):
    await scan_all(update.message)

async def callback_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data == "menu_home":
        await query.edit_message_text("FATHER V15.3 - Main menu", reply_markup=main_menu_keyboard())
    elif data == "menu_scan":
        await query.edit_message_text("Choose a sector:", reply_markup=sector_keyboard())
    elif data == "menu_alerts":
        enabled = get_user(query.from_user.id)["alert_enabled"]
        await query.edit_message_text(
            f"Research alerts: {'ON' if enabled else 'OFF'}.\nUse /auto on or /auto off.",
            reply_markup=main_menu_keyboard(),
        )
    elif data == "menu_stats":
        vals = get_alert_stats(query.from_user.id)
        await query.edit_message_text(
            f"📊 HUNTER ALERT STATISTICS\nTotal: {vals[0]}\nDelivered: {vals[1]}\nFailed: {vals[2]}\nGenerated: {vals[3]}\nBUY: {vals[4]}\nSELL: {vals[5]}",
            reply_markup=main_menu_keyboard(),
        )
    elif data == "menu_help":
        await query.edit_message_text("Use /help for commands.", reply_markup=main_menu_keyboard())
    elif data == "sector_all":
        await query.edit_message_text("FATHER scanning all sectors...")
        await scan_all(query.message)
    elif data.startswith("sector_"):
        key = data[len("sector_"):]
        if key in SECTORS:
            await query.edit_message_text(f"FATHER scanning {SECTORS[key]['name']}...")
            await scan_sector(query.message, key)

async def auto_cmd(update, context):
    uid = update.effective_user.id
    if not context.args or context.args[0].lower() not in ("on", "off"):
        current = get_user(uid)["alert_enabled"]
        await safe_reply(update.message, f"Your research alerts are {'ON' if current else 'OFF'}.\nUse /auto on or /auto off.")
        return
    enabled = int(context.args[0].lower() == "on")
    update_user(uid, "alert_enabled", enabled)
    await safe_reply(update.message, f"Research alerts {'ON' if enabled else 'OFF'}.")

async def myalerts_cmd(update, context):
    enabled = get_user(update.effective_user.id)["alert_enabled"]
    await safe_reply(update.message, f"Your research alerts: {'ON' if enabled else 'OFF'}.\nHunter watches BTC-USD and TLT only.")

async def alertstats_cmd(update, context):
    total, delivered, failed, generated, buys, sells = get_alert_stats(update.effective_user.id)
    await safe_reply(update.message, (
        "📊 HUNTER ALERT STATISTICS\n"
        f"Total records: {total}\nDelivered: {delivered}\nFailed: {failed}\n"
        f"Generated/pending: {generated}\nDelivered BUY: {buys}\nDelivered SELL: {sells}\n"
        "Scope: Hunter alerts only, not manual /scan."
    ))

async def benchmark_cmd(update, context):
    output = ["FATHER RESEARCH CHECK"]
    for ticker in ("TLT", "BTC-USD", "SPY", "GC=F"):
        result = await asyncio.to_thread(analyze_ticker, ticker)
        output.append(f"{ticker}: {result['winner']} | Votes: {result['votes']} | Shadow: {result['shadow']} | Source: {result.get('source')}")
    output.append("\nNot a historical accuracy benchmark or profitability claim.")
    await safe_reply(update.message, "\n".join(output))

async def logtrade_cmd(update, context):
    try:
        if len(context.args) != 4:
            raise ValueError()
        symbol, side = context.args[0].upper(), context.args[1].upper()
        quantity, price = validate_number(context.args[2]), validate_number(context.args[3])
        add_trade(update.effective_user.id, symbol, side, quantity, price)
        await safe_reply(update.message, f"Manual trade recorded: {side} {quantity:g} {symbol} @ {price:g}")
    except Exception:
        await safe_reply(update.message, "Usage: /logtrade EURUSD BUY 0.01 1.0850\nQuantity and price must be finite and positive.")

async def history_cmd(update, context):
    uid = update.effective_user.id
    if context.args and context.args[0].lower() == "clear":
        if len(context.args) < 2 or context.args[1].lower() != "confirm":
            await safe_reply(update.message, "This deletes your trade history, alert statistics and Hunter delivery history.\nConfirm with /history clear confirm")
            return
        clear_user_history(uid)
        await safe_reply(update.message, "Your history has been cleared. Other users are unaffected.")
        return
    rows = get_trades(uid)
    if not rows:
        await safe_reply(update.message, "No manual trades logged.")
        return
    lines = ["YOUR MANUAL TRADE LOG"]
    for row in rows:
        lines.append(f"#{row['id']} {row['side']} {row['quantity']:g} {row['symbol']} @ {row['entry_price']:g} | {row['logged_at'][:16]}")
    await safe_reply(update.message, "\n".join(lines))

async def setlimit_cmd(update, context):
    try:
        value = validate_number(context.args[0])
        if value <= 0:
            raise ValueError()
        update_user(update.effective_user.id, "risk_limit", value)
        await safe_reply(update.message, f"Reference limit set to {value:g}. This does not enforce broker risk.")
    except Exception:
        await safe_reply(update.message, "Usage: /setlimit 500 - finite positive number required.")

async def setlot_cmd(update, context):
    try:
        value = validate_number(context.args[0])
        if value <= 0:
            raise ValueError()
        update_user(update.effective_user.id, "lot_size", value)
        await safe_reply(update.message, f"Reference lot set to {value:g}. Confirm broker minimum and volume step.")
    except Exception:
        await safe_reply(update.message, "Usage: /setlot 0.01 - finite positive number required.")

async def pnl_cmd(update, context):
    rows = get_trades(update.effective_user.id, 10000)
    settings = get_user(update.effective_user.id)
    await safe_reply(update.message, f"Manual records: {len(rows)}\nReference limit: {settings['risk_limit']:g}\nLot: {settings['lot_size']:g}\nBroker P&L unavailable. No MT5 connection.")

async def autotrade_cmd(update, context):
    await safe_reply(update.message, "AUTOMATED TRADING DISABLED.\nNo MT5, no Exness connection and no orders.")

async def mt5info_cmd(update, context):
    await safe_reply(update.message, "MT5 status: NOT CONNECTED.\nNo MT5 login and no order execution.\nNever send passwords through Telegram.")

async def handle_text(update, context):
    message_text = (update.message.text or "").lower().strip()
    for key, sector in SECTORS.items():
        if key in message_text or sector["name"].lower() in message_text:
            await scan_sector(update.message, key)
            return
    await safe_reply(update.message, "Send /scan and choose a sector, or type gold or crypto.")

# ============================================================
# HUNTER: EDIT EXISTING MESSAGES, DON'T SPAM NEW ONES
# ============================================================

async def edit_or_send_hunter(app, uid, ticker, result):
    direction = result["winner"] if not result.get("blocked") else "HOLD"
    if direction not in ("BUY", "SELL"):
        direction = "BLOCKED" if result.get("d") is None else "HOLD"
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
            delay = exc.retry_after
            if hasattr(delay, "total_seconds"):
                delay = delay.total_seconds()
            await asyncio.sleep(float(delay) + 1)
            return
        except BadRequest as exc:
            # Do not blindly send a duplicate after arbitrary edit failures.
            error_text = str(exc).lower()
            if "message is not modified" in error_text:
                update_hunter_message_state(uid, ticker, direction)
                return
            if "message to edit not found" in error_text or "message_id_invalid" in error_text:
                delete_hunter_message(uid, ticker)
                existing = None
            else:
                logger.warning("Could not edit Hunter message for %s/%s: %s", uid, ticker, exc)
                return
        except Exception:
            logger.exception("Temporary Hunter edit failure for %s/%s", uid, ticker)
            return

    # New message is created only for a fresh BUY/SELL signal.
    if direction not in ("BUY", "SELL"):
        return
    price = result["d"]["price"]
    alert_id = claim_hunter_delivery(uid, ticker, direction, price)
    if alert_id is None:
        return
    try:
        sent = await app.bot.send_message(
            chat_id=uid, text=text[:MAX_TELEGRAM_LENGTH],
            reply_markup=main_menu_keyboard(),
        )
        save_hunter_message(uid, ticker, sent.chat_id, sent.message_id, direction, utc_iso())
        finish_hunter_delivery(uid, ticker, direction, alert_id, True)
    except Exception:
        logger.exception("Hunter send failed for user %s ticker %s", uid, ticker)
        try:
            finish_hunter_delivery(uid, ticker, direction, alert_id, False)
        except Exception:
            logger.exception("Could not record Hunter delivery failure.")

async def hunter_loop(app):
    logger.info("Hunter loop started: %s-second analysis cycle.", SCAN_INTERVAL_SECONDS)
    while True:
        try:
            with db() as conn:
                opted_in = [r["uid"] for r in conn.execute(
                    "SELECT uid FROM users WHERE alert_enabled=1"
                ).fetchall()]
            if opted_in:
                for ticker in HUNTER_TICKERS:
                    result = await asyncio.to_thread(analyze_ticker, ticker)
                    for uid in opted_in:
                        await edit_or_send_hunter(app, uid, ticker, result)
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            logger.info("Hunter loop cancelled.")
            raise
        except Exception:
            logger.exception("Hunter loop error.")
            await asyncio.sleep(60)

async def post_init(app):
    app.create_task(hunter_loop(app))

# ============================================================
# MAIN
# ============================================================

def main():
    print("=== FATHER V15.3 MANUAL EDITION STARTING ===", flush=True)
    print("SQLite ONLY - Render Free storage is temporary.", flush=True)
    print("No MT5, no Exness connection, no automated orders.", flush=True)
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    handlers = [
        ("start", start_cmd), ("help", help_cmd),
        ("scan", scan_cmd), ("all", all_cmd),
        ("auto", auto_cmd), ("myalerts", myalerts_cmd),
        ("alertstats", alertstats_cmd), ("benchmark", benchmark_cmd),
        ("logtrade", logtrade_cmd), ("history", history_cmd),
        ("setlimit", setlimit_cmd), ("setlot", setlot_cmd),
        ("pnl", pnl_cmd), ("autotrade", autotrade_cmd),
        ("mt5info", mt5info_cmd),
    ]
    for command, handler in handlers:
        app.add_handler(CommandHandler(command, handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("FATHER V15.3 ready.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
