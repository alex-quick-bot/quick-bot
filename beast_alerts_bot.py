import os
from flask import Flask
import threading, yfinance as yf, asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
app = Flask(__name__)
@app.route('/')
def home(): return "BEAST V9.5 FINAL - LIVE 🔥"
def run_flask(): app.run(host='0.0.0.0', port=10000)

user_symbols = {}
auto_users = set()
last_state = {}

def fix_symbol(s):
    s=s.upper().strip()
    M={
        "NFT":"MANA-USD","NFTS":"MANA-USD","MANA":"MANA-USD","APE":"APE-USD","SAND":"SAND-USD",
        "TREASURY":"^TNX","GOVT":"^TNX","BONDS":"TLT","BOND":"TLT","TLT":"TLT",
        "AGRI":"DBA","AGRICULTURE":"DBA","DBA":"DBA",
        "REALESTATE":"VNQ","REIT":"VNQ","VNQ":"VNQ",
        "SPX":"^GSPC","WALLSTREET":"^GSPC","SPY":"SPY",
        "AUTO":"CARZ","CARZ":"CARZ","FORD":"F","GM":"GM","RIVN":"RIVN",
        "ICT":"XLK","XLK":"XLK","COFFEE":"JO","JO":"JO",
        "OILGAS":"XLE","XLE":"XLE","OIL":"CL=F","GOLD":"GC=F","GLD":"GLD",
        "CRYPTO":"BTC-USD","BTC":"BTC-USD","ETH":"ETH-USD","SOL":"SOL-USD",
        "AI":"BOTZ","BOTZ":"BOTZ","TESLA":"TSLA","TSLA":"TSLA",
    }
    return M.get(s,s)

SECTOR_COMPANIES = {
    "NFT": "MANA, APE Bored Ape -> BUY MANA on Binance",
    "TREASURY": "^TNX Yield -> Trade TLT bond",
    "BONDS": "TLT 20Y Bond -> Buy TLT",
    "AGRI": "DBA Corn Wheat Soy -> Buy DBA",
    "REALESTATE": "VNQ PLD AMT -> Buy VNQ",
    "SPX": "SPY Wall St AAPL MSFT NVDA -> Buy SPY",
    "AUTO": "CARZ TSLA Ford GM -> BUY TSLA",
    "ICT": "XLK AAPL MSFT NVDA -> BUY AAPL/NVDA",
    "COFFEE": "JO Coffee -> Buy JO",
    "OILGAS": "XLE XOM Chevron -> BUY XOM",
    "GOLD": "GC=F GLD Gold -> Buy GLD",
    "CRYPTO": "BTC ETH SOL -> Buy BTC",
    "AI": "BOTZ NVDA META PLTR -> BUY NVDA",
    "TESLA": "TSLA alone -> Buy TSLA",
}

def get_data(symbol):
    try:
        ysym=fix_symbol(symbol)
        hist=yf.Ticker(ysym).history(period="6mo")
        if hist.empty or len(hist)<30: return None
        c=hist['Close']; curr=float(c.iloc[-1])
        sma20=float(c.rolling(20).mean().iloc[-1]); sma50=float(c.rolling(50).mean().iloc[-1])
        ema12=float(c.ewm(span=12).mean().iloc[-1]); ema26=float(c.ewm(span=26).mean().iloc[-1])
        delta=c.diff(); gain=delta.where(delta>0,0).rolling(14).mean(); loss=-delta.where(delta<0,0).rolling(14).mean()
        rs=gain/loss; rsi=100-(100/(1+rs)); rsi_now=float(rsi.iloc[-1])
        high20=float(hist['High'].rolling(20).max().iloc[-1]); low20=float(hist['Low'].rolling(20).min().iloc[-1])
        conf=90 if rsi_now<30 else 80 if rsi_now<38 else 85 if rsi_now>75 else 75 if rsi_now>68 else 65 if ema12>ema26 else 55
        return {"curr":curr,"rsi":rsi_now,"ema12":ema12,"ema26":ema26,"sma20":sma20,"sma50":sma50,"high20":high20,"low20":low20,"conf":conf,"ysym":ysym}
    except: return None

