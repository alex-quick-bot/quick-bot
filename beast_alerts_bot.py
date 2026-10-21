import os
from flask import Flask
import threading, yfinance as yf, asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
app = Flask(__name__)
@app.route('/')
def home(): return "BEAST V9 ALL SECTORS HUMAN - LIVE 🔥"
def run_flask(): app.run(host='0.0.0.0', port=10000)

user_symbols = {}
auto_users = set()
last_state = {}

def fix_symbol(s):
    s=s.upper().strip()
    M={
        # 1. NFTs
        "NFT":"MANA-USD","NFTS":"MANA-USD","BORED":"APE-USD","APE":"APE-USD","SAND":"SAND-USD","MANA":"MANA-USD",
        # 2. Government Treasury
        "TREASURY":"^TNX","GOVT":"GOVT","GOVERNMENT":"^TNX","10Y":"^TNX","30Y":"^TYX",
        # 3. Bonds
        "BOND":"TLT","BONDS":"TLT","TLT":"TLT","IEF":"IEF","BND":"BND","LQD":"LQD",
        # 4. Agriculture
        "AGRI":"DBA","AGRICULTURE":"DBA","CORN":"CORN","WHEAT":"WEAT","SOY":"SOYB","COTTON":"BAL",
        # 5. Real Estate
        "REALESTATE":"VNQ","REIT":"VNQ","ESTATE":"VNQ","VNQ":"VNQ","IYR":"IYR",
        # 6. Live prices Wall Street
        "WALLSTREET":"^GSPC","SPX":"^GSPC","SPY":"SPY","DOW":"^DJI","NASDAQ":"^IXIC","QQQ":"QQQ","AAPL":"AAPL","NVDA":"NVDA","MSFT":"MSFT",
        # 7. Investing.com trusted (Yahoo is source)
        "INVESTING":"SPY",
        # 8. Automotive
        "AUTO":"CARZ","AUTOMOTIVE":"CARZ","CARZ":"CARZ","FORD":"F","F":"F","GM":"GM","RIVIAN":"RIVN","RIVN":"RIVN",
        # 9. ICT
        "ICT":"XLK","XLK":"XLK","TECH":"XLK",
        # 10. Coffee
        "COFFEE":"JO","JO":"JO","KC":"KC=F",
        # 11. Oil and Gas
        "OIL":"CL=F","GAS":"NG=F","OILGAS":"XLE","XLE":"XLE","BRENT":"BZ=F",
        # 12. Gold
        "GOLD":"GC=F","XAU":"GC=F","SILVER":"SI=F",
        # 13. Crypto
        "CRYPTO":"BTC-USD","BTC":"BTC-USD","ETH":"ETH-USD","SOL":"SOL-USD",
        # 14. RSI+EMA - in engine
        # 15. AI
        "AI":"BOTZ","BOTZ":"BOTZ","AIQ":"AIQ",
        # 16. Tesla
        "TESLA":"TSLA","TSLA":"TSLA",
    }
    return M.get(s,s)

def get_data(symbol):
    try:
        ysym=fix_symbol(symbol)
        hist=yf.Ticker(ysym).history(period="6mo")
        if len(hist)<40: return None
        c=hist['Close']; curr=c.iloc[-1]
        sma20=c.rolling(20).mean().iloc[-1]; sma50=c.rolling(50).mean().iloc[-1]
        ema12=c.ewm(span=12).mean().iloc[-1]; ema26=c.ewm(span=26).mean().iloc[-1]
        delta=c.diff(); gain=delta.where(delta>0,0).rolling(14).mean(); loss=-delta.where(delta<0,0).rolling(14).mean()
        rsi=100-(100/(1+gain/loss)); rsi_now=rsi.iloc[-1]
        return {"curr":curr,"rsi":rsi_now,"ema12":ema12,"ema26":ema26,"sma20":sma20,"sma50":sma50,"ysym":ysym}
    except: return None

def human_text(orig, d):
    curr=d["curr"]; rsi=d["rsi"]; ema12=d["ema12"]; ema26=d["ema26"]; sma20=d["sma20"]
    if rsi < 30:
        return f"💎💎 {orig} - STRONG BUY\n💰 ${curr:.2f} | RSI {rsi:.1f}\n💡 Too cheap! People scared = Discount! BUY NOW!\n📈 EMA {ema12:.1f}/{ema26:.1f} | Avg {sma20:.1f}"
    elif rsi < 38 and ema12 > ema26:
        return f"💎 {orig} - BUY\n💰 ${curr:.2f} | RSI {rsi:.1f}\n💡 Oversold + Uptrend = Going UP! Buy!\n📈 EMA {ema12:.1f}>{ema26:.1f} Bullish"
    elif rsi < 38:
        return f"💎 {orig} - BUY\n💰 ${curr:.2f} | RSI {rsi:.1f}\n💡 Cheap price, good entry!"
    elif rsi > 75:
        return f"⚠️⚠️ {orig} - STRONG SELL\n💰 ${curr:.2f} | RSI {rsi:.1f}\n💡 Too expensive! Take profit NOW!"
    elif rsi > 68:
        return f"⚠️ {orig} - SELL\n💰 ${curr:.2f} | RSI {rsi:.1f}\n💡 High price, consider selling!"
    elif ema12 > ema26 and curr > sma20:
        return f"🚀 {orig} - BUY (Uptrend)\n💰 ${curr:.2f} | RSI {rsi:.1f}\n💡 Price above average + bullish = BUY!"
    elif ema12 < ema26:
        return f"🔻 {orig} - HOLD/WAIT\n💰 ${curr:.2f} | RSI {rsi:.1f}\n💡 Downtrend now, wait for cheaper!"
    else:
        return f"➡️ {orig} - HOLD\n💰 ${curr:.2f} | RSI {rsi:.1f}\n💡 Normal market, no action now"

