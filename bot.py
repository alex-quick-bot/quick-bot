import os, yfinance as yf, json, asyncio
from flask import Flask
from threading import Thread, Lock
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from datetime import datetime, date, timedelta

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8665686560"))
WALLET = os.getenv("WALLET", os.getenv("TRC20_WALLET", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"))
DATA_FILE = "beast_data.json"
CACHE_FILE = "beast_cache.json"

app = Flask(__name__)
@app.route('/')
def home(): return f"BEAST V11 DOMINATOR {datetime.utcnow()}"
def keep_alive():
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT",10000))), daemon=True).start()

lock = Lock()
ADMIN_IDS = {ADMIN_ID}

def load_data():
    if not os.path.exists(DATA_FILE): return {"paid": {}, "trial": {}}
    try:
        with open(DATA_FILE, "r") as f: return json.load(f)
    except: return {"paid": {}, "trial": {}}
def save_data(d):
    with lock:
        with open(DATA_FILE, "w") as f: json.dump(d, f)
def load_cache():
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r") as f: return json.load(f)
    except: pass
    return {}
def save_cache(c):
    try:
        with open(CACHE_FILE, "w") as f: json.dump(c, f)
    except: pass

db = load_data()

def is_paid(uid):
    if uid in ADMIN_IDS: return True
    exp = db["paid"].get(str(uid))
    if not exp: return False
    try: return datetime.strptime(exp, "%Y-%m-%d").date() >= date.today()
    except: return True

CATEGORIES = {
    'crypto': ("₿ CRYPTO", {'btc': ("BTC-USD","₿ BTC"), 'eth': ("ETH-USD","Ξ ETH"), 'sol': ("SOL-USD","◎ SOL"), 'nfts': ("ETH-USD","🖼️ NFT")}),
    'wallst': ("📈 WALL STREET", {'sp500': ("^GSPC","📈 S&P500"), 'nasdaq': ("^IXIC","📈 NASDAQ"), 'dow': ("^DJI","🏦 DOW"), 'vix': ("^VIX","😱 VIX")}),
    'treasury': ("🏛️ TREASURY & BONDS", {'treasury_10y': ("^TNX","🏛️ 10Y"), 'treasury_tlt': ("TLT","🏛️ TLT"), 'bonds_bnd': ("BND","🏦 BND"), 'bonds_lqd': ("LQD","🏦 LQD")}),
    'oilgas': ("🛢️ OIL & GAS", {'oil': ("CL=F","🛢️ OIL"), 'brent': ("BZ=F","🛢️ BRENT"), 'gas': ("NG=F","⛽ GAS"), 'xle': ("XLE","🛢️ XLE")}),
    'agric': ("🌽 AGRIC & COFFEE", {'corn': ("CORN","🌽 CORN"), 'wheat': ("WEAT","🌾 WHEAT"), 'soy': ("SOYB","🌱 SOY"), 'coffee_jo': ("JO","☕ JO"), 'coffee_sbux': ("SBUX","☕ SBUX")}),
    'realestate': ("🏠 REAL ESTATE", {'re_vnq': ("VNQ","🏠 VNQ"), 're_iyr': ("IYR","🏠 IYR")}),
    'auto_ict': ("🚗 AUTO + ICT + AI", {'tesla': ("TSLA","🚗 TSLA"), 'ford': ("F","🚗 FORD"), 'toyota': ("TM","🚗 TOYOTA"), 'ict_aapl': ("AAPL","📱 AAPL"), 'ict_msft': ("MSFT","💻 MSFT"), 'ict_nvda': ("NVDA","🤖 NVDA"), 'ict_googl': ("GOOGL","🌐 GOOGL"), 'ai_botz': ("BOTZ","🤖 BOTZ"), 'ai_pltr': ("PLTR","🤖 PLTR")}),
    'metals': ("🥇 METALS", {'gold': ("GC=F","🥇 GOLD"), 'silver': ("SI=F","🥈 SILVER")}),
}
ALL = {}
for _, mkts in CATEGORIES.values(): ALL.update(mkts)

PAY = f"💳 BEAST PRO — 2 FREE THEN LOCK\nMTN: 0792878628 Airtel: 0756484267\nTRC20: {WALLET}\n$35 / $75 / $150 / $250\nProof to @Sanglai\n\n⚠️ Educational only, not financial advice."
DISCLAIMER = "\n\n⚠️ NOT FINANCIAL ADVICE. Educational analysis only. Trade at your own risk."

def get_yf_session():
    try:
        from curl_cffi import requests as crequests
        return crequests.Session(impersonate="chrome120")
    except:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0"})
        return s

def get_signal(ticker):
    cache = load_cache()
    session = get_yf_session()
    try:
        tk = yf.Ticker(ticker, session=session)
        df = None
        for per in ["1mo","3mo","5d"]:
            try:
                df = tk.history(period=per, interval="1d", auto_adjust=False)
                if df is not None and not df.empty and len(df) >= 2:
                    break
            except: continue
        if df is not None and not df.empty:
            c = df['Close'].dropna()
            last = float(c.iloc[-1]); prev = float(c.iloc[-2])
            pct = (last-prev)/prev*100 if prev else 0
            d = c.diff()
            g = d.where(d>0,0).ewm(alpha=1/14, min_periods=7, adjust=False).mean()
            l = (-d.where(d<0,0)).ewm(alpha=1/14, min_periods=7, adjust=False).mean()
            rsi = 50.0
            try:
                gl = float(g.iloc[-1]); ll = float(l.iloc[-1])
                if ll != 0: rsi = 100 - (100/(1+gl/ll))
            except: pass
            ema20 = float(c.ewm(span=20, adjust=False).mean().iloc[-1])
            ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])
            high = float(df['High'].max()); low = float(df['Low'].min())
            vol = float(df['Volume'].iloc[-1]) if 'Volume' in df else 0
            cache[ticker] = {"price": last, "pct": pct, "rsi": rsi, "ema20": ema20, "ema50": ema50, "high": high, "low": low, "vol": vol, "time": datetime.utcnow().isoformat()}
            save_cache(cache)
            if rsi >= 70 and last > ema20: sig = f"🔴 OVERBOUGHT (RSI {rsi:.0f}) — Profit-taking risk"
            elif rsi <= 30 and last < ema20: sig = f"🟢 OVERSOLD (RSI {rsi:.0f}) — Reversal watch"
            elif last > ema20 > ema50 and rsi > 55: sig = f"🟢 STRONG BULLISH — Above EMA20/50"
            elif last < ema20 < ema50 and rsi < 45: sig = f"🔴 STRONG BEARISH — Below EMA20/50"
            else: sig = f"⚪ CONSOLIDATION — Wait for breakout"
            return f"🔥 BEAST PRO 🔴 LIVE • {ticker}\n💰 ${last:.2f} ({pct:+.2f}%) • Vol {vol:,.0f}\n━━━━━━━━━━━━━━━\nRSI: {rsi:.1f} | EMA20: {ema20:.2f} | EMA50: {ema50:.2f}\nRange: ${low:.2f} - ${high:.2f}\n━━━━━━━━━━━━━━━\n🎯 {sig}{DISCLAIMER}"
    except: pass
    if ticker in cache:
        cd = cache[ticker]
        return f"🔥 BEAST PRO 🔴 LIVE • {ticker} (Last Verified)\n💰 ${cd['price']:.2f} ({cd.get('pct',0):+.2f}%)\n━━━━━━━━━━━━━━━\nRSI: {cd.get('rsi',50):.1f} | EMA20: {cd.get('ema20',cd['price']):.2f}\n━━━━━━━━━━━━━━━\n🎯 Showing last real market price from cache — live sync in progress.{DISCLAIMER}"
    return f"🔥 BEAST PRO • {ticker}\nMarket data initializing — real feed loading, tap again in 3s.{DISCLAIMER}"

