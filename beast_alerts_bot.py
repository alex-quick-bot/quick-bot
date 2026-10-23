import os
from flask import Flask
import threading, yfinance as yf, asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
app = Flask(__name__)
@app.route('/')
def home(): return "BEAST V9.7 PRIVATE - REAL WILDER RSI+EMA LIVE - FIXED 🔥"
def run_flask(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

user_symbols = {}
auto_users = set()
last_state = {}

# === ALERT SPAM GUARD ===
# Prevents a whipsawing ticker (bouncing across RSI/EMA thresholds overnight)
# from flooding you with contradictory buy/sell pings while you're asleep.
import time
ALERT_COOLDOWN_SECONDS = 15 * 60   # min gap between alerts for the same symbol
MAX_ALERTS_PER_SYMBOL_PER_DAY = 4  # hard cap, resets every 24h per symbol
alert_tracker = {}  # symbol -> {"day": "YYYY-MM-DD", "count": int, "last_ts": float}

def _alert_allowed(symbol):
    from datetime import date
    today = date.today().isoformat()
    rec = alert_tracker.get(symbol)
    now = time.time()
    if rec is None or rec["day"] != today:
        alert_tracker[symbol] = {"day": today, "count": 0, "last_ts": 0.0}
        rec = alert_tracker[symbol]
    if rec["count"] >= MAX_ALERTS_PER_SYMBOL_PER_DAY:
        return False
    if now - rec["last_ts"] < ALERT_COOLDOWN_SECONDS:
        return False
    return True

def _alert_record(symbol):
    rec = alert_tracker[symbol]
    rec["count"] += 1
    rec["last_ts"] = time.time()

# === DAILY LOSS LIMIT ===
# This is a real money/risk guard, separate from the alert-spam guard above.
# It tracks P&L you report yourself (the bot has no broker connection, so it
# can't see your actual fills) and pauses /auto for the rest of the day once
# you hit your limit.
from datetime import date as _date

DEFAULT_DAILY_LOSS_LIMIT = 50.0  # USD; override per-user with /setlimit
user_loss_limit = {}   # uid -> float limit (positive number, e.g. 50 means stop at -$50)
user_pnl = {}          # uid -> {"day": "YYYY-MM-DD", "pnl": float, "trades": [str,...]}
loss_limit_hit_today = {}  # uid -> "YYYY-MM-DD" the limit was hit on

def _today():
    return _date.today().isoformat()

def _get_pnl_record(uid):
    rec = user_pnl.get(uid)
    today = _today()
    if rec is None or rec["day"] != today:
        rec = {"day": today, "pnl": 0.0, "trades": []}
        user_pnl[uid] = rec
    return rec

def _get_limit(uid):
    return user_loss_limit.get(uid, DEFAULT_DAILY_LOSS_LIMIT)

def _loss_limit_active(uid):
    return loss_limit_hit_today.get(uid) == _today()

def fix_symbol(s):
    s=s.upper().strip()
    M={
        "NFT":"MANA-USD","NFTS":"MANA-USD","MANA":"MANA-USD","APE":"APE-USD","SAND":"SAND-USD",
        "TREASURY":"TLT","GOVT":"TLT","BONDS":"TLT","BOND":"TLT","TLT":"TLT",
        "AGRI":"DBA","AGRICULTURE":"DBA","DBA":"DBA",
        "REALESTATE":"VNQ","REIT":"VNQ","VNQ":"VNQ",
        "SPX":"SPY","WALLSTREET":"SPY","SPY":"SPY",
        "AUTO":"CARZ","CARZ":"CARZ","FORD":"F","GM":"GM","RIVN":"RIVN",
        "ICT":"XLK","XLK":"XLK","COFFEE":"JO","JO":"JO",
        "OILGAS":"XLE","XLE":"XLE","OIL":"CL=F","GOLD":"GLD","GLD":"GLD",
        "CRYPTO":"BTC-USD","BTC":"BTC-USD","ETH":"ETH-USD","SOL":"SOL-USD",
        "AI":"BOTZ","BOTZ":"BOTZ","TESLA":"TSLA","TSLA":"TSLA",
    }
    return M.get(s,s)

SECTOR_COMPANIES = {
    "NFT": "MANA, APE Bored Ape -> BUY MANA on Binance",
    "TREASURY": "TLT 20Y Bond -> Buy TLT (Yield ^TNX)",
    "BONDS": "TLT 20Y Bond -> Buy TLT",
    "AGRI": "DBA Corn Wheat Soy -> Buy DBA",
    "REALESTATE": "VNQ PLD AMT -> Buy VNQ",
    "SPX": "SPY Wall St AAPL MSFT NVDA -> Buy SPY",
    "AUTO": "CARZ TSLA Ford GM -> BUY TSLA",
    "ICT": "XLK AAPL MSFT NVDA -> BUY AAPL/NVDA",
    "COFFEE": "JO Coffee -> Buy JO",
    "OILGAS": "XLE XOM Chevron -> BUY XOM",
    "GOLD": "GLD Gold -> Buy GLD",
    "CRYPTO": "BTC ETH SOL -> Buy BTC",
    "AI": "BOTZ NVDA META PLTR -> BUY NVDA",
    "TESLA": "TSLA alone -> Buy TSLA",
}

# === REAL WILDER RSI + EMA ===
def calc_rsi_wilder(close, period=14):
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    last_gain = avg_gain.iloc[-1]
    last_loss = avg_loss.iloc[-1]
    # FIX: flat price window (gain==loss==0) is RSI-undefined, don't fake 50
    if last_gain == 0 and last_loss == 0:
        return None
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return float(100 - (100 / (1 + rs)))

def calc_ema(close, span):
    if len(close) < span:
        return None
    return float(close.ewm(span=span, adjust=False).mean().iloc[-1])

def get_data(symbol):
    """Blocking network call - always run this via asyncio.to_thread from async code."""
    try:
        ysym=fix_symbol(symbol)
        hist=None
        for p,i in [("1d","1m"),("5d","5m"),("5d","15m"),("5d","1h"),("6mo","1d")]:
            try:
                h=yf.Ticker(ysym).history(period=p, interval=i)
                if not h.empty and len(h)>=30:
                    hist=h
                    break
            except: continue
        if hist is None or hist.empty or len(hist)<30: return None
        c=hist['Close']
        curr=float(c.iloc[-1])
        ema12=calc_ema(c,12)
        ema26=calc_ema(c,26)
        sma20=float(c.rolling(20).mean().iloc[-1])
        sma50=float(c.rolling(50).mean().iloc[-1])
        rsi_now=calc_rsi_wilder(c,14)
        if rsi_now is None or ema12 is None or ema26 is None: return None
        high20=float(hist['High'].rolling(20).max().iloc[-1])
        low20=float(hist['Low'].rolling(20).min().iloc[-1])
        # Renamed from "confidence" internally too - this is a rule-based tier, not a
        # backtested win-rate. See TIER label in make_text().
        tier=90 if rsi_now<30 else 80 if rsi_now<38 else 85 if rsi_now>75 else 75 if rsi_now>68 else 65 if ema12>ema26 else 55
        return {"curr":curr,"rsi":rsi_now,"ema12":ema12,"ema26":ema26,"sma20":sma20,"sma50":sma50,"high20":high20,"low20":low20,"tier":tier,"ysym":ysym}
    except Exception as e:
        print(f"get_data {symbol} error {e}")
        return None

def _safe_stop(curr, raw_stop, is_buy):
    """FIX: guarantee stop is on the correct side of entry.
    Buy -> stop must be below curr. Sell -> stop must be above curr.
    If the raw calc lands on the wrong side (can happen when sma20/high20/low20
    sit close to curr), clamp to a small fixed % instead of shipping a broken stop."""
    if is_buy:
        if raw_stop >= curr:
            return curr * 0.98  # fallback: 2% below entry
        return raw_stop
    else:
        if raw_stop <= curr:
            return curr * 1.02  # fallback: 2% above entry
        return raw_stop

def make_text(orig,d):
    curr=d["curr"]; rsi=d["rsi"]; ema12=d["ema12"]; ema26=d["ema26"]; sma20=d["sma20"]; sma50=d["sma50"]; high20=d["high20"]; low20=d["low20"]; tier=d["tier"]
    if rsi<30:
        action="💎💎 STRONG BUY"; reason="Oversold Discount! REAL Wilder RSI"
        tgt=curr*1.08; stp=_safe_stop(curr, low20*0.98, True); prof="+8%"
    elif rsi<38 and ema12>ema26:
        action="💎 BUY"; reason="Oversold + Bullish UP! EMA12>EMA26"
        tgt=high20; stp=_safe_stop(curr, sma20*0.97, True); prof="+5-7%"
    elif rsi<38:
        action="💎 BUY"; reason="Cheap entry REAL RSI"
        tgt=sma20; stp=_safe_stop(curr, low20*0.98, True); prof="+3-5%"
    elif rsi>75:
        action="⚠️⚠️ STRONG SELL"; reason="Overbought Take profit REAL RSI"
        tgt=sma20; stp=_safe_stop(curr, high20*1.02, False); prof="SELL"
    elif rsi>68:
        action="⚠️ SELL"; reason="Expensive"
        tgt=sma20; stp=_safe_stop(curr, high20*1.02, False); prof="Sell"
    elif ema12>ema26 and curr>sma20:
        action="🚀 BUY"; reason="Uptrend! EMA Golden"
        tgt=high20; stp=_safe_stop(curr, sma20*0.98, True); prof="+4-6%"
    else:
        action="➡️ HOLD"; reason=f"Normal RSI {rsi:.1f} Wilder's"
        tgt=high20; stp=low20; prof="Hold"
    comp=SECTOR_COMPANIES.get(orig, orig)
    return (f"{action} {orig} TIER {tier}% (rule-based, not backtested)\n"
            f"💰 ${curr:.2f} RSI {rsi:.1f} (Wilder) EMA {ema12:.1f}/{ema26:.1f} SMA {sma20:.1f}/{sma50:.1f}\n"
            f"💡 {reason}\n"
            f"🎯 ENTRY ${curr:.2f} STOP ${stp:.2f} TARGET ${tgt:.2f} {prof}\n"
            f"📊 Sup ${low20:.2f} Res ${high20:.2f}\n"
            f"🏢 {comp}\n---")

def check_alert(orig,d):
    rsi=d["rsi"]; ema12=d["ema12"]; ema26=d["ema26"]; curr=d["curr"]; tier=d["tier"]
    key=orig; prev=last_state.get(key,{})
    alert=None
    if rsi < 32 and prev.get("rsi",50) >= 32:
        alert=f"💎💎 BUY ALERT {orig} ${curr:.2f} RSI {rsi:.1f} TIER {tier}% Wilder\n{SECTOR_COMPANIES.get(orig,'')}\nTARGET +8% STOP -3%"
    elif rsi > 72 and prev.get("rsi",50) <= 72:
        alert=f"⚠️ SELL ALERT {orig} ${curr:.2f} RSI {rsi:.1f} Wilder Take profit!"
    elif ema12 > ema26 and prev.get("ema12",0) <= prev.get("ema26",0):
        alert=f"🚀 GOLDEN CROSS {orig} ${curr:.2f} TIER {tier}% EMA12>{ema26:.1f}\n{SECTOR_COMPANIES.get(orig,'')}"
    last_state[key]={"rsi":rsi,"ema12":ema12,"ema26":ema26}
    return alert

async def scan_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    syms=user_symbols.get(update.effective_user.id,["NFT","TREASURY","BONDS","AGRI","REALESTATE","SPX","AUTO","ICT","COFFEE","OILGAS","GOLD","CRYPTO","AI","TESLA"])
    await update.message.reply_text(f"🔥 V9.7 PRIVATE REAL Wilder RSI+EMA - Scanning {len(syms)} sectors...")
    batch=""
    for i,sym in enumerate(syms,1):
        # FIX: run blocking yfinance call off the event loop so the bot stays responsive
        d=await asyncio.to_thread(get_data, sym)
        if not d: batch+=f"❌ {sym} No data\n---\n"
        else: batch+=make_text(sym.upper(), d)+"\n"
        if i%3==0:
            await update.message.reply_text(batch)
            batch=""; await asyncio.sleep(1)
    if batch: await update.message.reply_text(batch)
    await update.message.reply_text(f"✅ DONE {len(syms)} REAL RSI sectors! /auto on to hunt while you sleep!")

async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_symbols[update.effective_user.id]=["NFT","TREASURY","BONDS","AGRI","REALESTATE","SPX","AUTO","ICT","COFFEE","OILGAS","GOLD","CRYPTO","AI","TESLA"]
    await update.message.reply_text("🌍 ALL 14 LOCKED REAL RSI! /scan to see + /auto on for sleep alerts!")

async def myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use /all or /myalerts ICT AUTO GOLD...")
        return
    user_symbols[update.effective_user.id]=[s.upper() for s in context.args[:14]]
    await update.message.reply_text(f"✅ Locked: {', '.join(user_symbols[update.effective_user.id])} REAL RSI")

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args or context.args[0].lower()=="on":
        if _loss_limit_active(uid):
            lim = _get_limit(uid)
            pnl = _get_pnl_record(uid)["pnl"]
            await update.message.reply_text(
                f"🛑 Daily loss limit (-${lim:.2f}) already hit today (P&L ${pnl:+.2f}).\n"
                f"Auto-alerts stay paused until tomorrow. Use /pnl to review, or /setlimit to adjust your limit for next time."
            )
            return
        auto_users.add(uid)
        await update.message.reply_text("✅ Auto ON! I will alert you while you sleep with REAL Wilder RSI+EMA!")
    else:
        auto_users.discard(uid)
        await update.message.reply_text("❌ Auto OFF")

async def setlimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text(f"Current daily loss limit: ${_get_limit(uid):.2f}\nUse /setlimit 75 to change it.")
        return
    try:
        val = abs(float(context.args[0]))
    except ValueError:
        await update.message.reply_text("Use a number, e.g. /setlimit 75")
        return
    user_loss_limit[uid] = val
    await update.message.reply_text(f"✅ Daily loss limit set to ${val:.2f}. Once today's P&L hits -${val:.2f}, /auto pauses for the rest of the day.")

async def logtrade_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if len(context.args) < 2:
        await update.message.reply_text("Use /logtrade SYMBOL AMOUNT, e.g. /logtrade BTC -25.50 or /logtrade GOLD +12")
        return
    symbol = context.args[0].upper()
    try:
        amount = float(context.args[1].replace("+",""))
    except ValueError:
        await update.message.reply_text("Amount must be a number, e.g. -25.50 or +12")
        return

    rec = _get_pnl_record(uid)
    rec["pnl"] += amount
    rec["trades"].append(f"{symbol} {amount:+.2f}")

    limit = _get_limit(uid)
    msg = f"📝 Logged {symbol} {amount:+.2f}. Today's P&L: ${rec['pnl']:+.2f} (limit -${limit:.2f})"

    if rec["pnl"] <= -limit and not _loss_limit_active(uid):
        loss_limit_hit_today[uid] = _today()
        was_auto = uid in auto_users
        auto_users.discard(uid)
        msg += f"\n\n🛑 DAILY LOSS LIMIT HIT. Auto-alerts {'turned OFF' if was_auto else 'kept OFF'} for the rest of today. Step away, reset tomorrow."

    await update.message.reply_text(msg)

async def pnl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rec = _get_pnl_record(uid)
    limit = _get_limit(uid)
    status = "🛑 LIMIT HIT - auto paused" if _loss_limit_active(uid) else "✅ under limit"
    trades_txt = "\n".join(rec["trades"]) if rec["trades"] else "(none logged today)"
    await update.message.reply_text(
        f"📊 Today's P&L: ${rec['pnl']:+.2f} / limit -${limit:.2f} [{status}]\n\nTrades:\n{trades_txt}"
    )

async def alertstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not alert_tracker:
        await update.message.reply_text("No alerts fired yet today.")
        return
    lines = [f"{sym}: {rec['count']}/{MAX_ALERTS_PER_SYMBOL_PER_DAY} today" for sym, rec in alert_tracker.items()]
    await update.message.reply_text("📊 Alert usage today:\n" + "\n".join(lines))

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 BEAST V9.7 PRIVATE FINAL - REAL Wilder RSI+EMA!\n"
        "/all -> /scan -> /auto on\n"
        "/logtrade SYMBOL +/-AMOUNT to track P&L\n"
        "/setlimit AMOUNT to set your daily stop (default $50)\n"
        "/pnl to check today's status\n"
        "Scan shows REAL RSI + Sleep hunter works!"
    )

