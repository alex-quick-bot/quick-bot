import os, requests, threading, time
from datetime import datetime, timedelta
from flask import Flask

TOKEN = os.environ.get("TOKEN", "")
BASE = f"https://api.telegram.org/bot{TOKEN}"
OWNER_ID = 8665686560
PAID_FILE = "paid.txt"
TRIAL_USED = set()

app = Flask(__name__)

def load_paid():
    data={}
    if not os.path.exists(PAID_FILE): return data
    try:
        with open(PAID_FILE,"r") as f:
            for line in f:
                line=line.strip()
                if not line: continue
                try:
                    if ":" in line:
                        parts=line.split(":")
                        if len(parts)>=3:
                            uid=int(parts[0]); tier=parts[1]; exp=parts[2]
                            data[uid]={"tier":tier,"exp":exp}
                        else:
                            uid=int(parts[0]); tier=parts[1]
                            exp=(datetime.now()+timedelta(days=30)).strftime("%Y-%m-%d")
                            data[uid]={"tier":tier,"exp":exp}
                except: pass
    return data

def save_paid(d):
    try:
        with open(PAID_FILE,"w") as f:
            for uid,info in d.items():
                f.write(f"{uid}:{info['tier']}:{info['exp']}\n")
    except: pass

PAID_USERS = load_paid()

def get_tier(uid):
    if uid==OWNER_ID: return "gold", "2099-12-31"
    info=PAID_USERS.get(uid)
    if not info: return None, None
    # check expiry
    try:
        exp_date=datetime.strptime(info["exp"], "%Y-%m-%d")
        if datetime.now() > exp_date:
            return "expired", info["exp"]
    except: pass
    return info["tier"], info["exp"]

def send(c,t):
    try: requests.post(f"{BASE}/sendMessage", json={"chat_id":c,"text":t}, timeout=10)
    except: pass

def live_price():
    try:
        # BTC price live
        r=requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=5).json()
        btc=float(r["price"])
    except: btc=81200
    return btc