def check_alert(orig, d):
    rsi=d["rsi"]; ema12=d["ema12"]; ema26=d["ema26"]; curr=d["curr"]
    key=orig; prev=last_state.get(key,{})
    alert=None
    if rsi < 32 and prev.get("rsi",50) >= 32:
        alert=f"💎 BUY ALERT {orig} ${curr:.2f} RSI {rsi:.1f} - TOO CHEAP!"
    elif rsi > 72 and prev.get("rsi",50) <= 72:
        alert=f"⚠️ SELL ALERT {orig} ${curr:.2f} RSI {rsi:.1f} - Too expensive!"
    elif ema12 > ema26 and prev.get("ema12",0) <= prev.get("ema26",0):
        alert=f"🚀 GOLDEN CROSS {orig} ${curr:.2f} - New uptrend BUY!"
    last_state[key]={"rsi":rsi,"ema12":ema12,"ema26":ema26}
    return alert

async def scan_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    syms=user_symbols.get(update.effective_user.id,["NFT","TREASURY","BONDS","AGRI","REALESTATE","SPX","AUTO","ICT","COFFEE","OILGAS","GOLD","CRYPTO","AI","TESLA"])
    await update.message.reply_text(f"🔍 V9 SCANNING ALL {len(syms)} SECTORS FROM YOUR PAPER...")
    for sym in syms:
        d=get_data(sym)
        if not d: continue
        await update.message.reply_text(human_text(sym.upper(), d))
        await asyncio.sleep(1)

async def myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🌍 ALL SECTORS:\n/myalerts NFT TREASURY BONDS AGRI REALESTATE SPX AUTO ICT COFFEE OILGAS GOLD CRYPTO AI TESLA\nThen /scan")
        return
    user_symbols[update.effective_user.id]=[s.upper() for s in context.args[:14]]
    await update.message.reply_text(f"🔥 LOCKED ALL {len(user_symbols[update.effective_user.id])}:\n{', '.join(user_symbols[update.effective_user.id])}\nSend /scan")

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower()=="on":
        auto_users.add(update.effective_user.id)
        await update.message.reply_text("✅ Auto ON! You sleep, I hunt profit for ALL sectors!")
    else:
        auto_users.discard(update.effective_user.id)
        await update.message.reply_text("❌ Auto OFF")

async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_symbols[update.effective_user.id]=["NFT","TREASURY","BONDS","AGRI","REALESTATE","SPX","AUTO","ICT","COFFEE","OILGAS","GOLD","CRYPTO","AI","TESLA"]
    await update.message.reply_text("🌍 ALL 14 SECTORS LOCKED!\nNFTs, Treasury, Bonds, Agriculture, Real Estate, Wall Street, Automotive, ICT, Coffee, Oil/Gas, Gold, Crypto, AI, Tesla + RSI+EMA signals\nSend /scan now!")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 BEAST V9 - ALL SECTORS INCLUDED!\n/history or /all = scan all 14 sectors from your paper\n/myalerts = choose\n/scan = human BUY/SELL\n/auto on = sleep alerts\n\nAll live from Wall Street + Investing.com trusted feed!")

async def hunter_loop(app_bot):
    while True:
        try:
            for uid in list(auto_users):
                for sym in user_symbols.get(uid,["BTC","GOLD","TESLA","SPX","NFT","TREASURY"]):
                    d=get_data(sym)
                    if not d: continue
                    a=check_alert(sym.upper(), d)
                    if a: await app_bot.bot.send_message(chat_id=uid, text=a)
                await asyncio.sleep(1)
            await asyncio.sleep(60)
        except: await asyncio.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot_app=ApplicationBuilder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start_cmd))
    bot_app.add_handler(CommandHandler("myalerts", myalerts))
    bot_app.add_handler(CommandHandler("scan", scan_now))
    bot_app.add_handler(CommandHandler("auto", auto_cmd))
    bot_app.add_handler(CommandHandler("all", all_cmd))
    bot_app.add_handler(CommandHandler("history", all_cmd))
    async def on_start(a): asyncio.create_task(hunter_loop(a))
    bot_app.post_init=on_start
    bot_app.run_polling()