async def hunter_loop(app_bot):
    while True:
        try:
            # FIX: fetch each unique symbol once per cycle instead of once per user,
            # then fan the alert out to every subscribed user. Cuts yfinance calls
            # dramatically and avoids rate-limit/ban risk as user count grows.
            symbol_to_users = {}
            for uid in list(auto_users):
                if _loss_limit_active(uid):
                    # safety net: drop anyone whose loss limit got hit (e.g. via
                    # /logtrade) since they last entered this loop
                    auto_users.discard(uid)
                    continue
                syms=user_symbols.get(uid,["NFT","TREASURY","BONDS","AGRI","REALESTATE","SPX","AUTO","ICT","COFFEE","OILGAS","GOLD","CRYPTO","AI","TESLA"])
                for sym in syms:
                    symbol_to_users.setdefault(sym, []).append(uid)

            for sym, uids in symbol_to_users.items():
                # FIX: off the event loop, same as scan_now
                d = await asyncio.to_thread(get_data, sym)
                if not d:
                    continue
                a = check_alert(sym.upper(), d)
                if a:
                    if not _alert_allowed(sym.upper()):
                        # swallowed: still updates last_state via check_alert above,
                        # so we don't re-fire the instant the cooldown expires
                        continue
                    _alert_record(sym.upper())
                    for uid in uids:
                        try:
                            await app_bot.bot.send_message(chat_id=uid, text=a)
                        except Exception as send_err:
                            print(f"send_message {uid} error {send_err}")
                await asyncio.sleep(1)
            await asyncio.sleep(60)
        except Exception as e:
            print(f"Hunter error {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot_app=ApplicationBuilder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start_cmd))
    bot_app.add_handler(CommandHandler("myalerts", myalerts))
    bot_app.add_handler(CommandHandler("scan", scan_now))
    bot_app.add_handler(CommandHandler("auto", auto_cmd))
    bot_app.add_handler(CommandHandler("all", all_cmd))
    bot_app.add_handler(CommandHandler("history", all_cmd))
    bot_app.add_handler(CommandHandler("alertstats", alertstats_cmd))
    bot_app.add_handler(CommandHandler("logtrade", logtrade_cmd))
    bot_app.add_handler(CommandHandler("setlimit", setlimit_cmd))
    bot_app.add_handler(CommandHandler("pnl", pnl_cmd))
    async def on_start(a): asyncio.create_task(hunter_loop(a))
    bot_app.post_init=on_start
    bot_app.run_polling()
