"""
BEAST SIGNALS - V7 FINAL
Engine: V15.5 evidence-based (users see V7 only)
SQLite ONLY | Manual research only | No auto-trading
Render start: python -u beast_alerts_bot.py
Env: BOT_TOKEN, ADMIN_ID
Optional: PORT, DB_PATH, TWELVE_DATA_API_KEY
"""

import os, re, asyncio, logging, sqlite3, threading, math, time, json, urllib.parse, urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.error import RetryAfter, BadRequest, Forbidden, TimedOut, NetworkError
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN","").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID","0") or 0)
# --- BEAST V7 PAYMENTS - HARDCODED ---
MTN = "0792878628"
AIRTEL = "0756484267"
WALLET = "TSUMnwA7UWpuHAKQ4Jgd9oyusa3fBaSQQD"
PORT = int(os.environ.get("PORT","10000"))
DB_PATH = os.environ.get("DB_PATH","beast_v7.sqlite3").strip()
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY","").strip()
if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN missing")

VERSION = "V7"
SCAN_INTERVAL_SECONDS = 60
TWELVE_DATA_REFRESH_SECONDS = 15*60
YAHOO_CACHE_SECONDS = 5*60
HUNTER_COOLDOWN_SECONDS = 6*3600
HUNTER_PENDING_LEASE_SECONDS = 10*60
MAX_TELEGRAM_LENGTH = 3800
MAX_MARKET_DATA_AGE_DAYS = 7
STOP_ATR_MULTIPLE = 1.5
REWARD_RISK_RATIO = 2.0
RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0
REVERSAL_RECENT_BARS = 3
SWING_LOOKBACK_BARS = 20
MAX_STOP_PERCENT_WARNING = 8.0
SIGNAL_DELETE_SECONDS = 600

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("BEAST_V7")
_known_users=set(); _known_users_lock=threading.Lock()
_market_cache={}; _market_lock=threading.Lock()
_hunter_cycle_lock=asyncio.Lock()

PLANS = {
    "starter": {"label":"Starter","price":35,"days":7,"signals":2,"market_choice":False,"premium":False,"gold":False},
    "pro": {"label":"Pro","price":75,"days":15,"signals":3,"market_choice":False,"premium":False,"gold":False},
    "premium": {"label":"Premium","price":150,"days":35,"signals":4,"market_choice":False,"premium":True,"gold":False},
    "gold": {"label":"Gold","price":250,"days":40,"signals":5,"market_choice":True,"premium":True,"gold":True},
}
UNIVERSE = ["BTC-USD","ETH-USD","SOL-USD","GC=F","SI=F","EURUSD=X","GBPUSD=X","USDJPY=X","SPY","QQQ","TLT","AAPL","NVDA","CL=F"]
MARKET_GROUPS = {
    "crypto": ["BTC-USD","ETH-USD","SOL-USD"],
    "gold": ["GC=F","SI=F","EURUSD=X","GBPUSD=X","USDJPY=X"],
    "stocks": ["SPY","QQQ","TLT","AAPL","NVDA"],
    "mix": UNIVERSE,
}
MARKET_LABEL = {"crypto":"Crypto","gold":"Gold + Forex","stocks":"Stocks + Bonds","mix":"Mix / Inter-Market"}

CAUTION = "⚠️ Caution: Market is highly volatile. Trade responsibly. Risk 1% only, use stop-loss. Not financial advice."
DISCIPLINE_NOTE = "✅ You've got your best signals today.\n🔥 Pros don't trade more, they trade better. Discipline is profit. 👑"
NO_TRADE_NOTE = "⏸️ No clean trade today. I won't give you a bad trade just to fill quota.\n💎 Not trading is also a trade."
INVALID_NOTE = "⚠️ Market is highly volatile. This signal is now invalid. Trade responsibly — protect your capital, don't chase. 🛡️"
BEAST_RULES = "👑 BEAST 5 RULES 🔥\n1. 💰 Risk 1% only — never all-in\n2. 🛡️ Always use the stop — no 'it will come back'\n3. 🎯 Max trades = your plan — greed kills accounts\n4. 🧠 Never trade angry / after a loss — walk away\n5. 🏆 If you win, stop. If you lose 2 in a row, stop."

def utc_now(): return datetime.now(timezone.utc)
def utc_iso(): return utc_now().isoformat()
def today_str(): return date.today().isoformat()
def local_time(value=None):
    if not value: dt=utc_now()
    else:
        try:
            dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        except: dt=utc_now()
    try: dt=dt.astimezone(ZoneInfo("Africa/Kampala"))
    except: dt=dt.astimezone(timezone(timedelta(hours=3)))
    return dt.strftime("%d %b %Y, %H:%M EAT")
def finite_number(v):
    try: return math.isfinite(float(v))
    except: return False
def validate_number(v):
    if not finite_number(v): raise ValueError("Invalid number")
    return float(v)

flask_app = Flask(__name__)
@flask_app.get("/")
def home(): return f"BEAST SIGNALS {VERSION} - MANUAL ONLY - SQLITE",200
@flask_app.get("/health")
def health():
    try:
        with db() as c: c.execute("SELECT 1").fetchone()
        return jsonify({"status":"ok","version":VERSION,"db_ok":True}),200
    except Exception: return jsonify({"status":"unhealthy"}),503
def run_flask(): flask_app.run(host="0.0.0.0",port=PORT,use_reloader=False)

