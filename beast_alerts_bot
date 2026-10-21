# beast_alerts.py - SAFE SEPARATE FILE
import asyncio
import yfinance as yf
from telegram.ext import Application, CommandHandler

BOT_TOKEN = 8232597954:AAGwlRKNwmyQuQ88SpOsLbv1VIx6XuVsmZ8"

user_alerts = {}
yahoo_map = {"SPX":"^GSPC","DJI":"^DJI","VIX":"^VIX","AAPL":"AAPL","TSLA":"TSLA","NVDA":"NVDA","GOLD":"GC=F","BTC":"BTC-USD","EURUSD":"EURUSD=X"}

async def myalerts(update, context):
    user_id = update.effective_user.id
    if len(context.args) < 1:
        await update.message.reply_text("🔔 Use: /myalerts SPX 1\n/myalerts list")
        return
    if context.args[0].lower() == "list":
        alerts = user_alerts.get(user_id, {})
        txt = "No alerts" if not alerts else "\n".join([f"{k}: {v['pct']}%" for k,v in alerts.items()])
        await update.message.reply_text(txt)
        return
    try:
        symbol = context.args[0].upper()
        pct = float(context.args[1])
        y_symbol = yahoo_map.get(symbol, symbol)
        price = yf.Ticker(y_symbol).history(period="1d")['Close'].iloc[-1]
        if user_id not in user_alerts: user_alerts[user_id] = {}
        user_alerts[user_id][symbol] = {"pct": pct, "last_price": price, "y_symbol": y_symbol}
        await update.message.reply_text(f"🔔 LOCKED! {symbol} at ${price:.2f} - {pct}% alert!")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("myalerts", myalerts))
    app.add_handler(CommandHandler("start", myalerts))
    print("BEAST ALERTS RUNNING!")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
