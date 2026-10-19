import os, requests, time, threading
from flask import Flask
TOKEN = os.environ.get("TOKEN","")
BASE = f"https://api.telegram.org/bot{TOKEN}"
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot alive"
def send(cid, text):
    try:
        requests.post(f"{BASE}/sendMessage", json={"chat_id": cid, "text": text}, timeout=15)
    except: pass
def bot_loop():
    offset=0
    while True:
        try:
            r=requests.get(f"{BASE}/getUpdates", params={"offset": offset, "timeout": 25}, timeout=35).json()
            for u in r.get("result", []):
                offset=u["update_id"]+1
                m=u.get("message")
                if not m: continue
                t=m.get("text","")
                cid=m["chat"]["id"]
                if t.startswith("/start"):
                    send(cid, "Quick Cash Bot\n/gems\n/macro\n/house")
                elif t.startswith("/macro"):
                    send(cid, "MACRO BTC $81k Risk On")
                elif t.startswith("/gems"):
                    send(cid, "GEMS\n1. PEPE\n2. TAO\n3. RNDR")
                elif t.startswith("/house"):
                    send(cid, "THE HOUSE Code: QUICKCASH")
        except:
            time.sleep(3)
threading.Thread(target=bot_loop, daemon=True).start()
app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
