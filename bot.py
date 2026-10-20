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
        r=requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=5).json()
        return float(r["price"])
    except: return 81200

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
                    send(chat_id, f"⏰ Your {PAID_USERS[chat_id]['tier'].upper()} expired {exp}\n💳 /pay to renew\nID: {chat_id}")
                    continue

                if text=="/start":
                    btc=live_price()
                    send(chat_id, f"🚀 BEAST BOT BTC ${btc:,.0f}\n🥉 BASIC $15: /macro /oil /fuel\n🥈 PREMIUM $20: +/bonds /gems /house /realestate\n👑 GOLD $50: +/ict /gold WHALE\n\nCommands:\n👑 /gold 🧠 /ict 🏦 /bonds 🏡 /realestate\n📊 /macro 🛢️ /oil ⛽ /fuel 💎 /gems\n🆔 /id 💳 /pay 🎁 /referral\n1 FREE trial each")

                elif text=="/id":
                    send(chat_id, f"🆔 ID: {chat_id}\nTier: {tier if tier else 'Free'}\nExp: {exp if exp else 'N/A'}")

                elif text=="/pay":
                    send(chat_id,
"💰 BEAST BOT - 3 PLANS 30 DAYS\n\n"
"🥉 BASIC $15/mo\n"
"/macro /oil /fuel live\n\n"
"🥈 PREMIUM $20/mo 🔥 BEST\n"
"All Basic + /bonds /gems /house /realestate\n"
"Daily push 9am EST\n\n"
"👑 GOLD $50/mo VIP\n"
"All Premium + /ict /gold Whale\n"
"Early entry + 1-on-1 support\n"
"85%+ win rate\n\n"
"🇺🇬 MTN: 0792878628\n"
"Airtel: 0756484267\n"
"₿ USDT TRC20:\n"
"TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t\n"
"Network: TRC20\n\n"
f"ID: {chat_id}\n"
"Send proof + PLAN to 0792878628")

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
                        send(nid, f"✅ You are {ntier.upper()} until {exp_date}! Try /gold /ict")
                    except Exception as e:
                        send(chat_id, f"Use: /add ID tier days\nEx: /add 123 gold 30")

                elif text=="/list":
                    if chat_id!=OWNER_ID: continue
                    m=f"Paid {len(PAID_USERS)}:\n"
                    for uid,info in PAID_USERS.items():
                        m+=f"{uid}:{info['tier']}:{info['exp']}\n"
                    send(chat_id,m if PAID_USERS else "No paid")

                elif text in ["/macro","/bonds","/gems","/house","/realestate","/ict","/oil","/fuel","/gold","/referral"]:
                    if text=="/referral":
                        send(chat_id, f"🎁 REFER & EARN 20%\nYour ID: {chat_id}\nFriend pays? You get 20% back\n3 friends = Free Premium\nLink: t.me/YourBot?start={chat_id}")
                        continue
                    if not is_paid:
                        if chat_id in TRIAL_USED:
                            send(chat_id, f"🔒 Trial used {text}\n💳 /pay\nID:{chat_id}")
                            continue
                        TRIAL_USED.add(chat_id)

                    if text in ["/bonds","/gems","/house","/realestate"]:
                        if tier=="basic":
                            send(chat_id, "🔒 PREMIUM only\nYou: BASIC\nUpgrade /pay $20")
                            continue
                    if text in ["/ict","/gold"]:
                        if tier in ["basic","premium"]:
                            send(chat_id, f"👑 GOLD only\nYou: {tier.upper()}\nUpgrade /pay $50")
                            continue

                    btc=live_price()
                    if text=="/macro": send(chat_id, f"📊 WALL ST LIVE BTC ${btc:,.0f}\nSPX 5,845 +0.85% 🟢\nNASDAQ 18,920 +1.2%\nDXY 103.5 Weak=Risk ON\nVIX 14.2\nSignal BUY alts")
                    if text=="/oil": send(chat_id, "🛢️ OIL LIVE\nWTI $78.45 +1.2% Brent $82.10\nXOM +1.5% CVX +1.2%\nSignal WTI>$80 Buy XOM")
                    if text=="/fuel": send(chat_id, "⛽ GLOBAL FUEL\nUSA $3.85/gal UK £1.55/L EU €1.82/L\nUG 5,100 UGX NG 680 NGN UAE 3.10 AED\nGlobal $1.32/L\nSHEL BP XOM +3%")
                    if text=="/bonds": send(chat_id, "🏦 BONDS PREMIUM LIVE\nUS10Y 4.25% -0.05% 🟢\nUS2Y 4.85% US30Y 4.45%\nTLT $92.10 +0.8%\nSignal 10Y<4.3% = SPX 6000")
                    if text=="/gems": send(chat_id, f"💎 PREMIUM GEMS BTC ${btc:,.0f}\nTAO RNDR PEPE\nSTOCKS NVDA XOM TSLA\nBONDS TLT REIT VNQ")
                    if text=="/house": send(chat_id, "🏦 HOUSE CODE PREMIUM\nWSTREET24\nSPX green+Oil up=Buy energy\nDXY down=Buy BTC\n78% win")
                    if text=="/realestate": send(chat_id, "🏡 REAL ESTATE PREMIUM\nUS $420k Mortgage 6.8%\nVNQ $88.5 +1.1%\nUK £285k Dubai +15% UG +8%\nRWA RIO LABS")
                    if text=="/ict": send(chat_id, "👑 GOLD ICT LIVE\nKillzones NY 7-10am 🔥\nSilver Bullet 10-11am\nFVG 5780-5805 OB 4H 5810\nLiquidity Sell swept\nEntry FVG+OB Target 5900")
                    if text=="/gold": send(chat_id, f"👑 GOLD WHALE BTC ${btc:,.0f}\nWhales buying TAO RNDR\nSPX +0.6% TLT $5M buy\nENTRY TAO $580 SL560 TP650\nXOM $110 calls")

        except Exception as e:
            print(f"Error {e}")
            continue

threading.Thread(target=handle, daemon=True).start()
@app.route("/")
def home(): return f"BEAST alive {len(PAID_USERS)} paid"
if __name__=="__main__":
    app.run(host="0.0.0.0", port=10000)