def main_kb():
    kb = [[InlineKeyboardButton(name, callback_data=f"cat_{cid}")] for cid, (name, _) in CATEGORIES.items()]
    kb.append([InlineKeyboardButton("💰 PAY / VIP", callback_data='pay')])
    return InlineKeyboardMarkup(kb)
def cat_kb(cid):
    _, mkts = CATEGORIES[cid]
    kb=[]; row=[]
    for mid, (_, label) in mkts.items():
        row.append(InlineKeyboardButton(label, callback_data=mid))
        if len(row)==2: kb.append(row); row=[]
    if row: kb.append(row)
    kb.append([InlineKeyboardButton("🔙 CATEGORIES", callback_data='main')])
    return InlineKeyboardMarkup(kb)

async def live_updater(context, ticker, name, chat_id, msg_id):
    for i in range(1, 11):
        await asyncio.sleep(60)
        txt = await asyncio.to_thread(get_signal, ticker)
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"{name}\n{txt}\nAUTO {i}/10", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"🔴 LIVE {i}/10 STOP", callback_data='main')], [InlineKeyboardButton("💰 VIP", callback_data='pay')]]))
        except: break

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Use me in private @YourBot"); return
    uid = update.effective_user.id
    if not is_paid(uid):
        cnt = db["trial"].get(str(uid),0)
        if cnt >= 2:
            await update.message.reply_text(f"🔒 FREE 2/2 USED\n{PAY}"); return
        db["trial"][str(uid)] = cnt+1; save_data(db)
    await update.message.reply_text("🔥 BEAST V11 DOMINATOR — CHOOSE MARKET:", reply_markup=main_kb())

