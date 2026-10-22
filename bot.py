import os, yfinance as yf, asyncio
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from datetime import datetime

TOKEN = os.getenv("BOT_TOKEN")
TRC20_WALLET = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

print(f"BEAST V7.2 ATR-SIGNALS @Sanglai TRC20 {TRC20_WALLET}")

app = Flask(__name__)
@app.route('/')
def home():
    return f"BEAST V7.2 ATR-SIGNALS - REAL RSI+EMA+ATR - 34 MARKETS {datetime.now()}"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    Thread(target=run_flask, daemon=True).start()

SECTORS = {
    'sp500': ("^GSPC", "📈 WALL ST - S&P500"), 'nasdaq': ("^IXIC", "📈 WALL ST - NASDAQ"), 'dow': ("^DJI", "🏦 WALL ST - DOW JONES"),
    'vix': ("^VIX", "😱 VIX Fear Index"), 'treasury_10y': ("^TNX", "🏛️ TREASURY - 10Y"), 'treasury_tlt': ("TLT", "🏛️ TREASURY - TLT"),
    'bonds_bnd': ("BND", "🏦 BONDS - Total"), 'bonds_lqd': ("LQD", "🏦 BONDS - Corporate"), 'btc': ("BTC-USD", "₿ CRYPTO - Bitcoin"),
    'eth': ("ETH-USD", "Ξ CRYPTO - Ethereum"), 'sol': ("SOL-USD", "◎ CRYPTO - Solana"), 'nfts': ("ETH-USD", "🖼️ NFTS - Floor"),
    'oil': ("CL=F", "🛢️ OIL - Crude"), 'brent': ("BZ=F", "🛢️ OIL - Brent"), 'gas': ("NG=F", "⛽ GAS - NatGas"), 'xle': ("XLE", "🛢️ OIL & GAS - XLE"),
    'corn': ("CORN", "🌽 AGRIC - Corn"), 'wheat': ("WEAT", "🌾 AGRIC - Wheat"), 'soy': ("SOYB", "🌱 AGRIC - Soybean"),
    'coffee_jo': ("JO", "☕ COFFEE - JO"), 'coffee_sbux': ("SBUX", "☕ COFFEE - Starbucks"),
    're_vnq': ("VNQ", "🏠 REAL ESTATE - VNQ"), 're_iyr': ("IYR", "🏠 REAL ESTATE - IYR"),
    'tesla': ("TSLA", "🚗 TESLA"), 'ford': ("F", "🚗 AUTOMOTIVE - Ford"), 'toyota': ("TM", "🚗 AUTOMOTIVE - Toyota"),
    'ict_aapl': ("AAPL", "📱 ICT - Apple"), 'ict_msft': ("MSFT", "💻 ICT - Microsoft"), 'ict_nvda': ("NVDA", "🤖 ICT + AI - Nvidia"),
    'ict_googl': ("GOOGL", "🌐 ICT - Google"), 'ai_botz': ("BOTZ", "🤖 AI - BOTZ"), 'ai_pltr': ("PLTR", "🤖 AI - Palantir"),
    'gold': ("GC=F", "🥇 GOLD"), 'silver': ("SI=F", "🥈 SILVER"),
}

PAYMENT_TEXT = f"""
💳 PAYMENT - BEAST V7.2 @Sanglai
📱 MTN: 0792878628 (Alex Kamoga)
📱 Airtel: 0756484267 (Alex Kamoga)
🏦 TRC20 USDT: `{TRC20_WALLET}` TRC20 ONLY
PLANS:
🟢 $35 STARTER (3 signals/day)
🔵 $75 PRO (5-7 + Live 1m)
👑 $150 BEAST (10+ + AI + RSI+EMA+ATR)
💀 $250 MONSTER VIP (Unlimited + 1on1)
Send screenshot to @Sanglai after pay
"""

# === REAL RSI (Wilder) + EMA (adjust=False) + ATR-BASED ENTRY/STOP/TARGET ===

def calc_rsi_wilder(close, period=14):
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    last_gain = avg_gain.iloc[-1]
    last_loss = avg_loss.iloc[-1]
    if last_gain == 0 and last_loss == 0:
        return None  # flat price window - RSI undefined, don't fake 50
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return float(100 - (100 / (1 + rs)))

def calc_atr(data, period=14):
    """Average True Range - measures actual volatility, used to size stop/target
    so they scale with how much this specific ticker normally moves."""
    if len(data) < period + 1:
        return None
    high, low, close = data['High'], data['Low'], data['Close']
    prev_close = close.shift(1)
    tr = pd := None
    import pandas as pd
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    val = atr.iloc[-1]
    return float(val) if val == val else None  # NaN check

