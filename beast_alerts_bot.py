
"""
FATHER V15.2.1 - Manual Trading Edition
SQLite ONLY | No PostgreSQL | No MT5 | No Exness connection
No automated order placement, modification or closure.

Render Start Command:
python -u beast_alerts_bot.py

Required environment:
BOT_TOKEN
Optional:
PORT=10000
DB_PATH=father_v15.sqlite3

Render Free local SQLite storage is TEMPORARY.
"""

import os
import asyncio
import logging
import sqlite3
import threading
import math
import time

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yfinance as yf

from flask import Flask, jsonify

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", "10000"))
DB_PATH = os.environ.get("DB_PATH", "father_v15.sqlite3").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing.")

if not 1 <= PORT <= 65535:
    raise RuntimeError("Invalid PORT.")

if not DB_PATH:
    raise RuntimeError("DB_PATH cannot be empty.")

SCAN_INTERVAL_SECONDS = 900
HUNTER_COOLDOWN_SECONDS = 6 * 3600
HUNTER_PENDING_LEASE_SECONDS = 10 * 60
MAX_TELEGRAM_LENGTH = 3800
MAX_MARKET_DATA_AGE_DAYS = 7

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("FATHER_V15_2_1")


def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def finite_number(value):
    try:
        number = float(value)
        return math.isfinite(number)
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
    return "FATHER V15.2.1 - MANUAL ONLY - TEMPORARY SQLITE", 200


@flask_app.get("/health")
def health():
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()

        return jsonify({
            "status": "ok",
            "version": "15.2.1",
            "db_ok": True,
            "mode": "manual_only",
        }), 200

    except Exception:
        logger.exception("Health check failed")

        return jsonify({
            "status": "unhealthy",
            "version": "15.2.1",
            "db_ok": False,
        }), 503


def run_flask():
    flask_app.run(
        host="0.0.0.0",
        port=PORT,
        use_reloader=False,
    )


# ============================================================
# DATABASE
# ============================================================

@contextmanager
def db():
    parent = os.path.dirname(DB_PATH)

    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")

        yield conn
        conn.commit()

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise

    finally:
        try:
            conn.close()
        except Exception:
            pass


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

        # Legacy table retained for compatibility.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hunter_state (
                ticker TEXT NOT NULL,
                direction TEXT NOT NULL,
                price REAL NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (ticker, direction)
            )
        """)

        # Per-user Hunter delivery state.
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

        # IMPORTANT: migrate columns BEFORE creating indexes.
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(alert_stats)"
            ).fetchall()
        }

        if "status" not in columns:
            conn.execute("""
                ALTER TABLE alert_stats
                ADD COLUMN status TEXT NOT NULL DEFAULT 'generated'
            """)

        if "updated_at" not in columns:
            conn.execute("""
                ALTER TABLE alert_stats
                ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''
            """)

        # Create indexes only after migration.
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

    logger.info("Database initialized and migrations checked.")


def ensure_user(uid):
    with db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO users(uid, created_at)
            VALUES (?, ?)
            """,
            (uid, utc_iso()),
        )


def get_user(uid):
    ensure_user(uid)

    with db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE uid=?",
            (uid,),
        ).fetchone()

        return dict(row)


def update_user(uid, field, value):
    if field not in ("alert_enabled", "risk_limit", "lot_size"):
        raise ValueError("Unsupported setting.")

    if isinstance(value, (int, float)) and not finite_number(value):
        raise ValueError("Non-finite value rejected.")

    ensure_user(uid)

    with db() as conn:
        conn.execute(
            f"UPDATE users SET {field}=? WHERE uid=?",
            (value, uid),
        )


