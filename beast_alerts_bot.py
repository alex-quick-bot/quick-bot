import os
from flask import Flask
import threading, yfinance as yf, asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
app = Flask(__name__)
@app.route('/')
def home(): return "BEAST V9.3 STRONG + COMPANIES - LIVE 🔥"
def run_flask(): app.run(host='0.0.0.0', port=10000)

user_symbols = {}
auto_users = set()
last_state = {}

def fix_symbol(s):
    s=s.upper().strip()
    M={"NFT":"MANA-USD","TREASURY":"^TNX","BONDS":"TLT","AGRI":"DBA","REALESTATE":"VNQ","SPX":"^GSPC","AUTO":"CARZ","ICT":"XLK","COFFEE":"JO","OILGAS":"XLE","GOLD":"GC=F","CRYPTO":"BTC-USD","AI":"BOTZ","TESLA":"TSLA"}
    return M.get(s,s)

SECTOR_COMPANIES = {
    "ICT": "XLK: AAPL 20% Apple, MSFT 15% Microsoft, NVDA 10% Nvidia\n👉 BUY: AAPL / NVDA",
    "AUTO": "CARZ: TSLA Tesla, F Ford, GM, RIVN\n👉 BUY: TSLA",
    "AI": "BOTZ: NVDA, META, PLTR Palantir\n👉 BUY: NVDA",
    "OILGAS": "XLE: XOM Exxon, CVX Chevron\n👉 BUY: XOM",
    "GOLD": "GC=F: Trade GLD ETF or NEM miner\n👉 BUY: GLD",
    "CRYPTO": "BTC-USD: BTC, ETH, SOL\n👉 BUY: BTC",
    "REALESTATE": "VNQ: PLD, AMT towers\n👉 BUY: VNQ",
    "COFFEE": "JO ETF - Coffee futures\n👉 BUY: JO",
    "NFT": "MANA Decentraland, APE Bored Ape\n👉 BUY: MANA",
    "TESLA": "TSLA alone! Single company",
    "BONDS": "TLT 20Y Treasury\n👉 BUY: TLT",
    "TREASURY": "^TNX 10Y Yield\n👉 Trade TLT",
    "AGRI": "DBA Corn Wheat Soy\n👉 BUY: DBA",
    "SPX": "SPY S&P 500: AAPL MSFT NVDA\n👉 BUY: SPY",
}
def get_company(sector): return SECTOR_COMPANIES.get(sector.upper(), sector)

def get_data(symbol):
    try:
        ysym=fix_symbol(symbol)
        hist=yf.Ticker(ysym).history(period="6mo")
        if len(hist)<50: return None
        c=hist['Close']; curr=c.iloc[-1]
        sma20=c.rolling(20).mean().iloc[-1]; sma50=c.rolling(50).mean().iloc[-1]
        ema12=c.ewm(span=12).mean().iloc[-1]; ema26=c.ewm(span=26).mean().iloc[-1]
        delta=c.diff(); gain=delta.where(delta>0,0).rolling(14).mean(); loss=-delta.where(delta<0,0).rolling(14).mean()
        rs=gain/loss; rsi=100-(100/(1+rs)); rsi_now=rsi.iloc[-1]
        high20=hist['High'].rolling(20).max().iloc[-1]; low20=hist['Low'].rolling(20).min().iloc[-1]
        # Confidence
        conf=50
        if rsi<30: conf+=30
        elif rsi<38: conf+=20
        elif rsi>75: conf+=25
        if ema12>ema26: conf+=15
        if curr>sma20: conf+=10
        conf=min(conf,95)
        return {"curr":curr,"rsi":rsi_now,"ema12":ema12,"ema26":ema26,"sma20":sma20,"sma50":sma50,"high20":high20,"low20":low20,"conf":conf,"ysym":ysym}
    except: return None

