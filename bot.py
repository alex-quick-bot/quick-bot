import os, requests, threading
from flask import Flask

TOKEN = os.environ.get("TOKEN", "")
BASE = f"https://api.telegram.org/bot{TOKEN}"

# === SET YOUR ID HERE ===
# After you test /id, put your ID here. You will always be free.
OWNER_ID = 8665686560
TRIAL_USED = set()

app = Flask(__name__)

def send(c, t):
    try: requests.post(f"{BASE}/sendMessage", json={"chat_id": c, "text": t}, timeout=10)
    except: pass

def handle():
    offset = 0
    while True:
        try:
            r = requests.get(f"{BASE}/getUpdates", params={"offset": offset, "timeout": 40}, timeout=45).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                chat_id = u.get("message", {}).get("chat", {}).get("id")
                text = u.get("message", {}).get("text", "")
                if not chat_id: continue

                is_paid = (chat_id == OWNER_ID) or (chat_id in PAID_USERS) or (OWNER_ID == 0)

                if text == "/start":
                    send(chat_id, "🚀 Quick Cash Bot\n/macro\n/gems\n/house\n/id\n/pay")
                elif text == "/id":
                    send(chat_id, f"Your ID: {chat_id}\nSend to admin after paying")
                elif text == "/pay":
                    send(chat_id, f"💰 15k UGX Lifetime\nMTN: 07XXXXXXX\nYour ID: {chat_id}\nAfter pay send screenshot")
                elif text in ["/macro","/gems","/house"]:
                    if not is_paid:
                        if chat_id in TRIAL_USED:
                            send(chat_id, "🔒 Free trial used. Send /pay to unlock forever")
                            continue
                        TRIAL_USED.add(chat_id)
                    
                    if text == "/macro": send(chat_id, "📈 MACRO BTC $81k Risk On - BUY")
                    if text == "/gems": send(chat_id, "💎 GEMS: PEPE, TAO, RNDR")
                    if text == "/house": send(chat_id, "🏦 CODE: QUICKCASH")
        except: pass

threading.Thread(target=handle, daemon=True).start()
@app.route("/")
def home(): return "Bot alive"
if __name__ == "__main__": app.run(host="0.0.0.0", port=10000)
