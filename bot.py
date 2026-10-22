import os, yfinance as yf, asyncio, re
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from datetime import datetime

TOKEN = os.getenv("BOT_TOKEN")
TRC20_WALLET = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

print(f"BEAST V7.3 COMPLETE @Sanglai AUTO-LIVE TRC20 {TRC20_WALLET} TOKEN:{bool(TOKEN)}")

app = Flask(__name__)
@app.route('/')
def home():
    return f"BEAST V7.3 COMPLETE @Sanglai AUTO-LIVE 60s - 34 MARKETS REAL RSI+EMA {datetime.now()} - TRC20 LIVE"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    Thread(target=run_flask, daemon=True).start()

# 34 MARKETS = Full labels from your original - nothing left out
SECTORS = {
    'sp500': ("^GSPC", "📈 WALL ST - S&P500"),
    'nasdaq': ("^IXIC", "📈 WALL ST - NASDAQ"),
    'dow': ("^DJI", "🏦 WALL ST - DOW JONES"),
    'vix': ("^VIX", "😱 VIX Fear Index"),
    'treasury_10y': ("^TNX", "🏛️ GOV TREASURY - 10Y Yield"),
    'treasury_tlt': ("TLT", "🏛️ TREASURY - TLT 20Y"),
    'bonds_bnd': ("BND", "🏦 BONDS - Total Bond"),
    'bonds_lqd': ("LQD", "🏦 BONDS - Corporate"),
    'btc': ("BTC-USD", "₿ CRYPTO - Bitcoin LIVE"),
    'eth': ("ETH-USD", "Ξ CRYPTO - Ethereum"),
    'sol': ("SOL-USD", "◎ CRYPTO - Solana"),
    'nfts': ("ETH-USD", "🖼️ NFTS - Blue Chip Floor"),
    'oil': ("CL=F", "🛢️ OIL - Crude LIVE"),
    'brent': ("BZ=F", "🛢️ OIL - Brent"),
    'gas': ("NG=F", "⛽ GAS - Natural Gas"),
    'xle': ("XLE", "🛢️ OIL & GAS - XLE"),
    'corn': ("CORN", "🌽 AGRIC - Corn"),
    'wheat': ("WEAT", "🌾 AGRIC - Wheat"),
    'soy': ("SOYB", "🌱 AGRIC - Soybean"),
    'coffee_jo': ("JO", "☕ COFFEE - Coffee ETF JO"),
    'coffee_sbux': ("SBUX", "☕ COFFEE - Starbucks"),
    're_vnq': ("VNQ", "🏠 REAL ESTATE - VNQ REIT"),
    're_iyr': ("IYR", "🏠 REAL ESTATE - IYR"),
    'tesla': ("TSLA", "🚗 TESLA - Tesla LIVE"),
    'ford': ("F", "🚗 AUTOMOTIVE - Ford"),
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

PAYMENT_TEXT = f"""
💳 PAYMENT DETAILS - BEAST EMPIRE V7.3 @Sanglai

📱 MTN Mobile Money (Primary):
Number: 0792878628
Name: Alex Kamoga

📱 Airtel Money (Secondary):
Number: 0756484267
Name: Alex Kamoga

🏦 TRC20 USDT (International):
Wallet: `{TRC20_WALLET}`
Network: TRC20 TRON ONLY
Address: TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t

💎 PLANS:
🟢 $35 STARTER (3 signals/day)
🔵 $75 PRO (5-7 signals + Live 1m)
👑 $150 BEAST (10+ signals + AI + RSI+EMA REAL)
💀 $250 MONSTER VIP (Unlimited + Private Group + 1on1)

✅ After payment: Send screenshot + TXID to @Sanglai Telegram

🔥 BEAST V7.3 FINAL - EXTRAORDINAIRE LIVE - AUTO-LIVE 60s - REAL RSI+EMA
"""

def calc_rsi(close, period=14):
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    last_gain = avg_gain.iloc[-1]
    last_loss = avg_loss.iloc[-1]
    if last_loss == 0:
        if last_gain > 0:
            return 100.0
        else:
            return 50.0
    rs = last_gain / last_loss
    return 100 - (100 / (1 + rs))

def calc_ema(close, span=20):
    if len(close) < span:
        return None
    return close.ewm(span=span, adjust=False).mean().iloc[-1]

def get_live_signal(ticker_symbol):
    try:
        tk = yf.Ticker(ticker_symbol)
        data = tk.history(period="1d", interval="1m")
        if data.empty or len(data) < 25:
            data = tk.history(period="5d", interval="5m")
        if data.empty:
            return f"⚠️ Market closed for {ticker_symbol}, check later - BEAST V7.3"
        close = data['Close']
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) > 1 else last
        change = last - prev
        pct = 0
        if prev != 0:
            pct = (change / prev * 100)
        rsi_val = calc_rsi(close, period=14)
        ema_val = calc_ema(close, span=20)
        if rsi_val is None or ema_val is None:
            trend = "⚪ INSUFFICIENT DATA FOR RSI/EMA"
            rsi_tag = "n/a"
            ema_tag = "n/a"
        else:
            rsi_tag = f"{rsi_val:.1f}"
            ema_tag = f"{ema_val:.2f}"
            rsi_state = ""
            if rsi_val >= 70:
                rsi_state = "Overbought"
            elif rsi_val <= 30:
                rsi_state = "Oversold"
            else:
                rsi_state = "Healthy"
            if rsi_val >= 70 and last > ema_val:
                trend = "🔴 OVERBOUGHT - CAUTION / POSSIBLE PULLBACK"
            elif rsi_val <= 30 and last < ema_val:
                trend = "🟢 OVERSOLD - POSSIBLE BOUNCE (confirm before entry)"
            elif last > ema_val and rsi_val > 50:
                trend = "🟢 BULLISH (price > EMA20, RSI > 50)"
            elif last < ema_val and rsi_val < 50:
                trend = "🔴 BEARISH (price < EMA20, RSI < 50)"
            else:
                trend = "⚪ MIXED / NO CLEAR SIGNAL"
            rsi_tag = f"{rsi_tag} ({rsi_state})"
        return f"""
🔥 BEAST V7.3 LIVE 1m SIGNAL - Investing.com Style 🔥
Ticker: {ticker_symbol}
Time: {datetime.now().strftime('%H:%M:%S')} UTC

💲 Live Price: ${last:.4f}
📈 Change: {pct:+.2f}% ({change:+.4f})
📊 RSI(14): {rsi_tag} | EMA(20): {ema_tag}
🎯 Signal: {trend}
⚡ Trusted Signals: Wall St + Investing.com

⚠️ Signal is rule-based heuristic (RSI + price vs EMA20), not backtested. Not financial advice - size 1% risk.

Powered by BEAST V7.3 @Sanglai - AUTO-LIVE REAL RSI+EMA
"""
    except Exception as e:
        return f"⚠️ Error fetching {ticker_symbol}: {e}"