async def btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    d = q.data; uid = q.from_user.id
    if d.startswith("cat_"):
        await q.edit_message_text(f"{CATEGORIES[d[4:]][0]} — TAP:", reply_markup=cat_kb(d[4:]))
    elif d == "main":
        await q.edit_message_text("🔥 BEAST V11 — CHOOSE MARKET:", reply_markup=main_kb())
    elif d in ALL:
        if not is_paid(uid) and db["trial"].get(str(uid),0) >= 2:
            await q.edit_message_text(f"🔒 LOCKED\n{PAY}"); return
        ticker, name = ALL[d]
        await q.edit_message_text("⏳ LIVE fetching real data...")
        txt = await asyncio.to_thread(get_signal, ticker)
        msg = await q.edit_message_text(f"{name}\n{txt}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 BACK", callback_data='main')], [InlineKeyboardButton("💰 VIP", callback_data='pay')]]))
        context.application.create_task(live_updater(context, ticker, name, q.message.chat_id, msg.message_id))
    else:
        await q.edit_message_text(PAY, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 CATEGORIES", callback_data='main')]]))

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_paid(uid) and db["trial"].get(str(uid),0) >= 2:
        await update.message.reply_text(f"🔒 LOCKED\n{PAY}"); return
    await update.message.reply_text("Tap /start:", reply_markup=main_kb())

async def addpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addpaid USER_ID DAYS"); return
    tid=str(context.args[0]); days=int(context.args[1])
    exp=(date.today()+timedelta(days=days)).strftime("%Y-%m-%d")
    db["paid"][tid]=exp; save_data(db)
    await update.message.reply_text(f"✅ {tid} until {exp}")

async def removepaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not context.args:
        await update.message.reply_text("Usage: /removepaid USER_ID"); return
    tid=str(context.args[0]); db["paid"].pop(tid,None); db["trial"].pop(tid,None); save_data(db)
    await update.message.reply_text(f"🗑️ Removed {tid}")

async def listpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not db["paid"]:
        await update.message.reply_text("No paid yet"); return
    txt="\n".join([f"{k} -> {v}" for k,v in db["paid"].items()])
    await update.message.reply_text(f"💰 PAID:\n{txt}")

def main():
    keep_alive()
    app_ = Application.builder().token(TOKEN).build()
    app_.add_handler(CommandHandler("start", start))
    app_.add_handler(CommandHandler("addpaid", addpaid))
    app_.add_handler(CommandHandler("removepaid", removepaid))
    app_.add_handler(CommandHandler("listpaid", listpaid))
    app_.add_handler(CallbackQueryHandler(btn))
    app_.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("BEAST V11 DOMINATOR LIVE")
    app_.run_polling()

if __name__=="__main__": main()