def add_trade(uid, symbol, side, quantity, price):
    quantity = validate_number(quantity)
    price = validate_number(price)

    if quantity <= 0 or price <= 0:
        raise ValueError("Quantity and price must be positive.")

    if side not in ("BUY", "SELL"):
        raise ValueError("Side must be BUY or SELL.")

    ensure_user(uid)

    with db() as conn:
        conn.execute(
            """
            INSERT INTO trade_log
            (uid, symbol, side, quantity, entry_price, logged_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (uid, symbol, side, quantity, price, utc_iso()),
        )


def get_trades(uid, limit=20):
    with db() as conn:
        return conn.execute(
            """
            SELECT * FROM trade_log
            WHERE uid=?
            ORDER BY id DESC LIMIT ?
            """,
            (uid, limit),
        ).fetchall()


def clear_user_history(uid):
    with db() as conn:
        conn.execute("DELETE FROM trade_log WHERE uid=?", (uid,))
        conn.execute("DELETE FROM alert_stats WHERE uid=?", (uid,))
        conn.execute(
            "DELETE FROM hunter_deliveries WHERE uid=?",
            (uid,),
        )


# ============================================================
# ALERT LIFECYCLE
# ============================================================

def create_alert_generated(conn, uid, ticker, direction, price):
    now = utc_iso()

    cur = conn.execute(
        """
        INSERT INTO alert_stats
        (uid, symbol, decision, price, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'generated', ?, ?)
        """,
        (uid, ticker, direction, price, now, now),
    )

    return cur.lastrowid


def mark_alert_status(alert_id, status):
    if status not in ("delivered", "failed"):
        raise ValueError("Invalid alert status.")

    with db() as conn:
        conn.execute(
            """
            UPDATE alert_stats
            SET status=?, updated_at=?
            WHERE id=?
            """,
            (status, utc_iso(), alert_id),
        )


def get_alert_stats(uid):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) AS delivered,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status='generated' THEN 1 ELSE 0 END) AS generated,
                SUM(CASE WHEN status='delivered' AND decision='BUY' THEN 1 ELSE 0 END) AS buys,
                SUM(CASE WHEN status='delivered' AND decision='SELL' THEN 1 ELSE 0 END) AS sells
            FROM alert_stats
            WHERE uid=?
            """,
            (uid,),
        ).fetchone()

        return tuple(int(v or 0) for v in rows)


# ============================================================
# HUNTER: ATOMIC PER-USER CLAIMS AND RECOVERY
# ============================================================

def claim_hunter_delivery(uid, ticker, direction, price):
    """
    Serialized claim operation.

    Delivered alerts are suppressed for the cooldown period.
    Recent pending claims are suppressed.
    Failed or expired pending claims can be retried.

    The alert_stats generated record and Hunter claim are created
    in the same transaction.
    """

    now = utc_now()
    now_text = now.isoformat()

    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            """
            SELECT * FROM hunter_deliveries
            WHERE uid=? AND ticker=? AND direction=?
            """,
            (uid, ticker, direction),
        ).fetchone()

        if row:
            status = row["status"]

            if status == "delivered" and row["sent_at"]:
                sent = datetime.fromisoformat(row["sent_at"])

                if (now - sent).total_seconds() < HUNTER_COOLDOWN_SECONDS:
                    return None

            if status == "pending" and row["claimed_at"]:
                claimed = datetime.fromisoformat(row["claimed_at"])

                if (
                    now - claimed
                ).total_seconds() < HUNTER_PENDING_LEASE_SECONDS:
                    return None

        alert_id = create_alert_generated(
            conn,
            uid,
            ticker,
            direction,
            price,
        )

        conn.execute(
            """
            INSERT INTO hunter_deliveries
            (uid, ticker, direction, status, price,
             claimed_at, sent_at, alert_id)
            VALUES (?, ?, ?, 'pending', ?, ?, NULL, ?)

            ON CONFLICT(uid, ticker, direction)
            DO UPDATE SET
                status='pending',
                price=excluded.price,
                claimed_at=excluded.claimed_at,
                sent_at=NULL,
                alert_id=excluded.alert_id
            """,
            (
                uid,
                ticker,
                direction,
                price,
                now_text,
                alert_id,
            ),
        )

        return alert_id


def finish_hunter_delivery(
    uid,
    ticker,
    direction,
    alert_id,
    success,
):
    status = "delivered" if success else "failed"
    now = utc_iso()

    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        conn.execute(
            """
            UPDATE hunter_deliveries
            SET status=?, sent_at=?
            WHERE uid=? AND ticker=? AND direction=?
              AND alert_id=?
            """,
            (
                status,
                now if success else None,
                uid,
                ticker,
                direction,
                alert_id,
            ),
        )

        conn.execute(
            """
            UPDATE alert_stats
            SET status=?, updated_at=?
            WHERE id=? AND uid=?
            """,
            (status, now, alert_id, uid),
        )


# ============================================================
# SECTORS
# ============================================================

SECTORS = {
    "bonds": {
        "name": "🏦 Bonds & Treasury",
        "tickers": ["TLT", "IEF", "SHY"],
    },
    "treasury": {
        "name": "👑 Treasury Tier",
        "tickers": ["TLT"],
    },
    "crypto": {
        "name": "₿ Crypto",
        "tickers": ["BTC-USD", "ETH-USD", "SOL-USD"],
    },
    "gold": {
        "name": "🥇 Gold & Metals",
        "tickers": ["GC=F", "SI=F", "GLD"],
    },
    "tech": {
        "name": "💻 Tech",
        "tickers": ["AAPL", "NVDA", "MSFT"],
    },
    "energy": {
        "name": "⛽ Energy",
        "tickers": ["CL=F", "XOM", "NG=F"],
    },
    "forex": {
        "name": "💱 Forex",
        "tickers": ["EURUSD=X", "GBPUSD=X", "USDJPY=X"],
    },
    "stocks": {
        "name": "📈 US Stocks",
        "tickers": ["SPY", "QQQ", "DIA"],
    },
    "nasdaq": {
        "name": "📊 Nasdaq",
        "tickers": ["QQQ", "AAPL", "TSLA"],
    },
    "sp500": {
        "name": "🏛️ S&P 500",
        "tickers": ["SPY", "VOO"],
    },
    "commodities": {
        "name": "🌾 Commodities",
        "tickers": ["CL=F", "GC=F", "SI=F"],
    },
    "defense": {
        "name": "🛡️ Defense",
        "tickers": ["LMT", "RTX", "NOC"],
    },
    "ai": {
        "name": "🤖 AI & Chips",
        "tickers": ["NVDA", "AMD", "AVGO"],
    },
}