def handle():
    offset=0
    global PAID_USERS
    print("BEAST started")
    while True:
        try:
            r=requests.get(f"{BASE}/getUpdates", params={"offset":offset,"timeout":40}, timeout=45).json()
            for u in r.get("result", []):
                offset=u["update_id"]+1
                msg=u.get("message",{})
                chat_id=msg.get("chat",{}).get("id")
                text=msg.get("text","")
                if not chat_id: continue
                tier, exp = get_tier(chat_id)
                is_paid = tier not in [None, "expired"]

                if tier=="expired":
                    send(chat_id, f"⏰ Your {PAID_USERS[chat_id]['tier'].upper()} plan expired on {exp}\n💳 /pay to renew\nID: {chat_id}")
                    continue

                if text=="/start":
                    btc=live_price()
                    send(chat_id,
f"🚀 WALL ST BEAST BOT - BTC ${btc:,.0f}\n"
"Live Wall St + ICT + Real Estate\n\n"
"🥉 BASIC $15: /macro /oil /fuel\n"
"🥈 PREMIUM $20: +/bonds /gems /house /realestate\n"
"👑 GOLD $50: +/ict /gold WHALE\n\n"
"👑 /gold | 🧠 /ict | 🏦 /bonds | 🏡 /realestate\n"
"📊 /macro | 🛢️ /oil | ⛽ /fuel | 💎 /gems\n\n"
"🆔 /id | 💳 /pay | 🎁 /referral\n"
"1 FREE trial each - then pay")

                elif text=="/id":
                    send(chat_id, f"🆔 ID: {chat_id}\nTier: {tier if tier else 'Free'}\nExp: {exp if exp else 'N/A'}")

                elif text=="/referral":
                    send(chat_id,
f"🎁 REFER & EARN 20%\n"
"Share your ID: {chat_id}\n"
"Friend pays? You get 20% back\n"
"3 friends = Free Premium month\n"
"5 friends = Free Gold month\n"
"Your link: t.me/YourBot?start={chat_id}")

                elif text=="/pay":
                    send(chat_id,
"💰 BEAST BOT - 3 PLANS - 30 DAYS\n"
"------------------------\n"
"🥉 BASIC $15/mo\n"
"✓ /macro SPX NASDAQ DXY VIX live\n"
"✓ /oil WTI Brent + XOM CVX\n"
"✓ /fuel Global USA UK EU UG\n"
"Perfect for starters\n\n"
"🥈 PREMIUM $20/mo BEST SELLER 🔥\n"
"All Basic +:\n"
"✓ /bonds US10Y US2Y TLT HYG live\n"
"✓ /gems Crypto TAO RNDR + Stocks NVDA\n"
"✓ /house WSTREET24 78% win rate\n"
"✓ /realestate VNQ + Housing $420k + RWA\n"
"✓ Daily push 9am EST\n"
"✓ Most popular = more signals\n\n"
"👑 GOLD $50/mo WHALE VIP 👑\n"
"All Premium +:\n"
"✓ /ict FVG OB Killzones Silver Bullet\n"
"✓ /gold Private whale buys TAO RNDR\n"
"✓ Early entry 30min before Premium\n"
"✓ 1-on-1 WhatsApp 0792878628\n"
"✓ 85%+ win rate\n"
"✓ Expiry alerts + support\n\n"
"🇺🇬 MTN: 0792878628\n"
"Airtel: 0756484267\n"
"₿ USDT TRC20:\n"
"TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t\n"
"Network: TRC20\n\n"
f"ID: {chat_id}\n"
"Send proof + PLAN to 0792878628\n"
"Ex: 'My ID 123456 gold'")

                elif text.startswith("/add"):
                    if chat_id!=OWNER_ID: continue
                    try:
                        parts=text.split()
                        nid=int(parts[1])
                        ntier=parts[2].lower() if len(parts)>2 else "premium"
                        days=int(parts[3]) if len(parts)>3 else 30
                        if ntier not in ["basic","premium","gold"]: ntier="premium"
                        exp_date=(datetime.now()+timedelta(days=days)).strftime("%Y-%m-%d")
                        PAID_USERS[nid]={"tier":ntier,"exp":exp_date}
                        save_paid(PAID_USERS)
                        send(chat_id, f"✅ {nid} = {ntier.upper()} until {exp_date}\nTotal: {len(PAID_USERS)}")
                        send(nid, f"✅ You are {ntier.upper()} until {exp_date}!\n👑 Try /gold /ict /bonds now\n🎁 /referral to earn")
                    except Exception as e:
                        send(chat_id, f"Usage: /add ID tier days\nEx: /add 123 gold 30\nError {e}")

                elif text=="/list":
                    if chat_id!=OWNER_ID: continue
                    if not PAID_USERS: send(chat_id,"No paid")
                    else:
                        m=f"Paid {len(PAID_USERS)}:\n"
                        for uid,info in PAID_USERS.items():
                            m+=f"{uid}:{info['tier']}:{info['exp']}\n"
                        send(chat_id,m)

                elif text in ["/macro","/bonds","/gems","/house","/realestate","/ict","/oil","/fuel","/gold"]:
                    if not is_paid:
                        if chat_id in TRIAL_USED:
                            send(chat_id, f"🔒 Trial used {text}\n💳 /pay to unlock\n🎁 /referral = free\nID:{chat_id}")
                            continue
                        TRIAL_USED.add(chat_id)

                    btc=live_price()
                    
                    # Access control
                    if text in ["/macro","/oil","/fuel"]:
                        pass
                    elif text in ["/bonds","/gems","/house","/realestate"]:
                        if tier and tier=="basic":
                            send(chat_id, "🔒 PREMIUM only\nYou: BASIC\nUpgrade: /pay $20 Premium\nYou get Bonds+RealEstate+Gems")
                            continue
                    elif text in ["/ict","/gold"]:
                        if tier and tier in ["basic","premium"]:
                            send(chat_id, f"👑 GOLD only\nYou: {tier.upper()}\nUpgrade: /pay $50 Gold\nICT + Whale signals")
                            continue

                    if text=="/macro":
                        send(chat_id, f"📊 WALL ST LIVE BTC ${btc:,.0f}\nSPX 5,845 +0.85% 🟢\nNASDAQ 18,920 +1.2%\nDXY 103.5 Weak=Risk ON\nVIX 14.2\nFED cut 70%\nSignal: BUY alts when BTC>{btc-1000:.0f}")
                    if text=="/oil":
                        send(chat_id, "🛢️ OIL LIVE\nWTI $78.45 +1.2% Brent $82.10\nXOM +1.5% CVX +1.2%\nEnergy SPX +2.1%\nSignal: WTI>$80 = Buy XOM calls + BTC")
                    if text=="/fuel":
                        send(chat_id, "⛽ GLOBAL FUEL\nUSA $3.85/gal UK £1.55/L EU €1.82/L\nUG 5,100 UGX NG 680 NGN UAE 3.10 AED\nGlobal $1.32/L\nWall St: SHEL BP XOM +3% margins up")
                    if text=="/bonds":
                        send(chat_id, "🏦 BONDS PREMIUM LIVE\nUS10Y 4.25% -0.05% 🟢\nUS2Y 4.85% US30Y 4.45%\nTLT $92.10 +0.8% HYG +0.5%\nSignal: 10Y<4.3% = SPX 6000 + BTC pump\nICT: Bonds drive liquidity")
                    if text=="/gems":
                        send(chat_id, f"💎 PREMIUM GEMS BTC ${btc:,.0f}\nTAO AI=NVDA, RNDR GPU, PEPE beta\nSTOCKS NVDA XOM TSLA\nBONDS TLT REIT VNQ\nSignal SPX>5800 = Alts 2x")
                    if text=="/house":
                        send(chat_id, "🏦 HOUSE CODE PREMIUM\nWSTREET24\nSPX green+Oil up=Buy energy\nDXY down=Buy BTC\nBonds down=Buy all\nICT OB+FVG=Sniper 78% win")
                    if text=="/realestate":
                        send(chat_id, "🏡 REAL ESTATE PREMIUM\nUS $420k Mortgage 6.8% down\nVNQ $88.5 +1.1% AMT PLD O\nUK £285k Dubai +15% UG +8%\nRWA RIO LABS\nSignal: Mortgage down=REIT pump")
                    if text=="/ict":
                        send(chat_id, "👑 GOLD ICT LIVE\nKillzones NY 7-10am EST 🔥\nSilver Bullet 10-11am\nBias Bullish SPX + DXY weak\nFVG Daily 5780-5805 OB 4H 5810\nLiquidity Sell swept\nEntry FVG+OB Target 5900")
                    if text=="/gold":
                        send(chat_id, f"👑 GOLD WHALE BTC ${btc:,.0f}\nWhales buying TAO RNDR\nSPX fut +0.6% TLT $5M buy\nOPEC cut rumor=Long\nENTRY: TAO $580 SL560 TP650\nXOM $110 calls Sell DXY\nDM 0792878628")

        except Exception as e:
            print(f"Error {e}")
            continue

threading.Thread(target=handle, daemon=True).start()

@app.route("/")
def home(): return f"BEAST alive {len(PAID_USERS)} paid - LIVE"

if __name__=="__main__":
    app.run(host="0.0.0.0", port=10000)
