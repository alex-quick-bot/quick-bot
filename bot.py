import os, yfinance as yf, asyncio, re, json
from flask import Flask
from threading import Thread, Lock
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from datetime import datetime, date, timedelta

TOKEN = os.getenv("BOT_TOKEN")
TRC20_WALLET = os.getenv("TRC20_WALLET", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
DATA_FILE = "beast_data.json"
ADMIN_ID = 8665686560

print(f"BEAST V7.7 FINAL ADMIN {ADMIN_ID}")

app = Flask(__name__)
@app.route('/')
def home(): return f"BEAST V7.7 FINAL LIVE - {datetime.utcnow()}"
def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
def keep_alive(): Thread(target=run_flask, daemon=True).start()

lock = Lock()
ADMIN_IDS = {ADMIN_ID}

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"paid": {}, "trial": {}}
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {"paid": {}, "trial": {}}

def save_data(data):
    with lock:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f)

db = load_data()

def is_paid(uid: int) -> bool:
    if uid in ADMIN_IDS: return True
    exp = db["paid"].get(str(uid))
    if not exp: return False
    try: return datetime.strptime(exp, "%Y-%m-%d").date() >= date.today()
    except: return True

def get_trial(uid: int) -> int: return db["trial"].get(str(uid), 0)

def add_trial(uid: int):
    db["trial"][str(uid)] = get_trial(uid) + 1
    save_data(db)

SECTORS = {
    'sp500': ("^GSPC", "📈 S&P500"), 'nasdaq': ("^IXIC", "📈 NASDAQ"),
    'dow': ("^DJI", "🏦 DOW"), 'vix': ("^VIX", "😱 VIX"),
    'treasury_10y': ("^TNX", "🏛️ 10Y Yield"), 'treasury_tlt': ("TLT", "🏛️ TLT"),
    'bonds_bnd': ("BND", "🏦 BND"), 'bonds_lqd': ("LQD", "🏦 LQD"),
    'btc': ("BTC-USD", "₿ BTC"), 'eth': ("ETH-USD", "Ξ ETH"),
    'sol': ("SOL-USD", "◎ SOL"), 'nfts': ("ETH-USD", "🖼️ NFTS"),
    'oil': ("CL=F", "🛢️ OIL"), 'brent': ("BZ=F", "🛢️ BRENT"),
    'gas': ("NG=F", "⛽ GAS"), 'xle': ("XLE", "🛢️ XLE"),
    'corn': ("CORN", "🌽 CORN"), 'wheat': ("WEAT", "🌾 WHEAT"),
    'soy': ("SOYB", "🌱 SOY"), 'coffee_jo': ("JO", "☕ JO"),
    'coffee_sbux': ("SBUX", "☕ SBUX"), 're_vnq': ("VNQ", "🏠 VNQ"),
    're_iyr': ("IYR", "🏠 IYR"), 'tesla': ("TSLA", "🚗 TSLA"),
    'ford': ("F", "🚗 FORD"), 'toyota': ("TM", "🚗 TOYOTA"),
    'ict_aapl': ("AAPL", "📱 AAPL"), 'ict_msft': ("MSFT", "💻 MSFT"),
    'ict_nvda': ("NVDA", "🤖 NVDA"), 'ict_googl': ("GOOGL", "🌐 GOOGL"),
    'ai_botz': ("BOTZ", "🤖 BOTZ"), 'ai_pltr': ("PLTR", "🤖 PLTR"),
    'gold': ("GC=F", "🥇 GOLD"), 'silver': ("SI=F", "🥈 SILVER"),
}

PAYMENT_TEXT = f"💳 BEAST V7.7 — 2 FREE THEN LOCK\nMTN: 0792878628\nAirtel: 0756484267\nTRC20: `{TRC20_WALLET}`\n$35 / $75 / $150 / $250\nScreenshot to @Sanglai"

def calc_rsi(close, period=14):
    if len(close) < period + 1: return None
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    ag = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    al = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    lg, ll = ag.iloc[-1], al.iloc[-1]
    if ll == 0: return 100.0 if lg > 0 else 50.0
    return 100 - (100 / (1 + lg/ll))

def calc_ema(close, span=20):
    if len(close) < span: return None
    return close.ewm(span=span, adjust=False).mean().iloc[-1]