TICKER_TO_SECTORS = {}

for sector in SECTORS.values():
    for ticker in sector["tickers"]:
        TICKER_TO_SECTORS.setdefault(ticker, []).append(
            sector["name"]
        )


# ============================================================
# MARKET DATA NORMALIZATION AND INDICATORS
# ============================================================

def normalize_yf(frame):
    """
    Handles flat columns and both Yahoo MultiIndex arrangements.
    Selects one series per OHLCV field.
    """

    if frame is None or frame.empty:
        return None

    wanted = {"OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"}

    if not isinstance(frame.columns, pd.MultiIndex):
        result = frame.copy()
        result.columns = [
            str(c).strip().upper() for c in result.columns
        ]
        return result

    result = {}

    for i, column in enumerate(frame.columns):
        parts = (
            column if isinstance(column, tuple) else (column,)
        )

        for part in parts:
            name = str(part).strip().upper()

            if name in wanted and name not in result:
                result[name] = frame.iloc[:, i]
                break

    if not result:
        return None

    return pd.DataFrame(result, index=frame.index)


def rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    result = 100 - (100 / (1 + rs))

    result = result.mask(
        (avg_loss == 0) & (avg_gain > 0),
        100,
    )

    result = result.mask(
        (avg_gain == 0) & (avg_loss > 0),
        0,
    )

    return result


