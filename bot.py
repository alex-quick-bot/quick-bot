import os, requests, threading
from flask import Flask

TOKEN = os.environ.get("TOKEN", "")
BASE = f"https://api.telegram.org/bot{TOKEN}"
OWNER_ID = 8665686560
PAID_USERS = set()  # <-- THIS WAS MISSING
TRIAL_USED = set()

app = Flask(__name__)

def send(c, t):
    try: 
        requests.post(f"{BASE}/sendMessage", json={"chat_id": c, "text": t}, timeout=10)
    except: 
        pass

def handle():
    offset = 0
    print("Bot polling...")
    while True:
        try:
            r = requests.get(f"{BASE}/getUpdates", params={"offset": offset, "timeout": 40}, timeout=45).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                if not chat_id: continue

                # Owner or paid
                is_paid = (chat_id == OWNER_ID) or (chat_id in PAID_USERS)

                if text == "/start":
                    send(chat_id, "🚀 Quick Cash Bot\n/macro - Market\n/gems - Top picks\n/house - Code\n/id - Your ID\n/pay - Unlock")
                
                elif text == "/id":
                    send(chat_id, f"Your ID: {chat_id}")
                
                elif text == "/pay":
                    send(chat_id, f"💰 15k UGX Lifetime\nMTN: 07XXXXXXX\nYour ID: {chat_id}\nAfter pay send screenshot to admin")
                
                elif text.startswith("/add"):
                    # Only you can add paid users
                    if chat_id != OWNER_ID:
                        continue
                    try:
                        new_id = int(text.split()[1])
                        PAID_USERS.add(new_id)
                        send(chat_id, f"✅ Added {new_id} - now paid")
                        send(new_id, "✅ Payment confirmed! You now have lifetime access. Try /gems")
                    except:
                        send(chat_id, "Usage: /add 123456789")

                elif text in ["/macro","/gems","/house"]:
                    if not is_paid:
                        if chat_id in TRIAL_USED:
                            send(chat_id, f"🔒 Free trial used.\nSend /pay to unlock forever\nYour ID: {chat_id}")
                            continue
                        TRIAL_USED.add(chat_id)
                    
                    if text == "/macro": send(chat_id, "📈 MACRO BTC $81k Risk On - BUY")
                    if text == "/gems": send(chat_id, "💎 GEMS: PEPE, TAO, RNDR")
                    if text == "/house": send(chat_id, "🏦 CODE: QUICKCASH")

        except Exception as e:
            print(f"Error: {e}")
            continue

threading.Thread(target=handle, daemon=True).start()

@app.route("/")
def home(): return "Bot alive"

if __name__ == "__main__": 
    app.run(host="0.0.0.0", port=10000)