def get_live_signal(ticker_symbol):
    try:
        tk = yf.Ticker(ticker_symbol)
        data = tk.history(period="1d", interval="1m")
        if data.empty or len(data) < 25:
            data = tk.history(period="5d", interval="5m")
        if data.empty:
            return f"⚠️ Market closed {ticker_symbol} - BEAST V7.2"

        close = data['Close']
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) > 1 else last
        change = last - prev
        pct = (change / prev * 100) if prev else 0

        rsi_val = calc_rsi_wilder(close, period=14)
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1]) if len(close) >= 20 else None
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(close) >= 50 else None
        atr_val = calc_atr(data, period=14)

        if rsi_val is None or ema20 is None:
            trend = "⚪ INSUFFICIENT DATA FOR SIGNAL"
            rsi_tag, ema_tag, rsi_state = "n/a", "n/a", "n/a"
        else:
            rsi_tag = f"{rsi_val:.1f}"
            ema_tag = f"${ema20:.4f}"
            rsi_state = "Overbought >70" if rsi_val > 70 else "Oversold <30" if rsi_val < 30 else "Healthy"
            if last > ema20 and rsi_val > 55:
                trend = "🟢 BULLISH BUY - Above EMA20, RSI strong"
            elif last < ema20 and rsi_val < 45:
                trend = "🔴 BEARISH SELL - Below EMA20, RSI weak"
            else:
                trend = "⚪ NEUTRAL - Wait for breakout"

        # --- ATR-based entry/stop/target (only when we can actually compute ATR) ---
        if atr_val is not None and atr_val > 0 and "BULLISH" in trend:
            entry = last
            stop = last - (1.5 * atr_val)
            target = last + (3.0 * atr_val)  # 2:1 reward:risk
            level_block = (f"🎯 Entry: ${entry:.4f}  Stop: ${stop:.4f}  Target: ${target:.4f}\n"
                            f"   (stop = 1.5x ATR, target = 3x ATR -> 2:1 reward:risk)")
        elif atr_val is not None and atr_val > 0 and "BEARISH" in trend:
            entry = last
            stop = last + (1.5 * atr_val)
            target = last - (3.0 * atr_val)
            level_block = (f"🎯 Entry: ${entry:.4f}  Stop: ${stop:.4f}  Target: ${target:.4f}\n"
                            f"   (stop = 1.5x ATR, target = 3x ATR -> 2:1 reward:risk)")
        else:
            level_block = "🎯 No entry/stop/target - signal is NEUTRAL or data insufficient."

        atr_tag = f"${atr_val:.4f}" if atr_val is not None else "n/a"

        return f"""
🔥 BEAST V7.2 LIVE 1m - REAL INDICATORS 🔥
Ticker: {ticker_symbol}
Time: {datetime.now().strftime('%H:%M:%S')} UTC
💲 Price: ${last:.4f}
📈 Change: {pct:+.2f}% ({change:+.4f})
📊 RSI(14): {rsi_tag} ({rsi_state}) | EMA20: {ema_tag}
📊 ATR(14): {atr_tag} (volatility basis for stop/target below)
🎯 Signal: {trend}
{level_block}

⚠️ Rule-based heuristic (RSI + EMA20 + ATR sizing), not a backtested
strategy. Stop/target scale with this ticker's own volatility, but are
not guaranteed outcomes. Not financial advice.

⚡ BEAST V7.2 @Sanglai - REAL CALC
"""
    except Exception as e:
        return f"⚠️ Error {ticker_symbol}: {e}"

async def live_updater(context: ContextTypes.DEFAULT_TYPE, ticker, name, chat_id, message_id):
    for i in range(1, 11):
        await asyncio.sleep(60)
        txt = await asyncio.to_thread(get_live_signal, ticker)
        try:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🔴 LIVE {i}/10 STOP", callback_data='menu')], [InlineKeyboardButton("💰 PAY VIP", callback_data='pay')]])
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"{name}\n{txt}\n🔴 AUTO {i}/10", reply_markup=kb)
        except:
            break

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb, row = [], []
    for key, (ticker, name) in SECTORS.items():
        row.append(InlineKeyboardButton(name, callback_data=key))
        if len(row) == 2: kb.append(row); row = []
    if row: kb.append(row)
    kb.append([InlineKeyboardButton("💰 PAY - MTN/Airtel/TRC20", callback_data='pay')])
    kb.append([InlineKeyboardButton("$35 STARTER", callback_data='starter'), InlineKeyboardButton("$75 PRO", callback_data='pro')])
    kb.append([InlineKeyboardButton("$150 BEAST", callback_data='beast'), InlineKeyboardButton("$250 MONSTER", callback_data='monster')])
    await update.message.reply_text("🔥 BEAST V7.2 FINAL @Sanglai - REAL RSI+EMA+ATR\n34 MARKETS AUTO-LIVE 60s - Tap for LIVE:", reply_markup=InlineKeyboardMarkup(kb))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data in SECTORS:
        await q.edit_message_text("⏳ BEAST V7.2 Fetching REAL LIVE data...")
        ticker, name = SECTORS[data]
        txt = await asyncio.to_thread(get_live_signal, ticker)
        back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔴 LIVE AUTO - STOP", callback_data='menu')], [InlineKeyboardButton("💰 GET VIP", callback_data='pay')]])
        msg = await q.edit_message_text(f"{name}\n{txt}\n🔴 AUTO-LIVE STARTED - 60s updates", reply_markup=back_kb)
        context.application.create_task(live_updater(context, ticker, name, q.message.chat_id, msg.message_id))
    elif data == 'menu':
        kb, row = [], []
        for key, (ticker, name) in SECTORS.items():
            row.append(InlineKeyboardButton(name, callback_data=key))
            if len(row) == 2: kb.append(row); row = []
        if row: kb.append(row)
        kb.append([InlineKeyboardButton("💰 PAY", callback_data='pay')])
        await q.edit_message_text("🔥 BEAST V7.2 - 34 MARKETS - Tap:", reply_markup=InlineKeyboardMarkup(kb))
    elif data == 'pay':
        await q.edit_message_text(PAYMENT_TEXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 MENU", callback_data='menu')]]), parse_mode='Markdown')
    else:
        await q.edit_message_text(f"{data.upper()} PLAN\n{PAYMENT_TEXT}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 MENU", callback_data='menu')]]), parse_mode='Markdown')

def main():
    keep_alive()
    if not TOKEN: print("ERROR BOT_TOKEN not set"); return
    app_ = Application.builder().token(TOKEN).build()
    app_.add_handler(CommandHandler("start", start))
    app_.add_handler(CallbackQueryHandler(button))
    print("BEAST V7.2 ATR-SIGNALS LIVE polling...")
    app_.run_polling()

if __name__ == "__main__":
    main()