def calc_indicators(frame, ticker):
    frame = normalize_yf(frame)

    if frame is None or frame.empty:
        return None, "Missing market data."

    required = {"HIGH", "LOW", "CLOSE"}

    if not required.issubset(set(frame.columns)):
        return None, "Missing required OHLC fields."

    close = pd.to_numeric(
        frame["CLOSE"],
        errors="coerce",
    ).dropna()

    if len(close) < 50:
        return None, "Insufficient price history."

    high = pd.to_numeric(
        frame["HIGH"],
        errors="coerce",
    ).reindex(close.index)

    low = pd.to_numeric(
        frame["LOW"],
        errors="coerce",
    ).reindex(close.index)

    if high.isna().any() or low.isna().any():
        return None, "Incomplete OHLC data."

    if (close <= 0).any():
        return None, "Invalid price data."

    # Freshness validation.
    try:
        latest = pd.Timestamp(close.index[-1])

        if latest.tzinfo is None:
            latest = latest.tz_localize("UTC")
        else:
            latest = latest.tz_convert("UTC")

        age_days = (
            utc_now() - latest.to_pydatetime()
        ).total_seconds() / 86400

        if age_days < -1:
            return None, "Market data timestamp is in the future."

        if age_days > MAX_MARKET_DATA_AGE_DAYS:
            return None, (
                f"Stale market data: latest bar is "
                f"{age_days:.1f} days old."
            )

    except Exception:
        return None, "Unable to validate market-data timestamp."

    n = len(close)

    if n >= 400:
        ema200_state = "calculated"
    elif n >= 210:
        ema200_state = "provisional"
    else:
        ema200_state = "unavailable"

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    ema200 = (
        close.ewm(span=200, adjust=False).mean().iloc[-1]
        if ema200_state != "unavailable"
        else float("nan")
    )

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()

    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(
        span=9,
        adjust=False,
    ).mean()

    macd_hist = macd_line - macd_signal

    previous = close.shift(1)

    true_range = pd.concat(
        [
            (high - low).abs(),
            (high - previous).abs(),
            (low - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14,
    ).mean()

    # Volume classification.
    is_fx = ticker.endswith("=X")

    if "VOLUME" not in frame.columns:
        volume_state = "missing"
        volume = pd.Series(0.0, index=close.index)

    else:
        volume = pd.to_numeric(
            frame["VOLUME"],
            errors="coerce",
        ).reindex(close.index)

        if volume.isna().all():
            volume_state = "missing"
            volume = pd.Series(0.0, index=close.index)

        elif (volume.fillna(0) == 0).all() and is_fx:
            # Yahoo FX volume is not centralized exchange volume.
            volume_state = "missing"
            volume = pd.Series(0.0, index=close.index)

        elif (volume.fillna(0) == 0).all():
            volume_state = "zero"

        else:
            volume_state = "normal"
            volume = volume.fillna(0)

    average_volume = volume.rolling(20).mean()

    latest_volume = float(volume.iloc[-1])

    if (
        pd.notna(average_volume.iloc[-1])
        and average_volume.iloc[-1] > 0
    ):
        volume_ratio = (
            latest_volume / average_volume.iloc[-1]
        )
    else:
        volume_ratio = 0.0

    values = {
        "price": close.iloc[-1],
        "rsi": rsi(close).iloc[-1],
        "ema20": ema20.iloc[-1],
        "ema50": ema50.iloc[-1],
        "ema200": ema200,
        "macd": macd_line.iloc[-1],
        "macd_signal": macd_signal.iloc[-1],
        "macd_hist": macd_hist.iloc[-1],
        "atr": atr.iloc[-1],
        "vol": latest_volume,
        "vol_ratio": volume_ratio,
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
        key: float(value)
        for key, value in values.items()
        if key != "ema200"
        or ema200_state != "unavailable"
    }

    result["ema200"] = (
        float(ema200)
        if ema200_state != "unavailable"
        else float("nan")
    )

    result["ema200_state"] = ema200_state
    result["volume_state"] = volume_state
    result["bars"] = n
    result["age_days"] = age_days

    return result, None


# ============================================================
# PROMPTGUARD AND SHADOW
# ============================================================

def promptguard_check(data):
    if data["price"] <= 0 or data["atr"] <= 0:
        return False, "PromptGuard: invalid price or ATR."

    if data["volume_state"] == "missing":
        return True, (
            "PASS - volume unavailable; participation votes HOLD."
        )

    if data["volume_state"] == "zero":
        return False, "PromptGuard: zero volume on non-FX instrument."

    return True, "PASS"


def multipath_vote(data):
    votes = {
        "BUY": 0,
        "SELL": 0,
        "HOLD": 0,
    }

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
        if (
            data["price"] > data["ema50"]
            and data["ema20"] > data["ema50"]
        ):
            votes["BUY"] += 1

        elif (
            data["price"] < data["ema50"]
            and data["ema20"] < data["ema50"]
        ):
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

    elif (
        data["vol_ratio"] >= 1
        and data["macd_hist"] > 0
    ):
        votes["BUY"] += 1

    elif (
        data["vol_ratio"] >= 1
        and data["macd_hist"] < 0
    ):
        votes["SELL"] += 1

    else:
        votes["HOLD"] += 1

    return votes


def shadow_check(votes):
    maximum = max(votes.values())

    if maximum < 2:
        return (
            "BLOCK",
            "Shadow: no majority; fewer than two matching votes.",
            "HOLD",
        )

    winner = next(
        key for key, value in votes.items()
        if value == maximum
    )

    if winner == "HOLD":
        return (
            "BLOCK",
            "Shadow: HOLD majority; no trade.",
            "HOLD",
        )

    return "PASS", "Shadow: majority check passed.", winner


# ============================================================
# YAHOO FINANCE FETCHING
# ============================================================

def fetch_yahoo(ticker):
    last_error = None

    attempts = [
        (0, "3y"),
        (2, "18mo"),
        (5, "18mo"),
    ]

    for delay, period in attempts:
        if delay:
            time.sleep(delay)

        try:
            frame = yf.download(
                ticker,
                period=period,
                interval="1d",
                progress=False,
                auto_adjust=True,
                threads=False,
                timeout=15,
            )

            if frame is not None and not frame.empty:
                return frame

            last_error = "Empty Yahoo response."

        except Exception as exc:
            last_error = str(exc)
            logger.warning(
                "Yahoo attempt failed for %s: %s",
                ticker,
                exc,
            )

    logger.error(
        "Yahoo failed for %s: %s",
        ticker,
        last_error,
    )

    return None


def analyze_ticker(ticker):
    try:
        frame = fetch_yahoo(ticker)
        data, error = calc_indicators(frame, ticker)

        if data is None:
            return {
                "ticker": ticker,
                "blocked": True,
                "block_reason": error,
                "d": None,
                "votes": None,
                "winner": "BLOCKED",
                "shadow": "BLOCK",
                "pg": "BLOCK",
                "entry": None,
                "stop": None,
                "target": None,
            }

        prompt_ok, prompt_message = promptguard_check(data)

        votes = multipath_vote(data)

        shadow, shadow_message, winner = shadow_check(votes)

        blocked = not prompt_ok or shadow == "BLOCK"

        if blocked:
            return {
                "ticker": ticker,
                "blocked": True,
                "block_reason": (
                    prompt_message
                    if not prompt_ok
                    else shadow_message
                ),
                "d": data,
                "votes": votes,
                "winner": "BLOCKED",
                "shadow": shadow,
                "shadow_msg": shadow_message,
                "pg": prompt_message,
                "entry": None,
                "stop": None,
                "target": None,
            }

        entry = data["price"]

        if winner == "BUY":
            stop = min(
                data["low"],
                entry - 1.5 * data["atr"],
            )
            target = entry + 2 * (entry - stop)

        elif winner == "SELL":
            stop = max(
                data["high"],
                entry + 1.5 * data["atr"],
            )
            target = entry - 2 * (stop - entry)

        else:
            stop = None
            target = None

        return {
            "ticker": ticker,
            "blocked": False,
            "block_reason": None,
            "d": data,
            "votes": votes,
            "winner": winner,
            "shadow": shadow,
            "shadow_msg": shadow_message,
            "pg": prompt_message,
            "entry": entry,
            "stop": stop,
            "target": target,
        }

    except Exception:
        logger.exception("Analysis failed for %s", ticker)

        return {
            "ticker": ticker,
            "blocked": True,
            "block_reason": "Analysis exception.",
            "d": None,
            "votes": None,
            "winner": "BLOCKED",
            "shadow": "BLOCK",
            "pg": "BLOCK",
            "entry": None,
            "stop": None,
            "target": None,
        }


# ============================================================
# RESULT FORMATTING
# ============================================================

def format_one(result, sector_name):
    ticker = result.get("ticker", "?")

    if not result or result.get("d") is None:
        return (
            f"⛔ BLOCKED - {ticker}\n"
            f"Reason: {result.get('block_reason', 'No reliable data.')}\n"
            "No signal approved. No entry, stop or target."
        )

    data = result["d"]

    if result.get("blocked"):
        return (
            f"⛔ BLOCKED - {ticker} | {sector_name}\n"
            f"Reason: {result.get('block_reason')}\n"
            f"Raw votes: {result.get('votes')}\n"
            f"PromptGuard: {result.get('pg')}\n"
            f"Shadow: {result.get('shadow')}\n"
            f"Bars: {data['bars']}\n"
            f"EMA200: {data['ema200_state']}\n"
            f"Volume: {data['volume_state']}\n"
            "NOT an approved signal. No entry/stop/target."
        )

    action = result["winner"]

    emoji = (
        "🟢" if action == "BUY"
        else "🔴" if action == "SELL"
        else "⏸️"
    )

    if data["ema200_state"] == "unavailable":
        ema200_text = "N/A"

    elif data["ema200_state"] == "provisional":
        ema200_text = (
            f"{data['ema200']:.5g} "
            "(provisional; fewer than 400 bars)"
        )

    else:
        ema200_text = f"{data['ema200']:.5g}"

    volume_text = (
        "N/A - unavailable"
        if data["volume_state"] == "missing"
        else f"{data['vol_ratio']:.2f} ({data['volume_state']})"
    )

    lines = [
        f"{emoji} FATHER V15.2.1 | {sector_name}",
        f"Symbol: {ticker} | Daily",
        (
            "Sectors: "
            + ", ".join(TICKER_TO_SECTORS.get(ticker, []))
        ),
        f"Price: {data['price']:.5g}",
        f"RSI: {data['rsi']:.1f}",
        (
            f"EMA 20/50/200: {data['ema20']:.5g} / "
            f"{data['ema50']:.5g} / {ema200_text}"
        ),
        (
            f"MACD: {data['macd']:.5g} | "
            f"Signal: {data['macd_signal']:.5g} | "
            f"Hist: {data['macd_hist']:.5g}"
        ),
        f"ATR: {data['atr']:.5g}",
        f"Volume ratio: {volume_text}",
        f"Votes: {result['votes']}",
        f"Decision: {action}",
        f"PromptGuard: {result['pg']}",
        f"Shadow: {result['shadow']}",
    ]

    if action in ("BUY", "SELL") and result["stop"] is not None:
        distance = abs(result["entry"] - result["stop"])
        percentage = distance / result["entry"] * 100
        atr_multiple = distance / data["atr"]

        warning = ""

        if atr_multiple > 3 or percentage > 8:
            warning = (
                " WIDE STOP: substantial potential loss. "
                "Review position size."
            )

        lines.extend([
            f"Reference entry: {result['entry']:.5g}",
            (
                f"Reference stop: {result['stop']:.5g} "
                f"({percentage:.2f}% away, "
                f"{atr_multiple:.1f}x ATR).{warning}"
            ),
            f"Reference target: {result['target']:.5g}",
        ])

    lines.extend([
        "",
        "RESEARCH ONLY.",
        "Yahoo prices are NOT Exness bid/ask execution prices.",
        "Spreads, specifications and execution may differ.",
        "Confirm the live chart and manage trades manually.",
        "No order was placed. Manual-only build.",
    ])

    return "\n".join(lines)


# ============================================================
# TELEGRAM UTILITIES
# ============================================================

async def reply_with_retry(message, text, reply_markup=None):
    while True:
        try:
            await message.reply_text(
                text,
                reply_markup=reply_markup,
            )
            return

        except RetryAfter as exc:
            retry_seconds = exc.retry_after

            if hasattr(retry_seconds, "total_seconds"):
                retry_seconds = retry_seconds.total_seconds()

            await asyncio.sleep(float(retry_seconds) + 1)


async def safe_reply(message, text, reply_markup=None):
    if not text:
        return

    chunks = []
    current = ""

    for line in text.split("\n"):

        while len(line) > MAX_TELEGRAM_LENGTH:
            if current:
                chunks.append(current)
                current = ""

            chunks.append(line[:MAX_TELEGRAM_LENGTH])
            line = line[MAX_TELEGRAM_LENGTH:]

        if not line:
            continue

        candidate = (
            current + "\n" + line
            if current
            else line
        )

        if len(candidate) > MAX_TELEGRAM_LENGTH:
            chunks.append(current)
            current = line
        else:
            current = candidate

    if current:
        chunks.append(current)

    for index, chunk in enumerate(chunks):
        markup = (
            reply_markup
            if index == len(chunks) - 1
            else None
        )

        await reply_with_retry(
            message,
            chunk,
            markup,
        )


def sector_keyboard():
    rows = []
    row = []

    for key, sector in SECTORS.items():
        row.append(
            InlineKeyboardButton(
                sector["name"],
                callback_data=f"sector_{key}",
            )
        )

        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            "📊 SCAN ALL 13",
            callback_data="sector_all",
        )
    ])

    return InlineKeyboardMarkup(rows)


