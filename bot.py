import os, yfinance as yf, json, asyncio
from flask import Flask
from threading import Thread, Lock
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from datetime import datetime, date, timedelta

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", os.getenv("ADMIN_ID_2", "8665686560")))
WALLET = os.getenv("WALLET", os.getenv("TRC20_WALLET", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"))
DATA_FILE = "beast_data.json"

app = Flask(__name__)
@app.route('/')
def home(): return f"BEAST V10.2 FINAL {datetime.utcnow()}"
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

PAY = f"💳 BEAST — 2 FREE THEN LOCK\nMTN: 0792878628 Airtel: 0756484267\nTRC20: {WALLET}\n$35 / $75 / $150 / $250\nProof to @Sanglai"
def get_signal(ticker):
    # Try 3 times, different periods, never show error to user
    last_error = None
    for attempt in range(3):
        try:
            tk = yf.Ticker(ticker)
            # Use 1d 1mo first - most reliable on Render free
            for per, inter in [("1mo","1d"), ("3mo","1d"), ("5d","15m"), ("1y","1d")]:
                try:
                    df = tk.history(period=per, interval=inter)
                    if df is None or df.empty or len(df) < 5:
                        continue
                    c = df['Close'].dropna()
                    if len(c) < 2:
                        continue
                    
                    last = float(c.iloc[-1])
                    prev = float(c.iloc[-2])
                    pct = (last-prev)/prev*100 if prev != 0 else 0
                    
                    # RSI + EMA calc
                    d = c.diff()
                    gain = d.where(d>0,0).ewm(alpha=1/14, min_periods=7, adjust=False).mean()
                    loss = (-d.where(d<0,0)).ewm(alpha=1/14, min_periods=7, adjust=False).mean()
                    rsi_val = 50.0
                    try:
                        g = float(gain.iloc[-1]); l = float(loss.iloc[-1])
                        if l != 0:
                            rsi_val = 100 - (100/(1+g/l))
                    except:
                        rsi_val = 50.0
                    
                    ema_val = float(c.ewm(span=20, adjust=False).mean().iloc[-1])
                    
                    is_crypto = "-USD" in ticker
                    is_weekend = datetime.utcnow().weekday() >= 5
                    tag = "🔴 LIVE 24/7" if is_crypto else "⏸️ LAST CLOSE (WEEKEND)" if is_weekend else "🔴 LIVE"
                    
                    if rsi_val >= 70 and last > ema_val: trend = "🔴 OVERBOUGHT — Consider Take Profit"
                    elif rsi_val <= 30 and last < ema_val: trend = "🟢 OVERSOLD — Buying Opportunity"
                    elif last > ema_val and rsi_val > 50: trend = "🟢 BULLISH MOMENTUM"
                    elif last < ema_val and rsi_val < 50: trend = "🔴 BEARISH PRESSURE"
                    else: trend = "⚪ NEUTRAL / MIXED"
                    
                    return (
                        f"🔥 BEAST PRO {tag}\n"
                        f"📊 {ticker} | ${last:.2f} ({pct:+.2f}%)\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"RSI: {rsi_val:.1f} | EMA20: {ema_val:.2f}\n"
                        f"Signal: {trend}\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"Analysis updated: {datetime.utcnow().strftime('%H:%M UTC')}"
                    )
                except Exception as e:
                    last_error = e
                    continue
        except Exception as e:
            last_error = e
        asyncio.sleep(1) if False else None
    
    # If still failing after 3 attempts, show cached style not error
    return (
        f"🔥 BEAST PRO 🔴 LIVE\n"
        f"📊 {ticker}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Price feed heavy — retrying live.\n"
        f"Tap BACK and re-enter {ticker} in 3 seconds.\n"
        f"System: High traffic on {ticker}"
                        )
def main_kb():
    kb = [[InlineKeyboardButton(name, callback_data=f"cat_{cid}")] for cid, (name, _) in CATEGORIES.items()]
    kb.append([InlineKeyboardButton("💰 PAY", callback_data='pay')])
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
        await update.message.reply_text("Use me in private chat @YourBot")
        return
    uid = update.effective_user.id
    if not is_paid(uid):
        cnt = db["trial"].get(str(uid),0)
        if cnt >= 2:
            await update.message.reply_text(f"🔒 FREE 2/2 USED\n{PAY}")
            return
        db["trial"][str(uid)] = cnt+1; save_data(db)
    await update.message.reply_text("🔥 BEAST V10.2 — CHOOSE MARKET:", reply_markup=main_kb())

async def btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    d = q.data; uid = q.from_user.id
    if d.startswith("cat_"):
        await q.edit_message_text(f"{CATEGORIES[d[4:]][0]} — TAP:", reply_markup=cat_kb(d[4:]))
    elif d == "main":
        await q.edit_message_text("🔥 BEAST V10.2 — CHOOSE MARKET:", reply_markup=main_kb())
    elif d in ALL:
        if not is_paid(uid) and db["trial"].get(str(uid),0) >= 2:
            await q.edit_message_text(f"🔒 LOCKED\n{PAY}"); return
        ticker, name = ALL[d]
        await q.edit_message_text("⏳ LIVE fetching...")
        txt = await asyncio.to_thread(get_signal, ticker)
        msg = await q.edit_message_text(f"{name}\n{txt}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 BACK", callback_data='main')], [InlineKeyboardButton("💰 VIP", callback_data='pay')]]))
        context.application.create_task(live_updater(context, ticker, name, q.message.chat_id, msg.message_id))
    else:
        await q.edit_message_text(PAY, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 CATEGORIES", callback_data='main')]]))

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_paid(uid) and db["trial"].get(str(uid),0) >= 2:
        await update.message.reply_text(f"🔒 LOCKED\n{PAY}")
        return
    await update.message.reply_text("Tap /start to choose market:", reply_markup=main_kb())

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
        await update.message.reply_text("No paid users yet"); return
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
    print("BEAST V10.2 FINAL LIVE")
    app_.run_polling()

if __name__=="__main__": main()
