import os, yfinance as yf
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from datetime import datetime

TOKEN = os.getenv("BOT_TOKEN")
print("MONSTER V7 FINAL @Sanglai - TRC20 TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t LIVE")

app = Flask(__name__)
@app.route('/')
def home(): return f"BEAST V7 FINAL @Sanglai LIVE {datetime.utcnow()}"
def run_flask(): app.run(host='0.0.0.0', port=10000)
def keep_alive(): Thread(target=run_flask).start()

SECTORS = {
    'sp500': ("^GSPC", "📈 WALL ST - S&P500"),
    'nasdaq': ("^IXIC", "💻 WALL ST - NASDAQ"),
    'dow': ("^DJI", "🏦 WALL ST - DOW JONES"),
    'vix': ("^VIX", "😱 VIX Fear Index"),
    'treasury_10y': ("^TNX", "🏛️ GOV TREASURY - 10Y Yield"),
    'treasury_tlt': ("TLT", "🏛️ TREASURY - TLT 20Y"),
    'bonds_bnd': ("BND", "📜 BONDS - Total Bond"),
    'bonds_lqd': ("LQD", "📜 BONDS - Corporate"),
    'btc': ("BTC-USD", "₿ CRYPTO - Bitcoin LIVE"),
    'eth': ("ETH-USD", "Ξ CRYPTO - Ethereum"),
    'sol': ("SOL-USD", "◎ CRYPTO - Solana"),
    'nfts': ("ETH-USD", "🖼️ NFTS - Blue Chip Floor"),
    'oil': ("CL=F", "🛢️ OIL - Crude LIVE"),
    'brent': ("BZ=F", "🛢️ OIL - Brent"),
    'gas': ("NG=F", "⛽ GAS - Natural Gas"),
    'xle': ("XLE", "⛽ OIL & GAS - XLE"),
    'corn': ("CORN", "🌽 AGRIC - Corn"),
    'wheat': ("WEAT", "🌾 AGRIC - Wheat"),
    'soy': ("SOYB", "🌱 AGRIC - Soybean"),
    'coffee_jo': ("JO", "☕ COFFEE - Coffee ETF JO"),
    'coffee_sbux': ("SBUX", "☕ COFFEE - Starbucks"),
    're_vnq': ("VNQ", "🏠 REAL ESTATE - VNQ REIT"),
    're_iyr': ("IYR", "🏢 REAL ESTATE - IYR"),
    'tesla': ("TSLA", "🚗 TESLA - Tesla LIVE"),
    'ford': ("F", "🚙 AUTOMOTIVE - Ford"),
    'toyota': ("TM", "🚗 AUTOMOTIVE - Toyota"),
    'ict_aapl': ("AAPL", "📱 ICT - Apple"),
    'ict_msft': ("MSFT", "💻 ICT - Microsoft"),
    'ict_nvda': ("NVDA", "🤖 ICT + AI - Nvidia"),
    'ict_googl': ("GOOGL", "🌐 ICT - Google"),
    'ai_botz': ("BOTZ", "🤖 AI - BOTZ ETF"),
    'ai_pltr': ("PLTR", "🤖 AI - Palantir"),
    'gold': ("GC=F", "🥇 GOLD LIVE"),
    'silver': ("SI=F", "🥈 SILVER"),
}

PAYMENT_TEXT = """
💳 PAYMENT DETAILS - BEAST EMPIRE V7

MTN Mobile Money (Primary):
Number: 0792878628
Name: BEAST TRADING
Reason: BEAST $35/$75/$150/$250

Airtel Money / Second Number:
Number: 0756484267
Name: BEAST TRADING

USDT TRC20 (TRON):
Address: TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t
Network: TRC20 - TRON
Copy: TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t

Contact to confirm payment:
Telegram: @Sanglai
WhatsApp: +256 792878628 / +256 756484267

After payment, send screenshot + package name (STARTER/PRO/BEAST/MONSTER)
Instant activation!
"""

