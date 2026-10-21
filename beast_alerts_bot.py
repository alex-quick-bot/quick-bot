import os
from flask import Flask
import threading
import time
import yfinance as yf
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")

app = Flask(__name__)
@app.route('/')
def home(): return "BEAST V2 SUPER SIGNALS LIVE 🔥"

def run_flask(): app.run(host='0.0.0.0', port=10000)

alerts = {}
user_symbols = {}  # store symbols per user for auto scan

# === SUPER SIGNAL ENGINE ===
def get_super_signals(symbol):
    try:
        stock = yf.Ticker(symbol)
        hist = stock.history(period="3mo")
        if len(hist) < 50: return None
        
        close = hist['Close']
        vol = hist['Volume']
        curr = close.iloc[-1]
        prev = close.iloc[-2]
        
        sma20 = close.rolling(20).mean().iloc[-1]
        sma50 = close.rolling(50).mean().iloc[-1]
        ema12 = close.ewm(span=12).mean().iloc[-1]
        ema26 = close.ewm(span=26).mean().iloc[-1]
        
        # RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        rsi_now = rsi.iloc[-1]
        
        high_20 = hist['High'].rolling(20).max().iloc[-1]
        low_20 = hist['Low'].rolling(20).min().iloc[-1]
        avg_vol = vol.rolling(20).mean().iloc[-1]
        
        signals = []
        strength = 0

        # 1. BREAKOUT
        if curr >= high_20 * 0.998:
            signals.append(f"🚀 BREAKOUT! {symbol} breaking 20-day high ${high_20:.2f}")
            strength += 3
        # 2. BREAKDOWN
        if curr <= low_20 * 1.002:
            signals.append(f"🔻 BREAKDOWN! {symbol} breaking 20-day low ${low_20:.2f}")
            strength += 3
        # 3. GOLDEN CROSS
        if ema12 > ema26 and close.ewm(span=12).mean().iloc[-2] <= close.ewm(span=26).mean().iloc[-2]:
            signals.append(f"✨ GOLDEN CROSS! {symbol} EMA12 crossed above EMA26 - BULLISH")
            strength += 2
        # 4. RSI OVERSOLD = BUY
        if rsi_now < 30:
            signals.append(f"💎 OVERSOLD! {symbol} RSI {rsi_now:.1f} - STRONG BUY DIP")
            strength += 3
        # 5. RSI OVERBOUGHT
        if rsi_now > 75:
            signals.append(f"⚠️ OVERBOUGHT! {symbol} RSI {rsi_now:.1f} - Take profit")
            strength += 2
        # 6. VOLUME SURGE
        if vol.iloc[-1] > avg_vol * 2:
            direction = "UP 📈" if curr > prev else "DOWN 📉"
            signals.append(f"🔥 VOLUME SURGE! {symbol} {vol.iloc[-1]/avg_vol:.1f}x volume moving {direction}")
            strength += 2
        # 7. SMA SUPPORT BOUNCE
        if abs(curr - sma20) / curr < 0.005 and curr > sma20:
            signals.append(f"🛡️ BOUNCE! {symbol} bouncing off SMA20 support ${sma20:.2f}")
            strength += 2
        # 8. PRICE ABOVE SMA50 TREND
        if curr > sma50 and prev <= sma50:
            signals.append(f"📈 TREND START! {symbol} crossed above SMA50 ${sma50:.2f}")
            strength += 2

        if signals:
            header = f"⚡ BEAST SIGNAL [{strength}/10 POWER] {symbol} ${curr:.2f}\n"
            return header + "\n".join(signals)
        return None
    except Exception as e:
        print(f"Error {symbol}: {e}")
        return None

# Background scanner
async def auto_scanner(app_bot):
    await asyncio.sleep(10)
    while True:
        for user_id, symbols in list(user_symbols.items()):
            for sym in symbols:
                sig = get_super_signals(sym)
                if sig:
                    try:
                        await app_bot.bot.send_message(chat_id=user_id, text=sig)
                    except: pass
        await asyncio.sleep(60)  # scan every 60 sec

async def myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Use: /myalerts SPX\nOr: /myalerts SPX AAPL TSLA (up to 5)")
        return
    user_id = update.effective_user.id
    symbols = [s.upper() for s in context.args[:5]]
    user_symbols[user_id] = symbols
    await update.message.reply_text(f"🔥 BEAST LOCKED! Scanning every 60s:\n{', '.join(symbols)}\n\nYou will get:\n🚀 Breakouts\n🔻 Breakdowns\n💎 Oversold buys\n🔥 Volume surges\n✨ Golden crosses\n\nSend /scan to check NOW!")

async def scan_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    syms = user_symbols.get(user_id, ["SPX", "SPY", "QQQ"])
    await update.message.reply_text(f"🔍 Scanning {', '.join(syms)}...")
    for sym in syms:
        sig = get_super_signals(sym)
        if sig:
            await update.message.reply_text(sig)
        else:
            try:
                price = yf.Ticker(sym).history(period="1d")['Close'].iloc[-1]
                await update.message.reply_text(f"{sym}: ${price:.2f} - No strong signal yet, watching...")
            except:
                await update.message.reply_text(f"{sym}: checking...")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 BEAST V2 SUPER SIGNALS READY!\n\nCommands:\n/myalerts SPX AAPL - Start auto alerts (every 60s)\n/scan - Force scan now\n/list - Show watched symbols\n\nI give 8 STRONG signals, not weak ones!")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    syms = user_symbols.get(user_id, [])
    if not syms: await update.message.reply_text("No symbols. Use /myalerts SPX")
    else: await update.message.reply_text(f"Watching: {', '.join(syms)}")

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("myalerts", myalerts))
    application.add_handler(CommandHandler("scan", scan_now))
    application.add_handler(CommandHandler("list", list_cmd))
    
    # Start auto scanner in background
    async def post_init(app): asyncio.create_task(auto_scanner(app))
    application.post_init = post_init
    
    application.run_polling()