@contextmanager
def db():
    parent=os.path.dirname(DB_PATH)
    if parent: os.makedirs(parent,exist_ok=True)
    conn=sqlite3.connect(DB_PATH,timeout=30,check_same_thread=False)
    conn.row_factory=sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000"); conn.execute("PRAGMA journal_mode=WAL"); yield conn; conn.commit()
    except: conn.rollback(); raise
    finally: conn.close()

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(uid INTEGER PRIMARY KEY, alert_enabled INTEGER DEFAULT 0, market_pref TEXT DEFAULT 'mix', created_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS paid(uid INTEGER PRIMARY KEY, plan TEXT, expiry TEXT, created_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS trial(uid INTEGER PRIMARY KEY, used INTEGER DEFAULT 0, used_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS daily_signals(uid INTEGER, day TEXT, count INTEGER DEFAULT 0, PRIMARY KEY(uid,day))""")
        c.execute("""CREATE TABLE IF NOT EXISTS alert_stats(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, symbol TEXT, decision TEXT, price REAL, status TEXT DEFAULT 'generated', created_at TEXT, updated_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS hunter_state(ticker TEXT, direction TEXT, price REAL, sent_at TEXT, PRIMARY KEY(ticker,direction))""")
        c.execute("""CREATE TABLE IF NOT EXISTS hunter_deliveries(uid INTEGER, ticker TEXT, direction TEXT, status TEXT, price REAL, claimed_at TEXT, sent_at TEXT, alert_id INTEGER, PRIMARY KEY(uid,ticker,direction))""")
        c.execute("""CREATE TABLE IF NOT EXISTS hunter_messages(uid INTEGER, ticker TEXT, chat_id INTEGER, message_id INTEGER, direction TEXT, updated_at TEXT, last_signal_at TEXT, PRIMARY KEY(uid,ticker))""")
        c.execute("""CREATE TABLE IF NOT EXISTS trade_log(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, symbol TEXT, side TEXT, quantity REAL, entry_price REAL, logged_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS pending_pay(uid INTEGER PRIMARY KEY, txn TEXT, plan TEXT, created_at TEXT)""")
    logger.info("DB init V7")

def ensure_user(uid):
    uid=int(uid)
    with _known_users_lock:
        if uid in _known_users: return
    with db() as c: c.execute("INSERT OR IGNORE INTO users(uid,created_at) VALUES(?,?)",(uid,utc_iso()))
    with _known_users_lock: _known_users.add(uid)
def get_user(uid):
    ensure_user(uid)
    with db() as c:
        r=c.execute("SELECT * FROM users WHERE uid=?",(int(uid),)).fetchone(); return dict(r)
def update_user(uid,field,val):
    if field not in ("alert_enabled","market_pref"): raise ValueError("bad field")
    ensure_user(uid)
    with db() as c: c.execute(f"UPDATE users SET {field}=? WHERE uid=?",(val,int(uid)))
def get_paid(uid):
    with db() as c:
        r=c.execute("SELECT * FROM paid WHERE uid=?",(int(uid),)).fetchone()
        if not r: return None
        d=dict(r)
        try:
            if datetime.strptime(d["expiry"],"%Y-%m-%d").date() < date.today(): return None
        except: pass
        return d
def is_paid(uid):
    if int(uid)==ADMIN_ID: return True
    return get_paid(uid) is not None
def get_plan_for(uid):
    if int(uid)==ADMIN_ID: return PLANS["gold"]
    p=get_paid(uid)
    if not p: return None
    return PLANS.get(p["plan"], PLANS["pro"])
def is_trial_used(uid):
    with db() as c:
        r=c.execute("SELECT used FROM trial WHERE uid=?",(int(uid),)).fetchone()
        return bool(r and r["used"])
def mark_trial_used(uid):
    with db() as c: c.execute("INSERT OR REPLACE INTO trial(uid,used,used_at) VALUES(?,?,?)",(int(uid),1,utc_iso()))
def get_daily_count(uid):
    with db() as c:
        r=c.execute("SELECT count FROM daily_signals WHERE uid=? AND day=?",(int(uid),today_str())).fetchone()
        return int(r["count"]) if r else 0
def inc_daily_count(uid):
    with db() as c: c.execute("INSERT INTO daily_signals(uid,day,count) VALUES(?,?,1) ON CONFLICT(uid,day) DO UPDATE SET count=count+1",(int(uid),today_str()))
def add_trade(uid,symbol,side,qty,price):
    qty=validate_number(qty); price=validate_number(price)
    if side not in ("BUY","SELL"): raise ValueError()
    ensure_user(uid)
    with db() as c: c.execute("INSERT INTO trade_log(uid,symbol,side,quantity,entry_price,logged_at) VALUES(?,?,?,?,?,?)",(int(uid),symbol,side,qty,price,utc_iso()))
def get_trades(uid,limit=20):
    with db() as c: return c.execute("SELECT * FROM trade_log WHERE uid=? ORDER BY id DESC LIMIT ?",(int(uid),int(limit))).fetchall()

def normalize_ohlcv(frame):
    if frame is None or frame.empty: return None
    wanted={"OPEN","HIGH","LOW","CLOSE","VOLUME"}
    if not isinstance(frame.columns,pd.MultiIndex):
        r=frame.copy(); r.columns=[str(c).strip().upper() for c in r.columns]; return r
    result={}
    for i,col in enumerate(frame.columns):
        for part in (col if isinstance(col,tuple) else (col,)):
            n=str(part).strip().upper()
            if n in wanted and n not in result: result[n]=frame.iloc[:,i]; break
    return pd.DataFrame(result,index=frame.index) if result else None
def fetch_twelve_data(ticker):
    if not TWELVE_DATA_API_KEY: return None,"No key"
    symbol="BTC/USD" if ticker=="BTC-USD" else ticker
    now=time.time(); key=("twelve",ticker)
    with _market_lock:
        cached=_market_cache.get(key)
        if cached and now-cached["fetched_at"]<TWELVE_DATA_REFRESH_SECONDS: return cached["frame"].copy(),None
    params=urllib.parse.urlencode({"symbol":symbol,"interval":"1day","outputsize":"250","apikey":TWELVE_DATA_API_KEY,"format":"JSON"})
    try:
        req=urllib.request.Request("https://api.twelvedata.com/time_series?"+params,headers={"User-Agent":"BEAST-V7"})
        with urllib.request.urlopen(req,timeout=15) as resp: payload=json.loads(resp.read().decode())
        if payload.get("status")=="error" or "values" not in payload: return None,str(payload.get("message","No values"))
        frame=pd.DataFrame(payload["values"]); frame["datetime"]=pd.to_datetime(frame["datetime"],utc=True,errors="coerce")
        frame=frame.dropna(subset=["datetime"]).set_index("datetime")
        for col in ("open","high","low","close","volume"):
            if col not in frame.columns: frame[col]=0.0 if col=="volume" else float("nan")
        frame=frame.rename(columns={"open":"OPEN","high":"HIGH","low":"LOW","close":"CLOSE","volume":"VOLUME"})
        for col in ("OPEN","HIGH","LOW","CLOSE","VOLUME"): frame[col]=pd.to_numeric(frame[col],errors="coerce")
        frame=frame.sort_index()
        if frame.empty: return None,"No candles"
        with _market_lock: _market_cache[key]={"frame":frame.copy(),"fetched_at":now}
        return frame,None
    except Exception as e: return None,str(e)
def fetch_yahoo(ticker):
    now=time.time(); key=("yahoo",ticker)
    with _market_lock:
        cached=_market_cache.get(key)
        if cached and now-cached["fetched_at"]<YAHOO_CACHE_SECONDS: return cached["frame"].copy()
    for delay,period in ((0,"3y"),(2,"18mo")):
        if delay: time.sleep(delay)
        try:
            frame=yf.download(ticker,period=period,interval="1d",progress=False,auto_adjust=True,threads=False,timeout=15)
            if frame is not None and not frame.empty:
                n=normalize_ohlcv(frame)
                if n is not None and not n.empty:
                    with _market_lock: _market_cache[key]={"frame":n.copy(),"fetched_at":time.time()}
                    return n
        except Exception as e: logger.warning("Yahoo %s %s",ticker,e)
    return None
def fetch_market_data(ticker):
    if ticker in ("BTC-USD","TLT"):
        f,e=fetch_twelve_data(ticker)
        if f is not None: return f,"Twelve Data",None
        y=fetch_yahoo(ticker)
        if y is not None: return y,"Yahoo fallback",e
        return None,"Unavailable",e or "Both failed"
    y=fetch_yahoo(ticker)
    if y is not None: return y,"Yahoo Finance",None
    return None,"Unavailable","Yahoo no data"
def rsi_calc(series,period=14):
    delta=series.diff(); gain=delta.clip(lower=0); loss=-delta.clip(upper=0)
    ag=gain.ewm(alpha=1/period,adjust=False,min_periods=period).mean(); al=loss.ewm(alpha=1/period,adjust=False,min_periods=period).mean()
    rs=ag/al.replace(0,float("nan")); res=100-(100/(1+rs)); res=res.mask((al==0)&(ag>0),100); res=res.mask((ag==0)&(al>0),0); return res
def calc_indicators(frame,ticker):
    frame=normalize_ohlcv(frame)
    if frame is None or frame.empty: return None,"Missing data"
    if not {"HIGH","LOW","CLOSE"}.issubset(frame.columns): return None,"Missing price"
    close=pd.to_numeric(frame["CLOSE"],errors="coerce").dropna()
    if len(close)<50: return None,"Not enough history"
    high=pd.to_numeric(frame["HIGH"],errors="coerce").reindex(close.index); low=pd.to_numeric(frame["LOW"],errors="coerce").reindex(close.index)
    if high.isna().any() or low.isna().any() or (close<=0).any(): return None,"Invalid price"
    try:
        latest=pd.Timestamp(close.index[-1]); latest=latest.tz_localize("UTC") if latest.tzinfo is None else latest.tz_convert("UTC")
        age=(utc_now()-latest.to_pydatetime()).total_seconds()/86400
        if age>MAX_MARKET_DATA_AGE_DAYS: return None,f"Stale {age:.1f}d"
    except: return None,"Time check fail"
    n=len(close); ema200_state="calculated" if n>=400 else "provisional" if n>=210 else "unavailable"
    ema20=close.ewm(span=20,adjust=False).mean(); ema50=close.ewm(span=50,adjust=False).mean(); ema200=close.ewm(span=200,adjust=False).mean()
    ema200_v=ema200.iloc[-1] if ema200_state!="unavailable" else float("nan")
    macd=close.ewm(span=12,adjust=False).mean()-close.ewm(span=26,adjust=False).mean(); ms=macd.ewm(span=9,adjust=False).mean(); hist=macd-ms
    prev=close.shift(1); tr=pd.concat([(high-low).abs(),(high-prev).abs(),(low-prev).abs()],axis=1).max(axis=1); atr=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rs=rsi_calc(close); prior_high=high.shift(1).rolling(SWING_LOOKBACK_BARS).max(); prior_low=low.shift(1).rolling(SWING_LOOKBACK_BARS).min()
    is_fx=ticker.endswith("=X")
    if "VOLUME" not in frame.columns: vol_state="missing"; vol=pd.Series(0.0,index=close.index)
    else:
        vol=pd.to_numeric(frame["VOLUME"],errors="coerce").reindex(close.index)
        if vol.isna().all() or ((vol.fillna(0)==0).all() and is_fx): vol_state="missing"; vol=pd.Series(0.0,index=close.index)
        elif (vol.fillna(0)==0).all(): vol_state="zero"
        else: vol_state="normal"; vol=vol.fillna(0)
    avg_vol=vol.rolling(20).mean(); vol_ratio=float(vol.iloc[-1]/avg_vol.iloc[-1]) if pd.notna(avg_vol.iloc[-1]) and avg_vol.iloc[-1]>0 else 0.0
    vals={"price":close.iloc[-1],"previous_price":close.iloc[-2],"rsi":rs.iloc[-1],"rsi_prev":rs.iloc[-2],"rsi_low_recent":rs.tail(REVERSAL_RECENT_BARS).min(),"rsi_high_recent":rs.tail(REVERSAL_RECENT_BARS).max(),"ema20":ema20.iloc[-1],"ema20_prev":ema20.iloc[-2],"prior_swing_high":prior_high.iloc[-1],"prior_swing_low":prior_low.iloc[-1],"ema50":ema50.iloc[-1],"ema200":ema200_v,"macd":macd.iloc[-1],"macd_signal":ms.iloc[-1],"macd_hist":hist.iloc[-1],"macd_hist_prev":hist.iloc[-2],"atr":atr.iloc[-1],"vol":vol.iloc[-1],"vol_ratio":vol_ratio,"high":high.tail(20).max(),"low":low.tail(20).min()}
    for k,v in vals.items():
        if k=="ema200" and ema200_state=="unavailable": continue
        if not finite_number(v): return None,f"Invalid {k}"
    if vals["price"]<=0 or vals["atr"]<=0: return None,"Invalid price/ATR"
    res={k:float(v) for k,v in vals.items() if k!="ema200" or ema200_state!="unavailable"}
    res["ema200"]=float(ema200_v) if ema200_state!="unavailable" else float("nan")
    res.update({"ema200_state":ema200_state,"volume_state":vol_state,"bars":n,"age_days":age,"candle_time":latest.isoformat()})
    return res,None
def promptguard_check(d):
    if d["price"]<=0 or d["atr"]<=0: return False,"Blocked: invalid price/ATR"
    if d["volume_state"]=="zero": return False,"Blocked: zero volume"
    if d["volume_state"]=="missing": return True,"Pass: volume unavailable"
    return True,"Pass"
def multipath_vote(d):
    votes={"BUY":0,"SELL":0,"HOLD":0}
    if d["ema200_state"]!="unavailable":
        if d["price"]>d["ema50"] and d["ema20"]>d["ema50"] and d["price"]>d["ema200"]: votes["BUY"]+=1
        elif d["price"]<d["ema50"] and d["ema20"]<d["ema50"] and d["price"]<d["ema200"]: votes["SELL"]+=1
        else: votes["HOLD"]+=1
    else:
        if d["price"]>d["ema50"] and d["ema20"]>d["ema50"]: votes["BUY"]+=1
        elif d["price"]<d["ema50"] and d["ema20"]<d["ema50"]: votes["SELL"]+=1
        else: votes["HOLD"]+=1
    if d["rsi"]>=50 and d["macd_hist"]>0: votes["BUY"]+=1
    elif d["rsi"]<50 and d["macd_hist"]<0: votes["SELL"]+=1
    else: votes["HOLD"]+=1
    if d["volume_state"]=="missing": votes["HOLD"]+=1
    elif d["vol_ratio"]>=1 and d["macd_hist"]>0: votes["BUY"]+=1
    elif d["vol_ratio"]>=1 and d["macd_hist"]<0: votes["SELL"]+=1
    else: votes["HOLD"]+=1
    return votes
def classify_signal(d,votes):
    rsi_v=d["rsi"]; rsi_p=d["rsi_prev"]; hist=d["macd_hist"]; hist_p=d["macd_hist_prev"]; price=d["price"]; prev_p=d["previous_price"]; ema20=d["ema20"]; ema20_p=d["ema20_prev"]
    bull_mom=rsi_v>rsi_p and hist>hist_p; bear_mom=rsi_v<rsi_p and hist<hist_p
    bull_reclaim=price>ema20 and prev_p<=ema20_p; bear_loss=price<ema20 and prev_p>=ema20_p
    bull_break=finite_number(d.get("prior_swing_high")) and price>d["prior_swing_high"]; bear_break=finite_number(d.get("prior_swing_low")) and price<d["prior_swing_low"]
    bull_struct=bull_reclaim or bull_break; bear_struct=bear_loss or bear_break
    recently_oversold=finite_number(d.get("rsi_low_recent")) and d["rsi_low_recent"]<=RSI_OVERSOLD
    recently_overbought=finite_number(d.get("rsi_high_recent")) and d["rsi_high_recent"]>=RSI_OVERBOUGHT
    if recently_oversold:
        if bull_mom and bull_struct:
            ev=[]
            if bull_reclaim: ev.append("reclaimed EMA20")
            if bull_break: ev.append("broke prior swing high")
            return "REVERSAL WATCH","Bullish momentum improving + "+" + ".join(ev)+". Wait for confirmation; no BUY yet."
        miss=[]
        if not bull_mom: miss.append("momentum improvement")
        if not bull_struct: miss.append("price confirmation")
        return "OVERSOLD","Selling extended. Do not chase SELL. Waiting for "+" and ".join(miss)+"."
    if recently_overbought:
        if bear_mom and bear_struct:
            ev=[]
            if bear_loss: ev.append("lost EMA20")
            if bear_break: ev.append("broke prior swing low")
            return "REVERSAL WATCH","Bearish momentum weakening + "+" + ".join(ev)+". Wait for confirmation; no SELL yet."
        miss=[]
        if not bear_mom: miss.append("momentum weakening")
        if not bear_struct: miss.append("price confirmation")
        return "OVERBOUGHT","Buying extended. Do not chase BUY. Waiting for "+" and ".join(miss)+"."
    mx=max(votes.values())
    if mx<2 or votes["HOLD"]==mx: return "HOLD","No clear entry. Wait for stronger agreement."
    winner="BUY" if votes["BUY"]>votes["SELL"] else "SELL"
    if winner=="BUY" and votes["BUY"]<2: return "HOLD","Bullish evidence not strong enough."
    if winner=="SELL" and votes["SELL"]<2: return "HOLD","Bearish evidence not strong enough."
    return winner,"Trend and entry checks agree. Confirm broker price and risk."
def analyze_ticker(ticker):
    try:
        frame,source,ferr=fetch_market_data(ticker)
        data,err=calc_indicators(frame,ticker)
        if data is None: return {"ticker":ticker,"blocked":True,"block_reason":err or ferr or "No data","d":None,"votes":None,"winner":"BLOCKED","shadow":"BLOCK","pg":"BLOCK","entry":None,"stop":None,"target":None,"source":source,"analysis_time":utc_iso()}
        ok,msg=promptguard_check(data); votes=multipath_vote(data); signal,reason=classify_signal(data,votes)
        if not ok: return {"ticker":ticker,"blocked":True,"block_reason":msg,"d":data,"votes":votes,"winner":"BLOCKED","shadow":"BLOCK","shadow_msg":msg,"pg":msg,"signal_reason":reason,"entry":None,"stop":None,"target":None,"source":source,"analysis_time":utc_iso()}
        entry=stop=target=None
        if signal in ("BUY","SELL"):
            entry=data["price"]; risk=STOP_ATR_MULTIPLE*data["atr"]
            if signal=="BUY": stop=entry-risk; target=entry+REWARD_RISK_RATIO*risk
            else: stop=entry+risk; target=entry-REWARD_RISK_RATIO*risk
            if not all(finite_number(x) for x in (entry,stop,target)) or entry<=0 or stop<=0 or target<=0:
                return {"ticker":ticker,"blocked":True,"block_reason":"Invalid entry/stop/target","d":data,"votes":votes,"winner":"BLOCKED","shadow":"BLOCK","shadow_msg":"Risk check failed","pg":msg,"entry":None,"stop":None,"target":None,"source":source,"analysis_time":utc_iso()}
        return {"ticker":ticker,"blocked":False,"block_reason":None,"d":data,"votes":votes,"winner":signal,"shadow":"PASS" if signal in ("BUY","SELL") else "WATCH","shadow_msg":reason,"pg":msg,"signal_reason":reason,"entry":entry,"stop":stop,"target":target,"source":source,"analysis_time":utc_iso()}
    except Exception:
        logger.exception("Analysis fail %s",ticker)
        return {"ticker":ticker,"blocked":True,"block_reason":"Analysis error","d":None,"votes":None,"winner":"BLOCKED","shadow":"BLOCK","pg":"BLOCK","entry":None,"stop":None,"target":None,"source":"Unavailable","analysis_time":utc_iso()}

# ---------- FORMATTING WITH PERSUASIVE EMOJIS ----------
def pay_text():
    return (
        "🔥💰 BEAST V7 — PAY / VIP 👑🚀\n"
        "━━━━━━━━━━━━━━━\n"
        "💳 Pay in 30 seconds:\n"
        f"📱 MTN: {MTN}\n"
        f"📱 Airtel: {AIRTEL}\n"
        f"💎 TRC20 (USDT): {WALLET}\n"
        "━━━━━━━━━━━━━━━\n"
        "🔥 CHOOSE YOUR POWER:\n"
        "💚 $35 = 7 days, 2/day — Starter 🌱\n"
        "   Perfect to start, taste the BEAST 💪\n"
        "💙 $75 = 15 days, 3/day — Pro ⭐🔥 MOST POPULAR\n"
        "   3 best trades daily, serious traders choose this 🚀\n"
        "💜 $150 = 35 days, 4/day + PREMIUM 👑💎\n"
        "   ⚡ Early drop + 📊 Weekly Review + 💰 Risk Guide\n"
        "   Don't miss the early move!\n"
        "🥇 $250 = 40 days, 5/day + GOLD 🏆👑💎\n"
        "   🎯 Choose YOUR Market + 🌐 Inter-Market + 🤝 1-on-1\n"
        "   The full BEAST experience — for winners only 🏆\n"
        "━━━━━━━━━━━━━━━\n"
        "✅ 1. Send payment to one method above\n"
        "✅ 2. Send /paid TXN + Plan\n"
        "   Example: /paid TXN123 Pro\n"
        "⚡ After we confirm, you unlock instantly!\n"
        "⏳ Don't wait — tomorrow's best trade won't wait for you! 🔥\n\n"
        + CAUTION
    )
def format_signal(result, idx, total, is_gold_inter=False):
    d=result["d"]; action=result["winner"]
    emoji="🟢🚀" if action=="BUY" else "🔴📉"
    ema200_txt="N/A" if d["ema200_state"]=="unavailable" else f"{d['ema200']:.5g}"+(" (prov.)" if d["ema200_state"]=="provisional" else "")
    vol_txt="Unavailable" if d["volume_state"]=="missing" else f"{d['vol_ratio']:.2f}x"
    dist=abs(result["entry"]-result["stop"]); pct=dist/result["entry"]*100
    warn=" Review size." if pct>MAX_STOP_PERCENT_WARNING else ""
    inter_line="\n🌐💎 INTER-MARKET: Gold + Dollar + Stocks agree — extra confirmation 🔥" if is_gold_inter else ""
    lines=[
        f"🔥👑 BEAST V7 — Signal {idx}/{total} 💎",
        f"{emoji} {action} {result['ticker']} | Daily candles{inter_line}",
        f"🎯 Reason: {result.get('signal_reason','')}",
        f"💰 Entry: {result['entry']:.5g}",
        f"🛡️ Stop: {result['stop']:.5g} ({pct:.2f}% away).{warn}",
        f"🏆 Target: {result['target']:.5g} | 1.5 ATR stop / 2:1 target 🚀",
        f"📊 Price: {d['price']:.5g} | RSI: {d['rsi']:.1f} | EMA 20/50/200: {d['ema20']:.5g} / {d['ema50']:.5g} / {ema200_txt}",
        f"📈 MACD hist: {d['macd_hist']:.5g} | ATR: {d['atr']:.5g} | Vol: {vol_txt}",
        f"✅ Evidence: {result['votes']} | Provider: {result.get('source')}",
        f"🕒 Checked: {local_time(result.get('analysis_time'))}",
        "",
        CAUTION,
        "⏳⚡ This signal will disappear in 10 minutes. Save it now! 📸",
    ]
    return "\n".join(lines)

def kb_back_home():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data="home"),InlineKeyboardButton("🏠 Home",callback_data="home")]])
def kb_welcome(is_paid_user, trial_used):
    rows=[]
    if is_paid_user: rows.append([InlineKeyboardButton("📊🔥 Today's Signals",callback_data="today")])
    elif not trial_used: rows.append([InlineKeyboardButton("🎁🔥 Get My 1 FREE Signal",callback_data="free")])
    rows.append([InlineKeyboardButton("💰👑 PAY / VIP — Unlock Now 🚀",callback_data="pay")])
    rows.append([InlineKeyboardButton("📖👑 BEAST 5 RULES",callback_data="rules")])
    rows.append([InlineKeyboardButton("🏠 Home",callback_data="home")])
    return InlineKeyboardMarkup(rows)
def kb_after_signal():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📊🔥 Today's Signals",callback_data="today")],[InlineKeyboardButton("🔙 Back",callback_data="home"),InlineKeyboardButton("🏠 Home",callback_data="home")]])
def kb_pay():
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅💰 I Have Paid — Send /paid 🚀",callback_data="how_paid")],[InlineKeyboardButton("🔙 Back",callback_data="home"),InlineKeyboardButton("🏠 Home",callback_data="home")]])
def kb_market():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("₿ Crypto 🚀",callback_data="market_crypto"),InlineKeyboardButton("🥇 Gold + Forex 💎",callback_data="market_gold")],
        [InlineKeyboardButton("📈 Stocks + Bonds 🏆",callback_data="market_stocks"),InlineKeyboardButton("🌐 Mix / Inter-Market 🔥",callback_data="market_mix")],
        [InlineKeyboardButton("🔙 Back",callback_data="home"),InlineKeyboardButton("🏠 Home",callback_data="home")],
    ])

async def retry_delay(exc):
    d=exc.retry_after
    if hasattr(d,"total_seconds"): d=d.total_seconds()
    await asyncio.sleep(float(d)+1)
async def safe_send(bot,chat_id,text,reply_markup=None, delete_after=False):
    text=text or "No message."
    chunks=[]; cur=""
    for line in text.split("\n"):
        while len(line)>MAX_TELEGRAM_LENGTH:
            if cur: chunks.append(cur); cur=""
            chunks.append(line[:MAX_TELEGRAM_LENGTH]); line=line[MAX_TELEGRAM_LENGTH:]
        cand=cur+"\n"+line if cur else line
        if len(cand)>MAX_TELEGRAM_LENGTH:
            if cur: chunks.append(cur)
            cur=line
        else: cur=cand
    if cur: chunks.append(cur)
    sent=None
    for i,chunk in enumerate(chunks):
        while True:
            try:
                sent=await bot.send_message(chat_id=chat_id,text=chunk,reply_markup=reply_markup if i==len(chunks)-1 else None)
                break
            except RetryAfter as e: await retry_delay(e)
    if delete_after and sent:
        async def _del():
            await asyncio.sleep(SIGNAL_DELETE_SECONDS)
            try: await bot.delete_message(chat_id=sent.chat_id,message_id=sent.message_id)
            except: pass
        asyncio.create_task(_del())
    return sent
async def safe_reply(msg,text,reply_markup=None,delete_after=False): return await safe_send(msg.get_bot(),msg.chat_id,text,reply_markup,delete_after)
async def safe_edit(q,text,reply_markup=None):
    try: await q.edit_message_text(text=text[:MAX_TELEGRAM_LENGTH],reply_markup=reply_markup); return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower(): return True
        return False
    except: return False

async def pick_signals(uid, limit):
    plan=get_plan_for(uid)
    pref="mix"
    if plan and plan.get("market_choice"): pref=get_user(uid).get("market_pref","mix")
    universe=MARKET_GROUPS.get(pref, UNIVERSE)
    results=[]
    for ticker in universe:
        r=await asyncio.to_thread(analyze_ticker,ticker)
        if not r.get("blocked") and r["winner"] in ("BUY","SELL") and r["entry"] and r["stop"] and r["target"]:
            score=r["votes"].get(r["winner"],0)
            results.append((score,ticker,r))
        if len(results)>=limit: break
    results.sort(key=lambda x: -x[0])
    return results[:limit], pref

async def send_signals_for(uid, message_or_query, is_free=False):
    if is_free: limit=1
    else:
        plan=get_plan_for(uid)
        if not plan: return False
        limit=plan["signals"]
        already=get_daily_count(uid)
        if already>=limit:
            txt=f"{DISCIPLINE_NOTE}\n\nYou already got your {limit} best signals today. Come back tomorrow. 🚀\n\n{CAUTION}"
            if hasattr(message_or_query,"message"): await safe_edit(message_or_query,txt,kb_back_home())
            else: await safe_reply(message_or_query,txt,kb_back_home())
            return False
        limit=limit-already
    bot = message_or_query.get_bot() if hasattr(message_or_query,"get_bot") else message_or_query.bot
    chat_id = message_or_query.chat_id if hasattr(message_or_query,"chat_id") else message_or_query.message.chat_id
    await safe_send(bot,chat_id,"⏳🔥 BEAST is scanning for your best signals... No chasing, only clean trades. 💎",kb_back_home())
    picked, pref = await pick_signals(uid, limit)
    if not picked:
        txt=f"{NO_TRADE_NOTE}\n\n{CAUTION}"
        await safe_send(bot,chat_id,txt,kb_back_home())
        return False
    for i,(score,ticker,r) in enumerate(picked):
        plan=get_plan_for(uid)
        is_inter = bool(plan and plan.get("gold") and i==0)
        if is_free: idx=1; total=1
        else:
            already=get_daily_count(uid); idx=already+1; total=plan["signals"]
        text=format_signal(r, idx, total, is_gold_inter=is_inter)
        if plan and plan.get("premium"):
            text+=f"\n\n💰 Risk guide: $100 acct → risk $1 | $500 → $5 | $1000 → $10 on this trade. 🛡️"
        await safe_send(bot,chat_id,text,kb_after_signal(),delete_after=True)
        if not is_free: inc_daily_count(uid)
        else: mark_trial_used(uid)
    if not is_free:
        remaining=get_plan_for(uid)["signals"]-get_daily_count(uid)
        if remaining<=0:
            await safe_send(bot,chat_id,f"{DISCIPLINE_NOTE}\n\n{CAUTION}",kb_back_home())
    return True

async def setup_commands(app):
    await app.bot.set_my_commands([BotCommand("start","Open BEAST V7 🔥"),BotCommand("rules","BEAST 5 RULES 👑"),BotCommand("market","Gold: Choose market 🎯"),BotCommand("ask","Gold: 1-on-1 check-in 🤝"),BotCommand("paid","Send payment proof 💰"),BotCommand("history","Your trade log 📊")])

async def start_cmd(update,context):
    uid=update.effective_user.id; ensure_user(uid)
    paid=is_paid(uid); trial_used=is_trial_used(uid)
    if paid:
        plan=get_plan_for(uid); exp=get_paid(uid)
        exp_txt=f" until {exp['expiry']}" if exp else ""
        txt=f"👑🔥 Welcome to BEAST SIGNALS 👑🔥\n💎 BEAST V7 — You are {plan['label']}{exp_txt} 🏆\n\n🚀 Tap Today's Signals for your best trades today.\n💰 Don't waste a day — every day has a winner!\n\n{CAUTION}"
    elif not trial_used:
        txt=f"👑🔥 Welcome to BEAST SIGNALS 👑🔥\n💎 BEAST V7\n\n🎁🔥 You get 1 FREE signal — bot picks the BEST trade.\nNo scanning, no chasing. Just clean trades.\n\n👇 Tap below to claim it before it's gone!\n\n{CAUTION}"
    else:
        txt=f"👑🔥 Welcome to BEAST SIGNALS 👑🔥\n💎 BEAST V7\n\n😱 Your 1 FREE signal is used.\n🔥 100s of traders already upgraded — don't be left behind!\n👇 Tap PAY to unlock 2-5 best signals daily 🚀\n\n{CAUTION}"
    await safe_reply(update.message,txt,kb_welcome(paid,trial_used))

async def rules_cmd(update,context):
    await safe_reply(update.message,BEAST_RULES+"\n\n"+CAUTION,kb_back_home())
async def market_cmd(update,context):
    uid=update.effective_user.id
    plan=get_plan_for(uid)
    if not plan or not plan.get("market_choice"):
        await safe_reply(update.message,"🔒🎯 Market choice is GOLD only ($250) 👑\n🚀 Tap PAY to upgrade and choose YOUR market!\n\n"+CAUTION,kb_pay()); return
    if context.args:
        v=context.args[0].lower()
        mapping={"crypto":"crypto","gold":"gold","stocks":"stocks","mix":"mix","forex":"gold"}
        if v in mapping:
            update_user(uid,"market_pref",mapping[v])
            await safe_reply(update.message,f"✅🎯 Market set to {MARKET_LABEL[mapping[v]]} 🔥\nYour next signals will come from that market.\n\n{CAUTION}",kb_back_home()); return
    await safe_reply(update.message,"🥇👑 GOLD — Choose Your Market:\n₿ Crypto / 🥇 Gold+Forex / 📈 Stocks+Bonds / 🌐 Mix\nUse /market crypto, /market gold, /market stocks, /market mix",kb_market())
async def ask_cmd(update,context):
    uid=update.effective_user.id
    plan=get_plan_for(uid)
    if not plan or not plan.get("gold"):
        await safe_reply(update.message,"🔒🤝 1-on-1 check-in is GOLD only 👑\n🚀 Tap PAY to upgrade.",kb_pay()); return
    if not context.args:
        await safe_reply(update.message,"Send your question like:\n/ask Should I hold my Gold trade?\nI will reply personally. 🤝",kb_back_home()); return
    q=" ".join(context.args)
    try: await context.bot.send_message(chat_id=ADMIN_ID,text=f"🥇 GOLD ASK from {uid} (@{update.effective_user.username or 'no username'}):\n{q}")
    except: pass
    await safe_reply(update.message,"✅🤝 Sent to admin. You will get a personal reply. 👑\n\n"+CAUTION,kb_back_home())
async def paid_cmd(update,context):
    uid=update.effective_user.id
    if len(context.args)<2:
        await safe_reply(update.message,"Use: /paid TXN123 Pro\nExample: /paid TXN123 Starter\nPlans: Starter / Pro / Premium / Gold\n\n"+CAUTION,kb_back_home()); return
    txn=context.args[0]; plan_raw=" ".join(context.args[1:]).lower()
    mapping={"starter":"starter","pro":"pro","premium":"premium","gold":"gold","$35":"starter","$75":"pro","$150":"premium","$250":"gold","35":"starter","75":"pro","150":"premium","250":"gold"}
    plan=mapping.get(plan_raw,"pro")
    with db() as c: c.execute("INSERT OR REPLACE INTO pending_pay(uid,txn,plan,created_at) VALUES(?,?,?,?)",(uid,txn,plan,utc_iso()))
    try: await context.bot.send_message(chat_id=ADMIN_ID,text=f"💰 NEW PAYMENT CLAIM 🔥\nUser: {uid} (@{update.effective_user.username or 'no username'})\nTXN: {txn}\nPlan: {PLANS[plan]['label']} ${PLANS[plan]['price']} = {PLANS[plan]['days']} days\nConfirm with: /addpaid {uid} {PLANS[plan]['days']} {plan}")
    except: pass
    await safe_reply(update.message,f"✅💰 Proof received: {txn} — {PLANS[plan]['label']} 🔥\nAdmin will confirm and unlock you for {PLANS[plan]['days']} days. 🚀\nYou will get a message when active.\n\n{CAUTION}",kb_back_home())
async def addpaid_cmd(update,context):
    if update.effective_user.id!=ADMIN_ID: return
    if len(context.args)<2:
        await update.message.reply_text("Use: /addpaid USER_ID DAYS [plan]\nExample: /addpaid 123456 35 premium"); return
    try:
        tid=int(context.args[0]); days=int(context.args[1]); plan=context.args[2].lower() if len(context.args)>=3 else "pro"
        if plan not in PLANS: plan="pro"
        exp=(date.today()+timedelta(days=days)).strftime("%Y-%m-%d")
        with db() as c: c.execute("INSERT OR REPLACE INTO paid(uid,plan,expiry,created_at) VALUES(?,?,?,?)",(tid,plan,exp,utc_iso()))
        await update.message.reply_text(f"✅ {tid} — {PLANS[plan]['label']} until {exp} ({days} days)")
        try: await context.bot.send_message(chat_id=tid,text=f"🎉🔥 BEAST V7 ACTIVATED! 👑\n💎 Plan: {PLANS[plan]['label']} — {days} days until {exp} 🚀\nTap /start → Today's Signals for your best trades. 🏆\n\n{CAUTION}",reply_markup=kb_welcome(True,True))
        except: pass
    except Exception as e: await update.message.reply_text(f"Error: {e}")
async def removepaid_cmd(update,context):
    if update.effective_user.id!=ADMIN_ID: return
    if not context.args: await update.message.reply_text("Use: /removepaid USER_ID"); return
    tid=int(context.args[0])
    with db() as c: c.execute("DELETE FROM paid WHERE uid=?",(tid,)); c.execute("DELETE FROM daily_signals WHERE uid=?",(tid,))
    await update.message.reply_text(f"🗑️ Removed {tid}")
async def listpaid_cmd(update,context):
    if update.effective_user.id!=ADMIN_ID: return
    with db() as c: rows=c.execute("SELECT * FROM paid").fetchall()
    if not rows: await update.message.reply_text("No paid yet"); return
    txt="💰👑 PAID:\n"+"\n".join([f"{r['uid']} -> {r['plan']} until {r['expiry']}" for r in rows])
    await update.message.reply_text(txt)
async def history_cmd(update,context):
    rows=get_trades(update.effective_user.id)
    if not rows: await safe_reply(update.message,"No trades logged. Use /logtrade SYMBOL BUY 0.01 1.0850",kb_back_home()); return
    lines=["📊 YOUR TRADE HISTORY"]
    for r in rows: lines.append(f"#{r['id']} {r['side']} {r['quantity']:g} {r['symbol']} @ {r['entry_price']:g} | {local_time(r['logged_at'])}")
    await safe_reply(update.message,"\n".join(lines),kb_back_home())
async def logtrade_cmd(update,context):
    try:
        if len(context.args)!=4: raise ValueError()
        add_trade(update.effective_user.id,context.args[0].upper(),context.args[1].upper(),context.args[2],context.args[3])
        await safe_reply(update.message,f"✅ Recorded: {context.args[1].upper()} {context.args[0].upper()} 🔥",kb_back_home())
    except: await safe_reply(update.message,"Use: /logtrade EURUSD BUY 0.01 1.0850",kb_back_home())
async def help_cmd(update,context):
    await safe_reply(update.message,"🔥 BEAST V7 HELP 👑\n/start — Home\n📊 Today's Signals — bot picks best\n💰 PAY — payment details\n📖 /rules — BEAST 5 RULES\n🎯 /market — Gold only: choose market\n🤝 /ask — Gold only: 1-on-1\n💰 /paid TXN Plan — send proof\n\nManual only. No auto-trading.",kb_back_home())

async def callback_handler(update,context):
    q=update.callback_query
    if not q: return
    try: await q.answer()
    except: pass
    data=q.data or ""; uid=q.from_user.id; msg=q.message
    if msg is None: return
    if data=="home":
        await safe_edit(q,"👑🔥 BEAST SIGNALS — BEAST V7 💎\nChoose:",kb_welcome(is_paid(uid),is_trial_used(uid)))
        try: await safe_send(context.bot,msg.chat_id,"👑🔥 BEAST SIGNALS — BEAST V7 💎\nChoose:",kb_welcome(is_paid(uid),is_trial_used(uid)))
        except: pass
        return
    if data=="rules":
        await safe_edit(q,BEAST_RULES+"\n\n"+CAUTION,kb_back_home()); return
    if data=="pay":
        await safe_edit(q,pay_text(),kb_pay()); return
    if data=="how_paid":
        await safe_edit(q,"✅ Send:\n/paid TXN123 Pro\nReplace TXN123 with your transaction ID.\nAdmin confirms and unlocks you. 🚀\n\n"+CAUTION,kb_back_home()); return
    if data.startswith("market_"):
        plan=get_plan_for(uid)
        if not plan or not plan.get("market_choice"):
            await safe_edit(q,"🔒 Market choice is GOLD only. 👑\n🚀 Tap PAY to upgrade.",kb_pay()); return
        pref=data.replace("market_","")
        update_user(uid,"market_pref",pref)
        await safe_edit(q,f"✅🎯 Market set to {MARKET_LABEL.get(pref,'Mix')} 🔥\nYour next signals will come from that market.\n\n{CAUTION}",kb_back_home()); return
    if data=="free":
        if is_paid(uid):
            await safe_edit(q,"You are already VIP 👑. Tap Today's Signals. 🚀",kb_after_signal()); return
        if is_trial_used(uid):
            await safe_edit(q,"🔒 Your 1 FREE signal is used. 😱\n🔥 Tap PAY to unlock — don't miss tomorrow! 🚀\n\n"+CAUTION,kb_pay()); return
        await safe_edit(q,"⏳🔥 Claiming your 1 FREE signal...",kb_back_home())
        await send_signals_for(uid,q,is_free=True); return
    if data=="today":
        if not is_paid(uid):
            if not is_trial_used(uid):
                await safe_edit(q,"🎁 You still have your 1 FREE signal. Tap it first. 🔥",kb_welcome(False,False)); return
            await safe_edit(q,"🔒 Locked.\n🔥 Tap PAY to unlock — winners don't wait! 🚀\n\n"+CAUTION,kb_pay()); return
        await safe_edit(q,"⏳🔥 Getting your best signals for today...",kb_back_home())
        await send_signals_for(uid,q,is_free=False); return

async def handle_text(update,context):
    await safe_reply(update.message,"Tap /start for BEAST V7 menu. 🔥",kb_welcome(is_paid(update.effective_user.id),is_trial_used(update.effective_user.id)))

def get_hunter_msg(uid,ticker):
    with db() as c:
        r=c.execute("SELECT * FROM hunter_messages WHERE uid=? AND ticker=?",(uid,ticker)).fetchone()
        return dict(r) if r else None
def save_hunter_msg(uid,ticker,chat_id,mid,direction):
    with db() as c: c.execute("INSERT INTO hunter_messages(uid,ticker,chat_id,message_id,direction,updated_at,last_signal_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(uid,ticker) DO UPDATE SET chat_id=excluded.chat_id,message_id=excluded.message_id,direction=excluded.direction,updated_at=excluded.updated_at",(uid,ticker,chat_id,mid,direction,utc_iso(),utc_iso()))
def update_hunter_msg(uid,ticker,direction):
    with db() as c: c.execute("UPDATE hunter_messages SET direction=?,updated_at=? WHERE uid=? AND ticker=?",(direction,utc_iso(),uid,ticker))
async def edit_or_send_hunter(app,uid,ticker,result):
    direction=result["winner"] if not result.get("blocked") else "BLOCKED"
    if direction not in ("BUY","SELL","OVERSOLD","OVERBOUGHT","REVERSAL WATCH","HOLD"): direction="BLOCKED"
    if direction in ("BUY","SELL"): return
    existing=get_hunter_msg(uid,ticker)
    if direction in ("BLOCKED","HOLD","OVERBOUGHT","OVERSOLD"):
        text=f"🔔👑 BEAST V7 HUNTER — {ticker} 🔥\n{INVALID_NOTE}\n\n{CAUTION}"
        if existing:
            try: await app.bot.edit_message_text(chat_id=existing["chat_id"],message_id=existing["message_id"],text=text,reply_markup=kb_back_home()); update_hunter_msg(uid,ticker,direction); return
            except: pass
        if get_daily_count(uid)==0 and not is_trial_used(uid): return
        try:
            sent=await app.bot.send_message(chat_id=uid,text=text,reply_markup=kb_back_home())
            save_hunter_msg(uid,ticker,sent.chat_id,sent.message_id,direction)
        except Forbidden:
            with db() as c: c.execute("UPDATE users SET alert_enabled=0 WHERE uid=?",(uid,))
        except: pass
async def hunter_loop(app):
    while True:
        try:
            async with _hunter_cycle_lock:
                with db() as c: opted=[int(r["uid"]) for r in c.execute("SELECT uid FROM users WHERE alert_enabled=1").fetchall()]
                with db() as c: paid_users=[int(r["uid"]) for r in c.execute("SELECT uid FROM paid").fetchall()]
                all_uids=list(set(opted+paid_users))
                if all_uids:
                    for ticker in ("BTC-USD","TLT"):
                        res=await asyncio.to_thread(analyze_ticker,ticker)
                        for uid in all_uids:
                            if is_paid(uid): await edit_or_send_hunter(app,uid,ticker,res)
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)
        except asyncio.CancelledError: raise
        except: await asyncio.sleep(60)
async def post_init(app):
    await setup_commands(app)
    app.create_task(hunter_loop(app))
def main():
    print(f"=== BEAST SIGNALS {VERSION} STARTING ===",flush=True)
    init_db()
    threading.Thread(target=run_flask,daemon=True).start()
    app=ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    for cmd,h in [("start",start_cmd),("help",help_cmd),("rules",rules_cmd),("market",market_cmd),("ask",ask_cmd),("paid",paid_cmd),("addpaid",addpaid_cmd),("removepaid",removepaid_cmd),("listpaid",listpaid_cmd),("history",history_cmd),("logtrade",logtrade_cmd)]:
        app.add_handler(CommandHandler(cmd,h))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("BEAST V7 ready")
    app.run_polling(drop_pending_updates=True)
if __name__=="__main__": main()