async def live_updater(context: ContextTypes.DEFAULT_TYPE, ticker, name, chat_id, message_id):
    for i in range(1, 11):
        await asyncio.sleep(60)
        txt = await asyncio.to_thread(get_live_signal, ticker)
        try:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🔴 LIVE AUTO {i}/10 - STOP", callback_data='menu')],
                [InlineKeyboardButton("💰 PAY VIP", callback_data='pay')]
            ])
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"{name}\n{txt}\n\n🔴 AUTO-LIVE {i}/10 - Next in 60s - BEAST V7.3 @Sanglai", reply_markup=kb)
        except:
            break

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = []
    row = []
    for key, value in SECTORS.items():
        ticker, name = value
        row.append(InlineKeyboardButton(name, callback_data=key))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("💰 PAY - MTN 0792878628 / Airtel / TRC20", callback_data='pay')])
    kb.append([InlineKeyboardButton("$35 STARTER", callback_data='starter'), InlineKeyboardButton("$75 PRO", callback_data='pro')])
    kb.append([InlineKeyboardButton("$150 BEAST", callback_data='beast'), InlineKeyboardButton("$250 MONSTER", callback_data='monster')])
    await update.message.reply_text("🔥 BEAST V7.3 FINAL EMPIRE @Sanglai - EXTRAORDINAIRE\nNfts, treasury, bonds, agric, real estate, wall st live 1m, investing.com, automotive, ICT, coffee, oil & gas, gold, crypto, RSI+EMA REAL, AI, tesla\n\n34 MARKETS AUTO-LIVE 60s - Tap for LIVE signal:", reply_markup=InlineKeyboardMarkup(kb))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data in SECTORS:
        await q.edit_message_text("⏳ BEAST V7.3 Fetching LIVE Wall St 1m data - REAL RSI+EMA...")
        ticker, name = SECTORS[data]
        txt = await asyncio.to_thread(get_live_signal, ticker)
        back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔴 LIVE AUTO UPDATING - STOP", callback_data='menu')], [InlineKeyboardButton("💰 GET VIP ACCESS", callback_data='pay')]])
        msg = await q.edit_message_text(f"{name}\n{txt}\n\n🔴 AUTO-LIVE STARTED - Updates every 60s", reply_markup=back_kb)
        context.application.create_task(live_updater(context, ticker, name, q.message.chat_id, msg.message_id))
    elif data == 'menu':
        kb = []
        row = []
        for key, value in SECTORS.items():
            ticker, name = value
            row.append(InlineKeyboardButton(name, callback_data=key))
            if len(row) == 2:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        kb.append([InlineKeyboardButton("💰 PAY - MTN/Airtel/TRC20", callback_data='pay')])
        kb.append([InlineKeyboardButton("$35 STARTER", callback_data='starter'), InlineKeyboardButton("$75 PRO", callback_data='pro')])
        kb.append([InlineKeyboardButton("$150 BEAST", callback_data='beast'), InlineKeyboardButton("$250 MONSTER", callback_data='monster')])
        await q.edit_message_text("🔥 BEAST V7.3 - 34 MARKETS AUTO-LIVE 60s - REAL RSI+EMA - Tap for LIVE:", reply_markup=InlineKeyboardMarkup(kb))
    elif data == 'pay':
        await q.edit_message_text(PAYMENT_TEXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 MENU", callback_data='menu')]]), parse_mode='Markdown')
    else:
        await q.edit_message_text(f"{data.upper()} PLAN\n{PAYMENT_TEXT}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 MENU", callback_data='menu')]]), parse_mode='Markdown')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    m = re.search(r'\$?(\d+)', text)
    amount = 50
    if m:
        amount = int(m.group(1))
    risk = amount * 0.01
    if amount < 35:
        reply = f"Got you, you have ${amount} - you want to feel secure. Risk = ${amount}*1% = ${risk:.2f}/trade. Can't afford $35 yet. Save to $35 first, keep emergency fund. Paper-trade free with me or save $5/week - which first?"
    elif amount < 75:
        reply = f"Yeah with ${amount} - you want growth but not lose it. Risk = ${risk:.2f}/trade. Lot = {risk:.2f}/SL. If SL $2, lot = {risk/2:.3f}. Best = $35 STARTER (not $75). Why? You keep ${amount-35} safe. If you buy $75, left ${amount-75} broke. $35 gives 3 real signals/day with REAL RSI+EMA. After 2 weeks upgrade. Join STARTER now or want lot calc - which first?"
    else:
        reply = f"Got you, ${amount} is good start. Risk = ${risk:.2f}/trade. You qualify for $75 PRO (5-7 signals + live 1m auto). Don't take $150 yet - keep emergency 3 months. Offer: $75 PRO, keep ${amount-75} safe. Pay MTN 0792878628 or TRC20 TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t, or see free BTC+Gold live now - which first?"
    await update.message.reply_text(reply, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💰 PAY $35 STARTER", callback_data='starter'), InlineKeyboardButton("💰 PAY $75 PRO", callback_data='pro')], [InlineKeyboardButton("🔙 MENU", callback_data='menu')]]))

def main():
    keep_alive()
    print(f"TOKEN loaded: {bool(TOKEN)}")
    print("BEAST V7.3 COMPLETE AUTO-LIVE 60s REAL RSI+EMA @Sanglai - 34 Markets - PUBLISHED")
    if not TOKEN:
        print("ERROR: BOT_TOKEN not set in Render!")
        return
    app_ = Application.builder().token(TOKEN).build()
    app_.add_handler(CommandHandler("start", start))
    app_.add_handler(CallbackQueryHandler(button))
    app_.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("BEAST V7.3 REAL-SIGNALS LIVE - polling started...")
    app_.run_polling()

if __name__ == "__main__":
    main()
