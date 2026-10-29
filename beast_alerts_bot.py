import os
from flask import Flask
import threading, yfinance as yf, asyncio, time
from datetime import date as _date
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")  # set your ID in Render env

app = Flask(__name__)
@app.route('/')
def home(): return "BEAST FATHER V12 PRIVATE - LIVE MTF MARKET ORGANIZED + MT5 🔥"
def run_flask(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

# === STATE ===
user_symbols = {}
auto_users = set()
last_state = {}
AUTO_TRADE_ENABLED = set()
USER_LOT = {}
AUTO_SYMBOLS = {}

# === YOUR GUARDS ===
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

# === MARKET ORGANIZATION V7 STYLE ===
DEFAULT_SYMBOLS = ["NFT","TREASURY","BONDS","AGRI","REALESTATE","SPX","AUTO","ICT","OILGAS","GOLD","CRYPTO","AI","TESLA"]
MARKET_GROUPS = {
    "🏦 BONDS & TREASURY": ["TREASURY", "BONDS"],
    "🛢️ COMMODITIES & ENERGY": ["GOLD", "OILGAS", "AGRI"],
    "🏠 REAL ECONOMY": ["REALESTATE", "SPX", "AUTO"],
    "💻 TECH & AI": ["ICT", "AI", "TESLA"],
    "₿ CRYPTO & META": ["NFT", "CRYPTO"],
}

def fix_symbol(s):
    s=s.upper().strip()
    M={"NFT":"MANA-USD","NFTS":"MANA-USD","MANA":"MANA-USD","APE":"APE-USD","SAND":"SAND-USD",
        "TREASURY":"TLT","GOVT":"TLT","BONDS":"TLT","BOND":"TLT","TLT":"TLT",
        "AGRI":"DBA","AGRICULTURE":"DBA","DBA":"DBA",
        "REALESTATE":"VNQ","REIT":"VNQ","VNQ":"VNQ",
        "SPX":"SPY","WALLSTREET":"SPY","SPY":"SPY",
        "AUTO":"CARZ","CARZ":"CARZ","FORD":"F","GM":"GM","RIVN":"RIVN",
        "ICT":"XLK","XLK":"XLK",
        "OILGAS":"XLE","XLE":"XLE","OIL":"CL=F","GOLD":"GLD","GLD":"GLD",
        "CRYPTO":"BTC-USD","BTC":"BTC-USD","ETH":"ETH-USD","SOL":"SOL-USD",
        "AI":"BOTZ","BOTZ":"BOTZ","TESLA":"TSLA","TSLA":"TSLA",}
    return M.get(s,s)

SECTOR_COMPANIES = {
    "NFT": "MANA, APE Bored Ape -> BUY MANA", "TREASURY": "TLT 20Y Bond -> Buy TLT",
    "BONDS": "TLT 20Y Bond -> Buy TLT", "AGRI": "DBA Corn Wheat Soy -> Buy DBA",
    "REALESTATE": "VNQ PLD AMT -> Buy VNQ", "SPX": "SPY Wall St AAPL MSFT NVDA -> Buy SPY",
    "AUTO": "CARZ TSLA Ford GM -> BUY TSLA", "ICT": "XLK AAPL MSFT NVDA -> BUY AAPL/NVDA",
    "OILGAS": "XLE XOM Chevron -> BUY XOM", "GOLD": "GLD Gold -> Buy GLD",
    "CRYPTO": "BTC ETH SOL -> Buy BTC", "AI": "BOTZ NVDA META PLTR -> BUY NVDA",
    "TESLA": "TSLA alone -> Buy TSLA",
}

# === MT5 WRAPPER ===
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except:
    MT5_AVAILABLE = False
    mt5 = None

def mt5_connect():
    if not MT5_AVAILABLE: return False, "MT5 not installed. On Windows: pip install MetaTrader5 + install MT5 terminal"
    if not mt5.initialize(): return False, f"MT5 init failed: {mt5.last_error()}"
    return True, "MT5 Connected"

def mt5_place_order(symbol_mt5, order_type, lot, stop_loss=0, take_profit=0):
    if not MT5_AVAILABLE: return "MT5 not available (Linux/Render can't run MT5, need Windows VPS)"
    MT5_MAP = {"CL=F":"USOIL","BZ=F":"UKOIL","BTC-USD":"BTCUSD","ETH-USD":"ETHUSD","SOL-USD":"SOLUSD","GLD":"XAUUSD","GOLD":"XAUUSD","SPY":"US500","TSLA":"TSLA","AAPL":"AAPL","NVDA":"NVDA","VNQ":"VNQ","IYR":"IYR","TLT":"TLT","DBA":"DBA","XLE":"XLE","CARZ":"CARZ","XLK":"XLK","BOTZ":"BOTZ","MANA-USD":"MANAUSD"}
    mt5_symbol = MT5_MAP.get(symbol_mt5, symbol_mt5)
    info = mt5.symbol_info(mt5_symbol)
    if info is None: return f"Symbol {mt5_symbol} not found in MT5 MarketWatch"
    if not info.visible: mt5.symbol_select(mt5_symbol, True)
    tick = mt5.symbol_info_tick(mt5_symbol)
    if tick is None: return "No price"
    req = {"action": mt5.TRADE_ACTION_DEAL,"symbol": mt5_symbol,"volume": float(lot),
           "type": mt5.ORDER_TYPE_BUY if order_type=="BUY" else mt5.ORDER_TYPE_SELL,
           "price": tick.ask if order_type=="BUY" else tick.bid,
           "sl": float(stop_loss),"tp": float(take_profit),"deviation": 20,"magic": 123456,
           "comment": "FATHER V12","type_time": mt5.ORDER_TIME_GTC,"type_filling": mt5.ORDER_FILLING_IOC}
    result = mt5.order_send(req)
    if result.retcode != mt5.TRADE_RETCODE_DONE: return f"FAILED {mt5_symbol} {result.retcode} {result.comment}"
    return f"✅ EXECUTED {order_type} {mt5_symbol} {lot} lot Ticket {result.order}"

# === FATHER ENGINE ===
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
    return float(100 - (100 / (1 + last_gain / last_loss)))

def calc_ema(close, span):
    if len(close) < span: return None
    return float(close.ewm(span=span, adjust=False).mean().iloc[-1])

def get_data(symbol):
    try:
        ysym=fix_symbol(symbol)
        session = get_yf_session()
        hist=None
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
        ema12=calc_ema(c,12); ema26=calc_ema(c,26); ema50=calc_ema(c,50); ema200=calc_ema(c,200)
        sma20=float(c.rolling(20).mean().iloc[-1]); sma50=float(c.rolling(50).mean().iloc[-1])
        rsi_now=calc_rsi_wilder(c,14)
        if rsi_now is None or ema12 is None or ema26 is None: return None
        high20=float(hist['High'].rolling(20).max().iloc[-1]); low20=float(hist['Low'].rolling(20).min().iloc[-1])
        ema12_series=c.ewm(span=12).mean(); ema26_series=c.ewm(span=26).mean()
        macd=float((ema12_series-ema26_series).iloc[-1])
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

def _f(v, fmt=".1f"):
    if v is None: return "—"
    try: return format(float(v), fmt)
    except: return "—"

def make_text(orig,d):
    curr=d["curr"]; rsi=d["rsi"]; ema12=d["ema12"]; ema26=d["ema26"]; sma20=d["sma20"]; high20=d["high20"]; low20=d["low20"]; tier=d["tier"]
    macd=d.get("macd",0); atr=d.get("atr", curr*0.02); vol=d.get("vol",0); vol_mult=d.get("vol_mult",1)
    ema50=d.get("ema50")
    if rsi<30:
        action="💎💎 FATHER STRONG BUY"; reason=f"Oversold + MACD {_f(macd)} Vol x{_f(vol_mult)}"
        tgt=curr+3*atr; stp=_safe_stop(curr, low20*0.98, True); prof="+8% (ATR)"
    elif rsi<38 and ema12>ema26:
        action="💎 FATHER BUY"; reason=f"Cheap + Bullish MACD {_f(macd)}"
        tgt=high20; stp=_safe_stop(curr, sma20*0.97, True); prof="+5-7%"
    elif rsi>75:
        action="⚠️⚠️ FATHER STRONG SELL"; reason=f"Overbought RSI {_f(rsi)} Vol x{_f(vol_mult)}"
        tgt=sma20; stp=_safe_stop(curr, high20*1.02, False); prof="SELL"
    elif ema12>ema26 and curr>sma20:
        action="🚀 FATHER BULLISH BREAKOUT"; reason=f"Above EMA20/50 Vol x{_f(vol_mult)}"
        tgt=curr+2*atr; stp=_safe_stop(curr, curr-2*atr, True); prof="+4-6%"
    else:
        action="➡️ FATHER HOLD"; reason=f"RSI {_f(rsi)} EMA {_f(ema12)}/{_f(ema26)}/{_f(ema50)}"
        tgt=high20; stp=low20; prof="Hold"
    comp=SECTOR_COMPANIES.get(orig, orig)
    return (f"👑 {action} {orig} TIER {tier}% | FATHER V12\n"
            f"💰 ${curr:.2f} RSI {_f(rsi)} EMA {_f(ema12)}/{_f(ema26)}/{_f(ema50)} | MACD {_f(macd)}\n"
            f"📊 Vol {vol:,.0f} x{_f(vol_mult)} ATR ${atr:.2f} | Range ${low20:.2f}-${high20:.2f}\n"
            f"💡 {reason}\n"
            f"🎯 ENTRY ${curr:.2f} STOP ${stp:.2f} TARGET ${tgt:.2f} {prof}\n"
            f"🏢 {comp}\n---")

def check_alert(orig,d):
    rsi=d["rsi"]; ema12=d["ema12"]; ema26=d["ema26"]; curr=d["curr"]; tier=d["tier"]
    key=orig; prev=last_state.get(key,{})
    alert=None; trade_action=None
    if rsi < 32 and prev.get("rsi",50) >= 32:
        alert=f"💎💎 FATHER BUY ALERT {orig} ${curr:.2f} RSI {rsi:.1f} TIER {tier}%"
        trade_action="BUY"
    elif rsi > 72 and prev.get("rsi",50) <= 72:
        alert=f"⚠️ FATHER SELL ALERT {orig} ${curr:.2f} RSI {rsi:.1f}"
        trade_action="SELL"
    elif ema12 > ema26 and prev.get("ema12",0) <= prev.get("ema26",0):
        alert=f"🚀 FATHER GOLDEN CROSS {orig} ${curr:.2f} TIER {tier}%"
        trade_action="BUY"
    last_state[key]={"rsi":rsi,"ema12":ema12,"ema26":ema26}
    return alert, trade_action, d

# === COMMANDS ===
async def scan_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    syms=user_symbols.get(update.effective_user.id, DEFAULT_SYMBOLS)
    await update.message.reply_text(f"👑 FATHER V12 PRIVATE - Scanning {len(syms)} sectors by MARKET...")
    grouped = {}
    for market, group_syms in MARKET_GROUPS.items():
        for s in group_syms:
            if s in syms:
                grouped.setdefault(market, []).append(s)
    for market_name, market_syms in grouped.items():
        batch = f"\n{market_name}\n━━━━━━━━━━━━━━\n"
        has=False
        for sym in market_syms:
            try:
                d=await asyncio.wait_for(asyncio.to_thread(get_data, sym), timeout=15)
                if not d: batch+=f"❌ {sym} No data\n"
                else: batch+=make_text(sym.upper(), d)+"\n"; has=True
            except Exception as e: batch+=f"⚠️ {sym} error {e}\n"
            await asyncio.sleep(0.2)
        if has: await update.message.reply_text(batch)
    await update.message.reply_text(f"✅ FATHER DONE — {len(syms)} organized by market!")

async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_symbols[update.effective_user.id]=DEFAULT_SYMBOLS
    await update.message.reply_text("🌍 ALL 13 LOCKED (COFFEE removed) — Market organized!\n🏦 Bonds | 🛢️ Commodities | 🏠 Real | 💻 Tech | ₿ Crypto\n/scan to see")

async def myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use /all or /myalerts ICT AUTO GOLD..."); return
    user_symbols[update.effective_user.id]=[s.upper() for s in context.args[:13]]
    await update.message.reply_text(f"✅ Locked: {', '.join(user_symbols[update.effective_user.id])} FATHER V12")

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args or context.args[0].lower()=="on":
        if _loss_limit_active(uid):
            lim = _get_limit(uid); pnl = _get_pnl_record(uid)["pnl"]
            await update.message.reply_text(f"🛑 Loss limit -${lim:.2f} hit (P&L ${pnl:+.2f}). Paused till tomorrow. /pnl /setlimit"); return
        auto_users.add(uid)
        await update.message.reply_text("✅ FATHER Auto ON! 30s live hunter with Wilder RSI+MACD+ATR!")
    else:
        auto_users.discard(uid)
        await update.message.reply_text("❌ Auto OFF")

async def setlimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text(f"Current limit: ${_get_limit(uid):.2f}\nUse /setlimit 75"); return
    try: val = abs(float(context.args[0]))
    except: await update.message.reply_text("Use number e.g. /setlimit 75"); return
    user_loss_limit[uid] = val
    await update.message.reply_text(f"✅ Daily loss limit set to ${val:.2f}.")

async def logtrade_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if len(context.args) < 2:
        await update.message.reply_text("Use /logtrade SYMBOL AMOUNT e.g. /logtrade BTC -25.50"); return
    symbol = context.args[0].upper()
    try: amount = float(context.args[1].replace("+",""))
    except: await update.message.reply_text("Amount must be number"); return
    rec = _get_pnl_record(uid)
    rec["pnl"] += amount
    rec["trades"].append(f"{symbol} {amount:+.2f}")
    limit = _get_limit(uid)
    msg = f"📝 Logged {symbol} {amount:+.2f}. P&L: ${rec['pnl']:+.2f} (limit -${limit:.2f})"
    if rec["pnl"] <= -limit and not _loss_limit_active(uid):
        loss_limit_hit_today[uid] = _today()
        auto_users.discard(uid)
        AUTO_TRADE_ENABLED.discard(uid)
        msg += f"\n\n🛑 DAILY LOSS LIMIT HIT. Auto & Auto-trade OFF today."
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
    await update.message.reply_text("👑 BEAST FATHER V12 PRIVATE FINAL!\nMarket organized V7 style, COFFEE removed, MT5 ready\n/all -> /scan -> /auto on\nAuto-trade: /mt5login /autotrade on /setlot 0.01 /setmt5 BTC GOLD\nRisk: /logtrade /setlimit /pnl /alertstats")

# MT5 COMMANDS
async def mt5login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, msg = mt5_connect()
    await update.message.reply_text(f"{'✅' if ok else '❌'} {msg}\nMT5 Available: {MT5_AVAILABLE}\nNote: Render Linux can't run MT5, need Windows VPS/PC for real execution")

async def autotrade_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        status = "ON" if uid in AUTO_TRADE_ENABLED else "OFF"
        await update.message.reply_text(f"Auto-trade is {status}\nUse: /autotrade on or off\nThen /setmt5 BTC GOLD\n/setlot 0.01"); return
    if context.args[0].lower()=="on":
        AUTO_TRADE_ENABLED.add(uid)
        await update.message.reply_text("🤖 FATHER AUTO-TRADE ON — will EXECUTE on MT5 when signal fires!\nSet lot /setlot 0.01 and symbols /setmt5")
    else:
        AUTO_TRADE_ENABLED.discard(uid)
        await update.message.reply_text("❌ Auto-trade OFF — alerts only")

async def setlot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text(f"Lot: {USER_LOT.get(uid,0.01)}\nUse /setlot 0.05"); return
    try:
        USER_LOT[uid]=float(context.args[0])
        await update.message.reply_text(f"✅ Lot set to {USER_LOT[uid]}")
    except: await update.message.reply_text("Use number e.g. /setlot 0.01")

async def setmt5_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text(f"Auto symbols: {AUTO_SYMBOLS.get(uid,'ALL')}\nUse /setmt5 BTC GOLD OILGAS"); return
    AUTO_SYMBOLS[uid]=set([s.upper() for s in context.args])
    await update.message.reply_text(f"✅ Auto-trade only: {', '.join(AUTO_SYMBOLS[uid])}")

# === HUNTER LOOP WITH AUTO-TRADE ===
async def hunter_loop(app_bot):
    while True:
        try:
            symbol_to_users = {}
            for uid in list(auto_users):
                if _loss_limit_active(uid):
                    auto_users.discard(uid)
                    AUTO_TRADE_ENABLED.discard(uid)
                    continue
                syms=user_symbols.get(uid, DEFAULT_SYMBOLS)
                for sym in syms:
                    symbol_to_users.setdefault(sym, []).append(uid)
            for sym, uids in symbol_to_users.items():
                d = await asyncio.to_thread(get_data, sym)
                if not d: continue
                alert, trade_action, full_d = check_alert(sym.upper(), d)
                if alert:
                    if not _alert_allowed(sym.upper()): continue
                    _alert_record(sym.upper())
                    atr = full_d.get("atr", full_d["curr"]*0.02)
                    curr = full_d["curr"]
                    for uid in uids:
                        extra=""
                        if trade_action and uid in AUTO_TRADE_ENABLED:
                            allowed = AUTO_SYMBOLS.get(uid, set())
                            if not allowed or sym.upper() in allowed:
                                lot = USER_LOT.get(uid, 0.01)
                                sl = curr - 2*atr if trade_action=="BUY" else curr + 2*atr
                                tp = curr + 3*atr if trade_action=="BUY" else curr - 3*atr
                                res = mt5_place_order(sym.upper(), trade_action, lot, sl, tp)
                                extra = f"\n🤖 MT5: {res}"
                        try:
                            await app_bot.bot.send_message(chat_id=uid, text=alert+extra)
                        except Exception as e: print(f"send {uid} err {e}")
                await asyncio.sleep(0.5)
            await asyncio.sleep(30)
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
    bot_app.add_handler(CommandHandler("mt5login", mt5login_cmd))
    bot_app.add_handler(CommandHandler("autotrade", autotrade_cmd))
    bot_app.add_handler(CommandHandler("setlot", setlot_cmd))
    bot_app.add_handler(CommandHandler("setmt5", setmt5_cmd))
    async def on_start(a): asyncio.create_task(hunter_loop(a))
    bot_app.post_init=on_start
    bot_app.run_polling()