# ============================================================
# SCANNING
# ============================================================

async def scan_sector(message, key):
    sector = SECTORS[key]

    await safe_reply(
        message,
        f"FATHER scanning {sector['name']}...",
    )

    for index, ticker in enumerate(sector["tickers"]):
        result = await asyncio.to_thread(
            analyze_ticker,
            ticker,
        )

        keyboard = (
            sector_keyboard()
            if index == len(sector["tickers"]) - 1
            else None
        )

        await safe_reply(
            message,
            format_one(result, sector["name"]),
            keyboard,
        )


async def scan_all(message):
    await safe_reply(
        message,
        "FATHER scanning all 13 sectors. Fetching unique tickers...",
    )

    results = {}

    for ticker in TICKER_TO_SECTORS:
        results[ticker] = await asyncio.to_thread(
            analyze_ticker,
            ticker,
        )

    for sector in SECTORS.values():
        for ticker in sector["tickers"]:
            await safe_reply(
                message,
                format_one(results[ticker], sector["name"]),
            )

    await safe_reply(
        message,
        "Scan finished.",
        sector_keyboard(),
    )


# ============================================================
# COMMANDS
# ============================================================

async def start_cmd(update, context):
    ensure_user(update.effective_user.id)

    text = (
        "👑 FATHER V15.2.1 - MANUAL EDITION\n\n"
        "FATHER analyses. YOU place trades.\n\n"
        "/help - commands\n"
        "/scan - choose a sector\n"
        "/scan all - scan all sectors\n"
        "/all - scan all sectors\n"
        "/auto on|off - Hunter research alerts\n"
        "/myalerts - alert preference\n"
        "/alertstats - Hunter delivery statistics\n"
        "/benchmark - sample research scan\n"
        "/logtrade SYMBOL BUY|SELL QTY PRICE\n"
        "/history - view manual trade log\n"
        "/history clear - request deletion confirmation\n"
        "/history clear confirm - confirm deletion\n"
        "/setlimit AMOUNT - reference limit\n"
        "/setlot SIZE - reference lot\n"
        "/pnl - manual record count\n"
        "/autotrade - disabled\n"
        "/mt5info - MT5 status\n\n"
        "Hunter watches BTC-USD and TLT ONLY.\n"
        "Shadow requires at least two matching votes.\n"
        "HOLD is a real third vote.\n\n"
        "SQLite only. Render Free storage is temporary.\n"
        "No MT5, Exness login or automated orders."
    )

    await safe_reply(
        update.message,
        text,
        sector_keyboard(),
    )


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

    await safe_reply(
        update.message,
        "Choose a research sector:",
        sector_keyboard(),
    )


