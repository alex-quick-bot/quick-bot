from flask import Flask
import json, os
import yfinance as yf
from datetime import datetime

app = Flask(__name__)
PAPER_FILE = "paper_trades.json"
START_BALANCE = 100.0

def load_paper():
    if os.path.exists(PAPER_FILE):
        with open(PAPER_FILE, "r") as f:
            return json.load(f)
    return {"balance": START_BALANCE, "trades": [], "closed_profit": 0}

def save_paper(data):
    with open(PAPER_FILE, "w") as f:
        json.dump(data, f, indent=2)

def paper_buy(ticker, entry, stop, target, conf, sector):
    data = load_paper()
    if data["balance"] < 10:
        return "Balance low!"
    shares = 10 / entry
    trade = {
        "ticker": ticker, "sector": sector, "entry": entry,
        "stop": stop, "target": target, "conf": conf,
        "shares": round(shares,4), "invested": 10,
        "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "OPEN"
    }
    data["trades"].append(trade)
    data["balance"] -= 10
    save_paper(data)
    return f"BOUGHT {ticker} @ ${entry} | $10 | CONF {conf}%"

def paper_status():
    data = load_paper()
    out = []
    total_open = 0
    for t in data["trades"]:
        if t["status"] != "OPEN":
            continue
        try:
            live = float(yf.Ticker(t["ticker"]).history(period="1d")["Close"].iloc[-1])
            pnl_pct = (live - t["entry"])/t["entry"]*100
            pnl_usd = (live - t["entry"])*t["shares"]
            total_open += pnl_usd
            icon = "🟢"
            if live <= t["stop"]:
                t["status"] = "STOPPED"
                data["balance"] += t["invested"] + pnl_usd
                data["closed_profit"] += pnl_usd
                icon = "🔴 STOP"
            elif live >= t["target"]:
                t["status"] = "TARGET"
                data["balance"] += t["invested"] + pnl_usd
                data["closed_profit"] += pnl_usd
                icon = "🎯 TARGET!"
            out.append(f"{icon} {t['ticker']} ${t['entry']}->{round(live,2)} {round(pnl_pct,2)}% (${round(pnl_usd,2)})")
        except:
            out.append(f"⚠️ {t['ticker']} fail")
    save_paper(data)
    header = f"Bal: ${round(data['balance'],2)} | Closed: ${round(data['closed_profit'],2)} | Open: ${round(total_open,2)}\n"
    return header + "\n".join(out) if out else header + "No trades yet"

@app.route('/')
def home():
    return "BEAST V10 ALIVE - Go to /paper"

@app.route('/paper')
def paper_route():
    return paper_status().replace("\n","<br>")

@app.route('/buy/<ticker>')
def buy_route(ticker):
    from flask import request
    entry = float(request.args.get("entry", 33.59))
    stop = float(request.args.get("stop", 32.29))
    target = float(request.args.get("target", 34.95))
    conf = int(request.args.get("conf", 80))
    sector = request.args.get("sector", "AI")
    return paper_buy(ticker.upper(), entry, stop, target, conf, sector)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