def make_text(orig,d):
    curr=d["curr"]; rsi=d["rsi"]; ema12=d["ema12"]; ema26=d["ema26"]; sma20=d["sma20"]; sma50=d["sma50"]; high20=d["high20"]; low20=d["low20"]; conf=d["conf"]
    if rsi<30: action="💎💎 STRONG BUY"; reason="Oversold Discount!"; tgt=curr*1.08; stp=low20*0.98; prof="+8%"
    elif rsi<38 and ema12>ema26: action="💎 BUY"; reason="Oversold + Bullish UP!"; tgt=high20; stp=sma20*0.97; prof="+5-7%"
    elif rsi<38: action="💎 BUY"; reason="Cheap entry"; tgt=sma20; stp=low20*0.98; prof="+3-5%"
    elif rsi>75: action="⚠️⚠️ STRONG SELL"; reason="Overbought Take profit"; tgt=sma20; stp=high20*1.02; prof="SELL"
    elif rsi>68: action="⚠️ SELL"; reason="Expensive"; tgt=sma20; stp=high20*1.02; prof="Sell"
    elif ema12>ema26 and curr>sma20: action="🚀 BUY"; reason="Uptrend!"; tgt=high20; stp=sma20*0.98; prof="+4-6%"
    else: action="➡️ HOLD"; reason=f"Normal RSI {rsi:.1f}"; tgt=high20; stp=low20; prof="Hold"
    comp=SECTOR_COMPANIES.get(orig, orig)
    return f"{action} {orig} CONF {conf}%\n💰 ${curr:.2f} RSI {rsi:.1f} EMA {ema12:.1f}/{ema26:.1f} SMA {sma20:.1f}/{sma50:.1f}\n💡 {reason}\n🎯 ENTRY ${curr:.2f} STOP ${stp:.2f} TARGET ${tgt:.2f} {prof}\n📊 Sup ${low20:.2f} Res ${high20:.2f}\n🏢 {comp}\n---"

def check_alert(orig,d):
    rsi=d["rsi"]; ema12=d["ema12"]; ema26=d["ema26"]; curr=d["curr"]; conf=d["conf"]
    key=orig; prev=last_state.get(key,{})
    alert=None
    if rsi < 32 and prev.get("rsi",50) >= 32:
        alert=f"💎💎 BUY ALERT {orig} ${curr:.2f} RSI {rsi:.1f} CONF {conf}%\n{SECTOR_COMPANIES.get(orig,'')}\nTARGET +8% STOP -3%"
    elif rsi > 72 and prev.get("rsi",50) <= 72:
        alert=f"⚠️ SELL ALERT {orig} ${curr:.2f} RSI {rsi:.1f} Take profit!"
    elif ema12 > ema26 and prev.get("ema12",0) <= prev.get("ema26",0):
        alert=f"🚀 GOLDEN CROSS {orig} ${curr:.2f} CONF {conf}%\n{SECTOR_COMPANIES.get(orig,'')}"
    last_state[key]={"rsi":rsi,"ema12":ema12,"ema26":ema26}
    return alert

async def scan_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    syms=user_symbols.get(update.effective_user.id,["NFT","TREASURY","BONDS","AGRI","REALESTATE","SPX","AUTO","ICT","COFFEE","OILGAS","GOLD","CRYPTO","AI","TESLA"])
    await update.message.reply_text(f"🔥 V9.5 FINAL - Scanning {len(syms)} sectors...")
    batch=""
    for i,sym in enumerate(syms,1):
        d=get_data(sym)
        if not d: batch+=f"❌ {sym} No data\n---\n"
        else: batch+=make_text(sym.upper(), d)+"\n"
        if i%3==0:
            await update.message.reply_text(batch)
            batch=""; await asyncio.sleep(1)
    if batch: await update.message.reply_text(batch)
    await update.message.reply_text(f"✅ DONE {len(syms)} sectors! Now /auto on to hunt while you sleep!")

async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_symbols[update.effective_user.id]=["NFT","TREASURY","BONDS","AGRI","REALESTATE","SPX","AUTO","ICT","COFFEE","OILGAS","GOLD","CRYPTO","AI","TESLA"]
    await update.message.reply_text("🌍 ALL 14 LOCKED! /scan to see + /auto on for sleep alerts!")

async def myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use /all or /myalerts ICT AUTO GOLD...")
        return
    user_symbols[update.effective_user.id]=[s.upper() for s in context.args[:14]]
    await update.message.reply_text(f"✅ Locked: {', '.join(user_symbols[update.effective_user.id])}")

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower()=="on":
        auto_users.add(update.effective_user.id)
        await update.message.reply_text("✅ Auto ON! I will alert you while you sleep with company names!")
    else:
        auto_users.discard(update.effective_user.id)
        await update.message.reply_text("❌ Auto OFF")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 BEAST V9.5 FINAL - ALL FIXED!\n/all -> /scan -> /auto on\nScan shows all + Sleep hunter works!")

async def hunter_loop(app_bot):
    while True:
        try:
            for uid in list(auto_users):
                syms=user_symbols.get(uid,["NFT","TREASURY","BONDS","AGRI","REALESTATE","SPX","AUTO","ICT","COFFEE","OILGAS","GOLD","CRYPTO","AI","TESLA"])
                for sym in syms:
                    d=get_data(sym)
                    if not d: continue
                    a=check_alert(sym.upper(), d)
                    if a:
                        await app_bot.bot.send_message(chat_id=uid, text=a)
                    await asyncio.sleep(1)
            await asyncio.sleep(60)
        except Exception as e:
            print(f"Hunter error {e}")
            await asyncio.sleep(60)

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