async def all_cmd(update, context):
    await scan_all(update.message)


async def callback_handler(update, context):
    query = update.callback_query
    await query.answer()

    data = query.data or ""

    if data == "sector_all":
        await query.edit_message_text(
            "FATHER scanning all sectors..."
        )
        await scan_all(query.message)
        return

    if data.startswith("sector_"):
        key = data[len("sector_"):]

        if key in SECTORS:
            await scan_sector(query.message, key)


async def auto_cmd(update, context):
    uid = update.effective_user.id

    if not context.args or context.args[0].lower() not in ("on", "off"):
        current = get_user(uid)["alert_enabled"]

        await safe_reply(
            update.message,
            (
                f"Your research alerts are "
                f"{'ON' if current else 'OFF'}.\n"
                "Use /auto on or /auto off.\n"
                "Hunter watches BTC-USD and TLT only."
            ),
        )
        return

    enabled = int(context.args[0].lower() == "on")

    update_user(uid, "alert_enabled", enabled)

    await safe_reply(
        update.message,
        f"Research alerts {'ON' if enabled else 'OFF'}.",
    )


async def myalerts_cmd(update, context):
    enabled = get_user(
        update.effective_user.id
    )["alert_enabled"]

    await safe_reply(
        update.message,
        f"Your research alerts: {'ON' if enabled else 'OFF'}.\n"
        "Hunter watches BTC-USD and TLT only.",
    )


