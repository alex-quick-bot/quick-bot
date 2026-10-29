import os
from flask import Flask
import threading, yfinance as yf, asyncio, time
from datetime import date as _date
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8665686560"))

app = Flask(__name__)
@app.route('/')
def home(): return "BEAST FATHER V12 PRIVATE - LIVE MTF + FATHER SIGNALS 🔥"
def run_flask(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

user_symbols = {}
auto_users = set()
last_state = {}

# === YOUR GUARDS — KEPT 100% ===
ALERT_COOLDOWN_SECONDS = 15 * 60
MAX_ALERTS_PER_SYMBOL_PER_DAY = 4
alert_tracker = {}

def _alert_allowed(symbol):
    from datetime import date
    today = date.today().isoformat()
    rec = alert_tracker.get(symbol)
    now = time.time()
    if rec is None or rec["day"] != today:
        alert_tracker[symbol] = {"day": today, "count": 0, "last_ts": 0.0}
        rec = alert_tracker[symbol]
    if rec["count"] >= MAX_ALERTS_PER_SYMBOL_PER_DAY: return False
    if now - rec["last_ts"] < ALERT_COOLDOWN_SECONDS: return False
    return True

def _alert_record(symbol):
    rec = alert_tracker[symbol]
    rec["count"] += 1
    rec["last_ts"] = time.time()

DEFAULT_DAILY_LOSS_LIMIT = 50.0
user_loss_limit = {}
user_pnl = {}
loss_limit_hit_today = {}

def _today(): return _date.today().isoformat()
def _get_pnl_record(uid):
    rec = user_pnl.get(uid)
    today = _today()
    if rec is None or rec["day"] != today:
        rec = {"day": today, "pnl": 0.0, "trades": []}
        user_pnl[uid] = rec
    return rec
def _get_limit(uid): return user_loss_limit.get(uid, DEFAULT_DAILY_LOSS_LIMIT)
def _loss_limit_active(uid): return loss_limit_hit_today.get(uid) == _today()

def fix_symbol(s):
    s=s.upper().strip()
    M={"NFT":"MANA-USD","NFTS":"MANA-USD","MANA":"MANA-USD","APE":"APE-USD","SAND":"SAND-USD","TREASURY":"TLT","GOVT":"TLT","BONDS":"TLT","BOND":"TLT","TLT":"TLT","AGRI":"DBA","AGRICULTURE":"DBA","DBA":"DBA","REALESTATE":"VNQ","REIT":"VNQ","VNQ":"VNQ","SPX":"SPY","WALLSTREET":"SPY","SPY":"SPY","AUTO":"CARZ","CARZ":"CARZ","FORD":"F","GM":"GM","RIVN":"RIVN","ICT":"XLK","XLK":"XLK","COFFEE":"JO","JO":"JO","OILGAS":"XLE","XLE":"XLE","OIL":"CL=F","GOLD":"GLD","GLD":"GLD","CRYPTO":"BTC-USD","BTC":"BTC-USD","ETH":"ETH-USD","SOL":"SOL-USD","AI":"BOTZ","BOTZ":"BOTZ","TESLA":"TSLA","TSLA":"TSLA",}
    return M.get(s,s)

SECTOR_COMPANIES = {
    "NFT": "MANA, APE Bored Ape -> BUY MANA on Binance",
    "TREASURY": "TLT 20Y Bond -> Buy TLT (Yield ^TNX)",
    "BONDS": "TLT 20Y Bond -> Buy TLT", "AGRI": "DBA Corn Wheat Soy -> Buy DBA",
    "REALESTATE": "VNQ PLD AMT -> Buy VNQ", "SPX": "SPY Wall St AAPL MSFT NVDA -> Buy SPY",
    "AUTO": "CARZ TSLA Ford GM -> BUY TSLA", "ICT": "XLK AAPL MSFT NVDA -> BUY AAPL/NVDA",
    "COFFEE": "JO Coffee -> Buy JO", "OILGAS": "XLE XOM Chevron -> BUY XOM",
    "GOLD": "GLD Gold -> Buy GLD", "CRYPTO": "BTC ETH SOL -> Buy BTC",
    "AI": "BOTZ NVDA META PLTR -> BUY NVDA", "TESLA": "TSLA alone -> Buy TSLA",
}

# === FATHER ENGINE — UNBLOCKED LIVE ===
def get_yf_session():
    try:
        from curl_cffi import requests as crequests
        return crequests.Session(impersonate="chrome120")
    except:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0"})
        return s

def calc_rsi_wilder(close, period=14):
    if len(close) < period + 1: return None
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    last_gain = avg_gain.iloc[-1]; last_loss = avg_loss.iloc[-1]
    if last_gain == 0 and last_loss == 0: return None
    if last_loss == 0: return 100.0
    rs = last_gain / last_loss
    return float(100 - (100 / (1 + rs)))

def calc_ema(close, span):
    if len(close) < span: return None
    return float(close.ewm(span=span, adjust=False).mean().iloc[-1])

def get_data(symbol):
    try:
        ysym=fix_symbol(symbol)
        session = get_yf_session()
        hist=None
        # FATHER: Use only 1d intervals that work for ALL tickers on Render (not 1m)
        for p in ["1mo","3mo","6mo"]:
            try:
                h=yf.Ticker(ysym, session=session).history(period=p, interval="1d", auto_adjust=False)
                if not h.empty and len(h)>=30:
                    hist=h
                    break
            except: continue
        if hist is None or hist.empty or len(hist)<30: return None
        c=hist['Close']
        curr=float(c.iloc[-1])
        ema12=calc_ema(c,12); ema26=calc_ema(c,26)
        ema50=calc_ema(c,50); ema200=calc_ema(c,200)
        sma20=float(c.rolling(20).mean().iloc[-1])
        sma50=float(c.rolling(50).mean().iloc[-1])
        rsi_now=calc_rsi_wilder(c,14)
        if rsi_now is None or ema12 is None or ema26 is None: return None
        
        # FATHER EXTRAS
        high20=float(hist['High'].rolling(20).max().iloc[-1])
        low20=float(hist['Low'].rolling(20).min().iloc[-1])
        # MACD
        ema12_series=c.ewm(span=12).mean(); ema26_series=c.ewm(span=26).mean()
        macd=float((ema12_series-ema26_series).iloc[-1])
        # ATR for stop
        tr = (hist['High']-hist['Low']).combine((hist['High']-c.shift()).abs(), max).combine((hist['Low']-c.shift()).abs(), max)
        atr=float(tr.rolling(14).mean().iloc[-1])
        vol=float(hist['Volume'].iloc[-1]) if 'Volume' in hist else 0
        vol_avg=float(hist['Volume'].rolling(20).mean().iloc[-1]) if 'Volume' in hist else vol
        vol_mult=vol/vol_avg if vol_avg else 1
        
        tier=90 if rsi_now<30 else 80 if rsi_now<38 else 85 if rsi_now>75 else 75 if rsi_now>68 else 65 if ema12>ema26 else 55
        return {"curr":curr,"rsi":rsi_now,"ema12":ema12,"ema26":ema26,"ema50":ema50,"ema200":ema200,"sma20":sma20,"sma50":sma50,"high20":high20,"low20":low20,"tier":tier,"ysym":ysym,"macd":macd,"atr":atr,"vol":vol,"vol_mult":vol_mult}
    except Exception as e:
        print(f"get_data {symbol} error {e}")
        return None

def _safe_stop(curr, raw_stop, is_buy):
    if is_buy:
        if raw_stop >= curr: return curr * 0.98
        return raw_stop
    else:
        if raw_stop <= curr: return curr * 1.02
        return raw_stop

def make_text(orig,d):
    curr=d["curr"]; rsi=d["rsi"]; ema12=d["ema12"]; ema26=d["ema26"]; sma20=d["sma20"]; sma50=d["sma50"]; high20=d["high20"]; low20=d["low20"]; tier=d["tier"]
    macd=d.get("macd",0); atr=d.get("atr",0); vol=d.get("vol",0); vol_mult=d.get("vol_mult",1)
    ema50=d.get("ema50",sma50); ema200=d.get("ema200",sma50)
    
    if rsi<30:
        action="💎💎 FATHER STRONG BUY"; reason=f"Oversold + MACD {macd:.2f} Vol x{vol_mult:.1f}"
        tgt=curr+3*atr; stp=_safe_stop(curr, low20*0.98, True); prof="+8% (ATR)"
    elif rsi<38 and ema12>ema26:
        action="💎 FATHER BUY"; reason=f"Cheap + Bullish EMA12>EMA26 MACD {macd:.1f}"
        tgt=high20; stp=_safe_stop(curr, sma20*0.97, True); prof="+5-7%"
    elif rsi>75:
        action="⚠️⚠️ FATHER STRONG SELL"; reason=f"Overbought RSI {rsi:.1f} Vol x{vol_mult:.1f}"
        tgt=sma20; stp=_safe_stop(curr, high20*1.02, False); prof="SELL"
    elif ema12>ema26 and curr>sma20 and curr>ema50:
        action="🚀 FATHER BULLISH BREAKOUT"; reason=f"Above EMA20/50/200 Vol x{vol_mult:.1f}"
        tgt=curr+2*atr; stp=_safe_stop(curr, curr-2*atr, True); prof="+4-6%"
    else:
        action="➡️ FATHER HOLD"; reason=f"RSI {rsi:.1f} EMA {ema12:.0f}/{ema26:.0f}/{ema50:.0f}"
        tgt=high20; stp=low20; prof="Hold"
    
    comp=SECTOR_COMPANIES.get(orig, orig)
    return (f"👑 {action} {orig} TIER {tier}% | FATHER V12\n"
            f"💰 ${curr:.2f} RSI {rsi:.1f} EMA {ema12:.1f}/{ema26:.1f}/{ema50:.1f} | MACD {macd:.2f}\n"
            f"📊 Vol {vol:,.0f} x{vol_mult:.1f} ATR ${atr:.2f} | Range ${low20:.2f}-${high20:.2f}\n"
            f"💡 {reason}\n"
            f"🎯 ENTRY ${curr:.2f} STOP ${stp:.2f} TARGET ${tgt:.2f} {prof}\n"
            f"🏢 {comp}\n---")

def check_alert(orig,d):
    rsi=d["rsi"]; ema12=d["ema12"]; ema26=d["ema26"]; curr=d["curr"]; tier=d["tier"]
    key=orig; prev=last_state.get(key,{})
    alert=None
    if rsi < 32 and prev.get("rsi",50) >= 32:
        alert=f"💎💎 FATHER BUY ALERT {orig} ${curr:.2f} RSI {rsi:.1f} TIER {tier}% Wilder\n{SECTOR_COMPANIES.get(orig,'')} | Vol x{d.get('vol_mult',1):.1f}"
    elif rsi > 72 and prev.get("rsi",50) <= 72:
        alert=f"⚠️ FATHER SELL ALERT {orig} ${curr:.2f} RSI {rsi:.1f} Wilder Take profit! ATR ${d.get('atr',0):.2f}"
    elif ema12 > ema26 and prev.get("ema12",0) <= prev.get("ema26",0):
        alert=f"🚀 FATHER GOLDEN CROSS {orig} ${curr:.2f} TIER {tier}% EMA12>{ema26:.1f} MACD {d.get('macd',0):.2f}"
    last_state[key]={"rsi":rsi,"ema12":ema12,"ema26":ema26}
    return alert

async def scan_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    syms=user_symbols.get(update.effective_user.id,["NFT","TREASURY","BONDS","AGRI","REALESTATE","SPX","AUTO","ICT","COFFEE","OILGAS","GOLD","CRYPTO","AI","TESLA"])
    await update.message.reply_text(f"👑 FATHER V12 PRIVATE - Scanning {len(syms)} sectors LIVE MTF...")
    batch=""
    for i,sym in enumerate(syms,1):
        d=await asyncio.to_thread(get_data, sym)
        if not d: batch+=f"❌ {sym} No data\n---\n"
        else: batch+=make_text(sym.upper(), d)+"\n"
        if i%3==0:
            await update.message.reply_text(batch)
            batch=""; await asyncio.sleep(0.5)
    if batch: await update.message.reply_text(batch)
    await update.message.reply_text(f"✅ FATHER DONE {len(syms)} sectors! /auto on to hunt while you sleep!")

async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_symbols[update.effective_user.id]=["NFT","TREASURY","BONDS","AGRI","REALESTATE","SPX","AUTO","ICT","COFFEE","OILGAS","GOLD","CRYPTO","AI","TESLA"]
    await update.message.reply_text("🌍 ALL 14 LOCKED FATHER V12! /scan to see + /auto on for sleep alerts!")

async def myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use /all or /myalerts ICT AUTO GOLD...")
        return
    user_symbols[update.effective_user.id]=[s.upper() for s in context.args[:14]]
    await update.message.reply_text(f"✅ Locked: {', '.join(user_symbols[update.effective_user.id])} FATHER V12")

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args or context.args[0].lower()=="on":
        if _loss_limit_active(uid):
            lim = _get_limit(uid); pnl = _get_pnl_record(uid)["pnl"]
            await update.message.reply_text(f"🛑 Daily loss limit (-${lim:.2f}) already hit today (P&L ${pnl:+.2f}). Auto paused until tomorrow. /pnl /setlimit")
            return
        auto_users.add(uid)
        await update.message.reply_text("✅ FATHER Auto ON! 30s live hunter with Wilder RSI+MACD+ATR!")
    else:
        auto_users.discard(uid)
        await update.message.reply_text("❌ Auto OFF")

async def setlimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text(f"Current daily loss limit: ${_get_limit(uid):.2f}\nUse /setlimit 75 to change it.")
        return
    try: val = abs(float(context.args[0]))
    except: await update.message.reply_text("Use a number, e.g. /setlimit 75"); return
    user_loss_limit[uid] = val
    await update.message.reply_text(f"✅ Daily loss limit set to ${val:.2f}.")

async def logtrade_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if len(context.args) < 2:
        await update.message.reply_text("Use /logtrade SYMBOL AMOUNT, e.g. /logtrade BTC -25.50"); return
    symbol = context.args[0].upper()
    try: amount = float(context.args[1].replace("+",""))
    except: await update.message.reply_text("Amount must be a number"); return
    rec = _get_pnl_record(uid)
    rec["pnl"] += amount
    rec["trades"].append(f"{symbol} {amount:+.2f}")
    limit = _get_limit(uid)
    msg = f"📝 Logged {symbol} {amount:+.2f}. Today's P&L: ${rec['pnl']:+.2f} (limit -${limit:.2f})"
    if rec["pnl"] <= -limit and not _loss_limit_active(uid):
        loss_limit_hit_today[uid] = _today()
        auto_users.discard(uid)
        msg += f"\n\n🛑 DAILY LOSS LIMIT HIT. Auto OFF for today."
    await update.message.reply_text(msg)

async def pnl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rec = _get_pnl_record(uid); limit = _get_limit(uid)
    status = "🛑 LIMIT HIT" if _loss_limit_active(uid) else "✅ under limit"
    trades_txt = "\n".join(rec["trades"]) if rec["trades"] else "(none)"
    await update.message.reply_text(f"📊 P&L: ${rec['pnl']:+.2f} / -${limit:.2f} [{status}]\nTrades:\n{trades_txt}")

async def alertstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not alert_tracker:
        await update.message.reply_text("No alerts fired yet today."); return
    lines = [f"{sym}: {rec['count']}/{MAX_ALERTS_PER_SYMBOL_PER_DAY}" for sym, rec in alert_tracker.items()]
    await update.message.reply_text("📊 Alert usage today:\n" + "\n".join(lines))

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👑 BEAST FATHER V12 PRIVATE - FATHER OF THE BEAST!\nLive MTF Wilder RSI+EMA+MACD+ATR+Vol\n/all -> /scan -> /auto on\n/logtrade /setlimit /pnl /alertstats")

async def hunter_loop(app_bot):
    while True:
        try:
            symbol_to_users = {}
            for uid in list(auto_users):
                if _loss_limit_active(uid):
                    auto_users.discard(uid)
                    continue
                syms=user_symbols.get(uid,["NFT","TREASURY","BONDS","AGRI","REALESTATE","SPX","AUTO","ICT","COFFEE","OILGAS","GOLD","CRYPTO","AI","TESLA"])
                for sym in syms:
                    symbol_to_users.setdefault(sym, []).append(uid)

            for sym, uids in symbol_to_users.items():
                d = await asyncio.to_thread(get_data, sym)
                if not d: continue
                a = check_alert(sym.upper(), d)
                if a:
                    if not _alert_allowed(sym.upper()): continue
                    _alert_record(sym.upper())
                    for uid in uids:
                        try: await app_bot.bot.send_message(chat_id=uid, text=a)
                        except Exception as e: print(f"send {uid} err {e}")
                await asyncio.sleep(1)
            await asyncio.sleep(30)  # FATHER: 30s live, not 60s
        except Exception as e:
            print(f"Hunter error {e}")
            await asyncio.sleep(30)

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
