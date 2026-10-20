import os, requests, threading
from flask import Flask

TOKEN = os.environ.get("TOKEN", "")
BASE = f"https://api.telegram.org/bot{TOKEN}"
OWNER_ID = 8665686560
PAID_FILE = "paid.txt"
TRIAL_USED = set()

app = Flask(__name__)

def load_paid():
    if not os.path.exists(PAID_FILE):
        return set()
    try:
        with open(PAID_FILE, "r") as f:
            return set(int(line.strip()) for line in f if line.strip().isdigit())
    except:
        return set()

def save_paid(paid_set):
    try:
        with open(PAID_FILE, "w") as f:
            for pid in paid_set:
                f.write(str(pid) + "\n")
    except:
        pass

PAID_USERS = load_paid()

def send(c, t):
    try: 
        requests.post(f"{BASE}/sendMessage", json={"chat_id": c, "text": t}, timeout=10)
    except: 
        pass

def handle():
    offset = 0
    global PAID_USERS
    print("Bot polling...")
    print(f"Loaded {len(PAID_USERS)} paid users")
    while True:
        try:
            r = requests.get(f"{BASE}/getUpdates", params={"offset": offset, "timeout": 40}, timeout=45).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                if not chat_id: continue

                is_paid = (chat_id == OWNER_ID) or (chat_id in PAID_USERS)

                if text == "/start":
                    send(chat_id, "🚀 Quick Cash Bot\n/macro - Market\n/gems - Top picks\n/house - Code\n/id - Your ID\n/pay - Unlock")
                
                elif text == "/id":
                    send(chat_id, f"Your ID: {chat_id}")
                
                elif text == "/pay":
                    send(chat_id, f"💰 15k UGX Lifetime\nMTN MoMo: YOUR NUMBER HERE\nYour ID: {chat_id}\nAfter pay send screenshot to admin")
                
                elif text.startswith("/add"):
                    if chat_id != OWNER_ID:
                        continue
                    try:
                        new_id = int(text.split()[1])
                        PAID_USERS.add(new_id)
                        save_paid(PAID_USERS)
                        send(chat_id, f"✅ Added {new_id} - now paid (SAVED forever)")
                        send(new_id, "✅ Payment confirmed! You now have lifetime access. Try /gems")
                    except Exception as e:
                        send(chat_id, f"Usage: /add 123456789\nError: {e}")

                elif text == "/list":
                    if chat_id != OWNER_ID:
                        continue
                    if not PAID_USERS:
                        send(chat_id, "No paid users yet")
                    else:
                        send(chat_id, f"Paid users ({len(PAID_USERS)}):\n" + "\n".join(str(x) for x in PAID_USERS))

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
def home(): return f"Bot alive - {len(PAID_USERS)} paid users"

if __name__ == "__main__": 
    app.run(host="0.0.0.0", port=10000)