async def alertstats_cmd(update, context):
    total, delivered, failed, generated, buys, sells = (
        get_alert_stats(update.effective_user.id)
    )

    await safe_reply(
        update.message,
        (
            "📊 HUNTER ALERT STATISTICS\n"
            f"Total records: {total}\n"
            f"Delivered: {delivered}\n"
            f"Failed: {failed}\n"
            f"Generated/pending: {generated}\n"
            f"Delivered BUY: {buys}\n"
            f"Delivered SELL: {sells}\n"
            "Scope: Hunter alerts only, not manual /scan."
        ),
    )


async def benchmark_cmd(update, context):
    output = ["FATHER RESEARCH CHECK"]

    for ticker in ("TLT", "BTC-USD", "SPY", "GC=F"):
        result = await asyncio.to_thread(
            analyze_ticker,
            ticker,
        )

        output.append(
            f"{ticker}: {result['winner']} | "
            f"Votes: {result['votes']} | "
            f"Shadow: {result['shadow']}"
        )

    output.append(
        "\nNot a historical accuracy benchmark or profitability claim."
    )

    await safe_reply(
        update.message,
        "\n".join(output),
    )


async def logtrade_cmd(update, context):
    try:
        if len(context.args) != 4:
            raise ValueError()

        symbol = context.args[0].upper()
        side = context.args[1].upper()

        quantity = validate_number(context.args[2])
        price = validate_number(context.args[3])

        add_trade(
            update.effective_user.id,
            symbol,
            side,
            quantity,
            price,
        )

        await safe_reply(
            update.message,
            f"Manual trade recorded: {side} {quantity:g} "
            f"{symbol} @ {price:g}",
        )

    except Exception:
        await safe_reply(
            update.message,
            "Usage: /logtrade EURUSD BUY 0.01 1.0850\n"
            "Quantity and price must be finite and positive.",
        )


async def history_cmd(update, context):
    uid = update.effective_user.id

    if context.args and context.args[0].lower() == "clear":

        if (
            len(context.args) < 2
            or context.args[1].lower() != "confirm"
        ):
            await safe_reply(
                update.message,
                "WARNING: This deletes YOUR trade history, "
                "alert statistics and Hunter delivery history.\n"
                "To confirm, send:\n"
                "/history clear confirm",
            )
            return

        clear_user_history(uid)

        await safe_reply(
            update.message,
            "Your history has been cleared. "
            "Other users' records are unaffected.",
        )
        return

    rows = get_trades(uid)

    if not rows:
        await safe_reply(
            update.message,
            "No manual trades logged.",
        )
        return

    lines = ["YOUR MANUAL TRADE LOG"]

    for row in rows:
        lines.append(
            f"#{row['id']} {row['side']} "
            f"{row['quantity']:g} {row['symbol']} "
            f"@ {row['entry_price']:g} "
            f"| {row['logged_at'][:16]}"
        )

    await safe_reply(
        update.message,
        "\n".join(lines),
    )


async def setlimit_cmd(update, context):
    try:
        value = validate_number(context.args[0])

        if value <= 0:
            raise ValueError()

        update_user(
            update.effective_user.id,
            "risk_limit",
            value,
        )

        await safe_reply(
            update.message,
            f"Reference limit set to {value:g}. "
            "This does not enforce broker risk.",
        )

    except Exception:
        await safe_reply(
            update.message,
            "Usage: /setlimit 500 - finite positive number required.",
        )