def human_strong(orig, d):
    curr=d["curr"]; rsi=d["rsi"]; ema12=d["ema12"]; ema26=d["ema26"]; sma20=d["sma20"]; sma50=d["sma50"]; high20=d["high20"]; low20=d["low20"]; conf=d["conf"]
    
    # Strong logic
    if rsi < 30:
        action="💎💎 STRONG BUY"; reason="OVERSOLD! People scared, price at bottom! Big discount!"
        entry=curr; stop=low20*0.98; target=curr*1.08; profit="+8% target"
    elif rsi < 38 and ema12 > ema26:
        action="💎 BUY"; reason="Oversold + Golden momentum = About to explode UP!"
        entry=curr; stop=sma20*0.97; target=high20; profit="+5-7%"
    elif rsi < 38:
        action="💎 BUY"; reason="Cheap! Good entry for swing"
        entry=curr; stop=low20*0.98; target=sma20; profit="+3-5%"
    elif rsi > 75:
        action="⚠️⚠️ STRONG SELL"; reason="OVERBOUGHT! Everyone bought, time to take profit before drop!"
        entry=curr; stop=high20*1.02; target=sma20; profit="SELL NOW"
    elif rsi > 68:
        action="⚠️ SELL"; reason="Expensive, risk high"
        entry=curr; stop=high20*1.02; target=sma20; profit="Take profit"
    elif ema12 > ema26 and curr > sma20 and curr > sma50:
        action="🚀 BUY - UPTREND"; reason="All trends UP! Strong bull!"
        entry=curr; stop=sma20*0.98; target=high20; profit="+4-6%"
    elif ema12 < ema26:
        action="🔻 WAIT"; reason="Downtrend, wait for RSI <35"
        entry=low20; stop=low20*0.95; target=sma20; profit="Wait"
    else:
        action="➡️ HOLD"; reason=f"Normal market RSI {rsi:.1f}"
        entry=curr; stop=low20; target=high20; profit="0%"

    company=get_company(orig)
    
    return (
        f"{action} {orig} | CONFIDENCE {conf}%\n"
        f"💰 Price: ${curr:.2f} | RSI: {rsi:.1f}\n"
        f"📈 EMA: {ema12:.1f}/{ema26:.1f} | SMA20: ${sma20:.1f} SMA50: ${sma50:.1f}\n"
        f"💡 WHY: {reason}\n"
        f"🎯 ENTRY: ${entry:.2f} | STOP LOSS: ${stop:.2f} | TARGET: ${target:.2f} ({profit})\n"
        f"📊 Support: ${low20:.2f} | Resistance: ${high20:.2f}\n"
        f"🏢 BUY WHICH:\n{company}\n"
        f"----------------------------"
    )

def check_alert(orig,d):
    rsi=d["rsi"]; ema12=d["ema12"]; ema26=d["ema26"]; curr=d["curr"]; conf=d["conf"]
    key=orig; prev=last_state.get(key,{})
    alert=None
    if rsi < 32 and prev.get("rsi",50) >= 32:
        alert=f"💎💎 BUY ALERT {orig} ${curr:.2f} RSI {rsi:.1f} CONF {conf}%\n{get_company(orig)}\nTARGET +8% | STOP -3%"
    elif rsi > 72 and prev.get("rsi",50) <= 72:
        alert=f"⚠️ SELL ALERT {orig} ${curr:.2f} RSI {rsi:.1f} CONF {conf}% Take profit!"
    elif ema12 > ema26 and prev.get("ema12",0) <= prev.get("ema26",0):
        alert=f"🚀 GOLDEN CROSS {orig} ${curr:.2f} CONF {conf}%\nUptrend start! {get_company(orig)}"
    last_state[key]={"rsi":rsi,"ema12":ema12,"ema26":ema26}
    return alert

async def scan_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    syms=user_symbols.get(update.effective_user.id,["NFT","TREASURY","BONDS","AGRI","REALESTATE","SPX","AUTO","ICT","COFFEE","OILGAS","GOLD","CRYPTO","AI","TESLA"])
    await update.message.reply_text(f"🔥 V9.3 BEAST MODE - {len(syms)} sectors with FULL POWER...")
    for sym in syms:
        d=get_data(sym)
        if not d: continue
        await update.message.reply_text(human_strong(sym.upper(), d))
        await asyncio.sleep(1)

async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_symbols[update.effective_user.id]=["NFT","TREASURY","BONDS","AGRI","REALESTATE","SPX","AUTO","ICT","COFFEE","OILGAS","GOLD","CRYPTO","AI","TESLA"]
    await update.message.reply_text("🌍 ALL 14 LOCKED - STRONG MODE!\nSend /scan")

async def myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Type: /myalerts AUTO ICT GOLD etc")
        return
    user_symbols[update.effective_user.id]=[s.upper() for s in context.args[:14]]
    await update.message.reply_text(f"✅ Locked: {', '.join(user_symbols[update.effective_user.id])}")

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower()=="on":
        auto_users.add(update.effective_user.id)
        await update.message.reply_text("✅ Auto STRONG alerts ON while you sleep!")
    else:
        auto_users.discard(update.effective_user.id)
        await update.message.reply_text("❌ Auto OFF")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 BEAST V9.3 STRONG!\n/all - lock all\n/scan - FULL: price, RSI, EMA, SMA, support, resistance, entry, stop, target, confidence, company\n/auto on - sleep hunter")

async def hunter_loop(app_bot):
    while True:
        try:
            for uid in list(auto_users):
                for sym in user_symbols.get(uid,["BTC","GOLD","TESLA","SPX"]):
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