def get_live_signal(key):
    symbol, name = SECTORS.get(key, ("BTC-USD", "BTC"))
    try:
        df = yf.download(symbol, period="1d", interval="1m", progress=False)
        if df.empty or len(df) < 30:
            df = yf.download(symbol, period="5d", interval="15m", progress=False)
        close = df['Close']
        last = float(close.iloc[-1])
        first = float(close.iloc[0])
        change = ((last-first)/first)*100 if first!=0 else 0
        ema9 = float(close.ewm(span=9).mean().iloc[-1])
        ema21 = float(close.ewm(span=21).mean().iloc[-1])
        ema50 = float(close.ewm(span=50).mean().iloc[-1])
        delta = close.diff()
        gain = delta.where(delta>0,0).rolling(14).mean()
        loss = -delta.where(delta<0,0).rolling(14).mean()
        rsi = 100 - (100/(1+gain/loss))
        rsi_last = float(rsi.iloc[-1]) if not rsi.empty else 50
        vol = float(df['Volume'].iloc[-1]) if 'Volume' in df and not df.empty else 0
        if ema9>ema21>ema50 and 50<rsi_last<70: sig="🟢 STRONG BUY - BULLISH"; act="BUY NOW"
        elif ema9<ema21 and rsi_last<50: sig="🔴 STRONG SELL - BEARISH"; act="SELL NOW"
        elif rsi_last>70: sig="🟠 OVERBOUGHT - TAKE PROFIT"; act="SELL"
        elif rsi_last<30: sig="🟢 OVERSOLD - BUY DIP"; act="BUY"
        else: sig="🟡 NEUTRAL - WAIT"; act="HOLD"
        try:
            t = yf.Ticker(symbol)
            n = t.news[0]['title'][:70]+"..." if t.news else "Live market"
        except:
            n="Live: Wall Street + Investing.com style"
        return (
            f"🔥 MONSTER V7 FINAL @Sanglai 🔥\n"
            f"⏰ {datetime.utcnow().strftime('%H:%M UTC')} LIVE 1m\n\n"
            f"{name}\n"
            f"Symbol: {symbol}\n"
            f"💰 Price: ${last:.2f} ({change:+.2f}% Today)\n"
            f"EMA9: {ema9:.2f} | EMA21: {ema21:.2f} | EMA50: {ema50:.2f}\n"
            f"RSI(14): {rsi_last:.1f} + EMA\n"
            f"Vol: {vol:,.0f}\n\n"
            f"🎯 {sig}\n"
            f"👉 Action: {act}\n\n"
            f"📰 {n}\n"
            f"Source: Wall Street Live + Investing.com (1m)\n"
            f"🐉 BEAST AI - @Sanglai"
        )
    except Exception as e:
        return f"⚠️ {name} busy, retry.\n{e}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📈 S&P500", callback_data='sp500'), InlineKeyboardButton("💻 NASDAQ", callback_data='nasdaq'), InlineKeyboardButton("🏦 DOW", callback_data='dow')],
        [InlineKeyboardButton("🏛️ Treasury 10Y", callback_data='treasury_10y'), InlineKeyboardButton("🏛️ TLT", callback_data='treasury_tlt'), InlineKeyboardButton("📜 Bonds BND", callback_data='bonds_bnd')],
        [InlineKeyboardButton("₿ BTC", callback_data='btc'), InlineKeyboardButton("Ξ ETH", callback_data='eth'), InlineKeyboardButton("🖼️ NFTS", callback_data='nfts')],
        [InlineKeyboardButton("🛢️ Crude", callback_data='oil'), InlineKeyboardButton("⛽ Gas", callback_data='gas'), InlineKeyboardButton("XLE Energy", callback_data='xle')],
        [InlineKeyboardButton("🌽 Corn", callback_data='corn'), InlineKeyboardButton("☕ Coffee JO", callback_data='coffee_jo'), InlineKeyboardButton("🌾 Wheat", callback_data='wheat')],
        [InlineKeyboardButton("🏠 REIT VNQ", callback_data='re_vnq'), InlineKeyboardButton("🚗 Tesla", callback_data='tesla'), InlineKeyboardButton("📱 ICT Apple", callback_data='ict_aapl')],
        [InlineKeyboardButton("💻 ICT MSFT", callback_data='ict_msft'), InlineKeyboardButton("🤖 AI Nvidia", callback_data='ict_nvda'), InlineKeyboardButton("🤖 AI BOTZ", callback_data='ai_botz')],
        [InlineKeyboardButton("🥇 Gold", callback_data='gold'), InlineKeyboardButton("☕ SBUX", callback_data='coffee_sbux'), InlineKeyboardButton("🌐 ICT Google", callback_data='ict_googl')],
        [InlineKeyboardButton("💎 $35 STARTER", callback_data='starter'), InlineKeyboardButton("🚀 $75 PRO", callback_data='pro')],
        [InlineKeyboardButton("👑 $150 BEAST", callback_data='beast'), InlineKeyboardButton("🐉 $250 MONSTER VIP", callback_data='monster')],
        [InlineKeyboardButton("💳 Pay 0792878628 / TRC20 - @Sanglai", callback_data='pay')],
    ]
    await update.message.reply_text(
        "🔥 BEAST V7 FINAL EMPIRE @Sanglai - EXTRAORDINARY 🔥\n\n"
        "NFTS, GOV TREASURY, BONDS, AGRIC, REAL ESTATE\n"
        "WALL ST LIVE 1m, INVESTING.COM style\n"
        "AUTOMOTIVE, ICT, COFFEE, OIL & GAS\n"
        "GOLD, CRYPTO, RSI+EMA, AI, TESLA\n\n"
        "28 LIVE Markets - Tap for LIVE signal:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data in SECTORS:
        await q.edit_message_text("⏳ Fetching LIVE 1m Wall Street data...")
        txt = get_live_signal(q.data)
        await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data='menu'), InlineKeyboardButton("🔄 Refresh", callback_data=q.data)]]))
    elif q.data == 'menu':
        await start(q, context)
    elif q.data == 'pay':
        await q.edit_message_text(PAYMENT_TEXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data='menu')]]))
    elif q.data == 'starter':
        await q.edit_message_text(f"💎 $35 STARTER\n3 signals/day All sectors\n\n{PAYMENT_TEXT}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 Pay 0792878628", callback_data='pay'), InlineKeyboardButton("🔙 Menu", callback_data='menu')]]))
    elif q.data == 'pro':
        await q.edit_message_text(f"🚀 $75 PRO\n5-7 signals All sectors\n\n{PAYMENT_TEXT}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 Pay Now", callback_data='pay'), InlineKeyboardButton("🔙 Menu", callback_data='menu')]]))
    elif q.data == 'beast':
        await q.edit_message_text(f"👑 $150 BEAST\n10+ signals + VIP\n\n{PAYMENT_TEXT}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 Pay Now", callback_data='pay'), InlineKeyboardButton("🔙 Menu", callback_data='menu')]]))
    elif q.data == 'monster':
        await q.edit_message_text(f"🐉 $250 MONSTER VIP - FULL EMPIRE\nEVERYTHING + Mentorship @Sanglai\n\n{PAYMENT_TEXT}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 Pay TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", callback_data='pay'), InlineKeyboardButton("🔙 Menu", callback_data='menu')]]))

def main():
    keep_alive()
    print("BEAST V7 FINAL @Sanglai - TRC20 LIVE")
    app_ = Application.builder().token(TOKEN).build()
    app_.add_handler(CommandHandler("start", start))
    app_.add_handler(CallbackQueryHandler(button))
    app_.run_polling()

if __name__ == "__main__":
    main()