async def setlot_cmd(update, context):
    try:
        value = validate_number(context.args[0])

        if value <= 0:
            raise ValueError()

        update_user(
            update.effective_user.id,
            "lot_size",
            value,
        )

        await safe_reply(
            update.message,
            f"Reference lot set to {value:g}. "
            "Confirm broker minimum and volume step.",
        )

    except Exception:
        await safe_reply(
            update.message,
            "Usage: /setlot 0.01 - finite positive number required.",
        )


async def pnl_cmd(update, context):
    rows = get_trades(
        update.effective_user.id,
        10000,
    )

    settings = get_user(update.effective_user.id)

    await safe_reply(
        update.message,
        (
            f"Manual records: {len(rows)}\n"
            f"Reference limit: {settings['risk_limit']:g}\n"
            f"Lot: {settings['lot_size']:g}\n"
            "Broker P&L unavailable. No MT5 connection."
        ),
    )


async def autotrade_cmd(update, context):
    await safe_reply(
        update.message,
        "AUTOMATED TRADING DISABLED.\n"
        "No MT5, no Exness connection and no orders.",
    )


async def mt5info_cmd(update, context):
    await safe_reply(
        update.message,
        "MT5 status: NOT CONNECTED.\n"
        "No MT5 login and no order execution.\n"
        "Never send passwords through Telegram.",
    )


async def handle_text(update, context):
    message_text = (
        update.message.text or ""
    ).lower().strip()

    for key, sector in SECTORS.items():
        if (
            key in message_text
            or sector["name"].lower() in message_text
        ):
            await scan_sector(update.message, key)
            return

    await safe_reply(
        update.message,
        "Send /scan and choose a sector, "
        "or type gold or crypto.",
    )


# ============================================================
# HUNTER LOOP
# ============================================================

async def hunter_loop(app):
    logger.info("Hunter loop started.")

    while True:
        try:
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

            with db() as conn:
                opted_in = [
                    row["uid"]
                    for row in conn.execute(
                        """
                        SELECT uid FROM users
                        WHERE alert_enabled=1
                        """
                    ).fetchall()
                ]

            if not opted_in:
                continue

            # Exactly two Hunter instruments.
            for ticker in ("BTC-USD", "TLT"):

                result = await asyncio.to_thread(
                    analyze_ticker,
                    ticker,
                )

                if (
                    not result
                    or result.get("blocked")
                    or result["winner"] not in ("BUY", "SELL")
                ):
                    continue

                direction = result["winner"]
                price = result["d"]["price"]

                sector_name = (
                    "Crypto"
                    if ticker == "BTC-USD"
                    else "Bonds & Treasury"
                )

                message = format_one(
                    result,
                    sector_name,
                )

                # Separate claim and delivery for every user.
                for uid in opted_in:

                    alert_id = claim_hunter_delivery(
                        uid,
                        ticker,
                        direction,
                        price,
                    )

                    if alert_id is None:
                        continue

                    try:
                        await app.bot.send_message(
                            chat_id=uid,
                            text=message[:MAX_TELEGRAM_LENGTH],
                        )

                        finish_hunter_delivery(
                            uid,
                            ticker,
                            direction,
                            alert_id,
                            True,
                        )

                    except Exception:
                        logger.exception(
                            "Hunter delivery failed for user %s",
                            uid,
                        )

                        try:
                            finish_hunter_delivery(
                                uid,
                                ticker,
                                direction,
                                alert_id,
                                False,
                            )
                        except Exception:
                            logger.exception(
                                "Could not record failed delivery."
                            )

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
    print(
        "=== FATHER V15.2.1 MANUAL EDITION STARTING ===",
        flush=True,
    )

    print(
        "SQLite ONLY - temporary on Render Free.",
        flush=True,
    )

    init_db()

    threading.Thread(
        target=run_flask,
        daemon=True,
    ).start()

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("all", all_cmd))

    app.add_handler(CommandHandler("auto", auto_cmd))
    app.add_handler(CommandHandler("myalerts", myalerts_cmd))
    app.add_handler(CommandHandler("alertstats", alertstats_cmd))

    app.add_handler(CommandHandler("benchmark", benchmark_cmd))
    app.add_handler(CommandHandler("logtrade", logtrade_cmd))
    app.add_handler(CommandHandler("history", history_cmd))

    app.add_handler(CommandHandler("setlimit", setlimit_cmd))
    app.add_handler(CommandHandler("setlot", setlot_cmd))
    app.add_handler(CommandHandler("pnl", pnl_cmd))

    app.add_handler(CommandHandler("autotrade", autotrade_cmd))
    app.add_handler(CommandHandler("mt5info", mt5info_cmd))

    app.add_handler(CallbackQueryHandler(callback_handler))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    logger.info("FATHER V15.2.1 ready.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