def get_live_signal(ticker_symbol):
    try:
        tk = yf.Ticker(ticker_symbol)
        data = None
        for per, inter in [("1d","1m"), ("5d","5m"), ("1mo","15m"), ("3mo","1h"), ("1y","1d")]:
            try:
                d = tk.history(period=per, interval=inter)
                if not d.empty and len(d) >= 20:
                    data = d
                    break
            except:
                continue
        if data is None or data.empty:
            return f"⏳ Syncing {ticker_symbol} — tap again in 5s"
        close = data['Close']
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) > 1 else last
        pct = (last-prev)/prev*100 if prev != 0 else 0.0
        rsi = calc_rsi(close)
        ema = calc_ema(close)
        now_utc = datetime.utcnow()
        is_weekend = now_utc.weekday() >= 5
        is_crypto = ticker_symbol.endswith("-USD") or ticker_symbol in ["BTC-USD","ETH-USD","SOL-USD"]
        if is_crypto:
            live_tag = "🔴 LIVE 24/7"
        else:
            live_tag = "⏸️ WEEKEND — LAST CLOSE" if is_weekend else "🔴 LIVE"
        if rsi is None or ema is None:
            return f"🔥 {live_tag}\n{ticker_symbol} ${last:.4f} {pct:+.2f}%\nRSI/EMA warming up — tap again"
        rstate = "Overbought" if rsi >= 70 else "Oversold" if rsi <= 30 else "Healthy"
        if rsi >= 70 and last > ema: trend = "🔴 OVERBOUGHT"
        elif rsi <= 30 and last < ema: trend = "🟢 OVERSOLD"
        elif last > ema and rsi > 50: trend = "🟢 BULLISH"
        elif last < ema and rsi < 50: trend = "🔴 BEARISH"
        else: trend = "⚪ MIXED"
        return f"🔥 {live_tag}\nTicker: {ticker_symbol}\nPrice: ${last:.4f} {pct:+.2f}%\nRSI: {rsi:.1f} ({rstate}) EMA: {ema:.2f}\nSignal: {trend}\nNot financial advice"
    except Exception as e:
        return f"Retry {ticker_symbol} — {str(e)[:100]}"

async def live_updater(context, ticker, name, chat_id, message_id):
    for i in range(1, 11):
        await asyncio.sleep(60)
        txt = await asyncio.to_thread(get_live_signal, ticker)
        try:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🔴 LIVE {i}/10 STOP", callback_data='menu')], [InlineKeyboardButton("💰 VIP", callback_data='pay')]])
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"{name}\n{txt}\nAUTO {i}/10", reply_markup=kb)
        except:
            break

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Use in private chat")
        return
    uid = update.effective_user.id
    if not is_paid(uid):
        cnt = get_trial(uid)
        if cnt >= 2:
            await update.message.reply_text(f"🔒 FREE 2/2 USED\n{PAYMENT_TEXT}", parse_mode='Markdown')
            return
        add_trial(uid)
        await update.message.reply_text(f"✅ FREE TRIAL {cnt+1}/2")
    kb = []; row = []
    for k, (_, label) in SECTORS.items():
        row.append(InlineKeyboardButton(label, callback_data=k))
        if len(row) == 2: kb.append(row); row = []
    if row: kb.append(row)
    kb.append([InlineKeyboardButton("💰 PAY", callback_data='pay')])
    await update.message.reply_text("🔥 BEAST V7.7 — 34 LIVE MARKETS — Tap:", reply_markup=InlineKeyboardMarkup(kb))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id; data = q.data
    if data in SECTORS and not is_paid(uid) and get_trial(uid) >= 2:
        await q.edit_message_text(f"🔒 Trial done\n{PAYMENT_TEXT}", parse_mode='Markdown')
        return
    if data in SECTORS:
        await q.edit_message_text("⏳ LIVE fetching...")
        ticker, name = SECTORS[data]
        txt = await asyncio.to_thread(get_live_signal, ticker)
        msg = await q.edit_message_text(f"{name}\n{txt}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 STOP", callback_data='menu')], [InlineKeyboardButton("💰 VIP", callback_data='pay')]]))
        context.application.create_task(live_updater(context, ticker, name, q.message.chat_id, msg.message_id))
    elif data == 'menu':
        kb = []; row = []
        for k, (_, label) in SECTORS.items():
            row.append(InlineKeyboardButton(label, callback_data=k))
            if len(row) == 2: kb.append(row); row = []
        await q.edit_message_text("🔥 BEAST V7.7 — 34 LIVE:", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await q.edit_message_text(f"{data.upper()}\n{PAYMENT_TEXT}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 MENU", callback_data='menu')]]), parse_mode='Markdown')

async def addpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addpaid USER_ID DAYS")
        return
    try:
        tid = str(context.args[0]); days = int(context.args[1])
        exp = (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")
        db["paid"][tid] = exp; save_data(db)
        await update.message.reply_text(f"✅ {tid} until {exp}")
    except Exception as e: await update.message.reply_text(f"Error {e}")

async def removepaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not context.args:
        await update.message.reply_text("Usage: /removepaid USER_ID"); return
    tid = str(context.args[0]); db["paid"].pop(tid, None); db["trial"].pop(tid, None); save_data(db)
    await update.message.reply_text(f"🗑️ Removed {tid}")

async def listpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not db["paid"]: await update.message.reply_text("No paid users yet"); return
    txt = "\n".join([f"{k} -> {v}" for k,v in db["paid"].items()])
    await update.message.reply_text(f"💰 PAID:\n{txt}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_paid(uid) and get_trial(uid) >= 2:
        await update.message.reply_text(f"🔒 Trial done\n{PAYMENT_TEXT}", parse_mode='Markdown'); return
    await update.message.reply_text("Tap /start for live markets", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 MENU", callback_data='menu')]]))

def main():
    keep_alive()
    if not TOKEN: print("BOT_TOKEN missing"); return
    app_ = Application.builder().token(TOKEN).build()
    app_.add_handler(CommandHandler("start", start))
    app_.add_handler(CommandHandler("addpaid", addpaid))
    app_.add_handler(CommandHandler("removepaid", removepaid))
    app_.add_handler(CommandHandler("listpaid", listpaid))
    app_.add_handler(CallbackQueryHandler(button))
    app_.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("BEAST V7.7 polling")
    app_.run_polling()

if __name__ == "__main__": main()
