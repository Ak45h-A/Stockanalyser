from flask import Flask, render_template, request, jsonify, Response
import yfinance as yf
import numpy as np
import pandas as pd
from datetime import datetime, timezone, date, timedelta
import math, random, calendar, time, threading, requests, json, queue

app = Flask(__name__)

# ══════════════════════════════════════════════════════════════════
# SESSION + CRUMB
# ══════════════════════════════════════════════════════════════════
_YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Referer":         "https://finance.yahoo.com/",
}

_session_lock = threading.Lock()
_yf_session   = None
_session_born = 0.0
_SESSION_TTL  = 1800

_crumb_lock = threading.Lock()
_crumb      = None
_crumb_born = 0.0
_CRUMB_TTL  = 3600

def _build_session():
    s = requests.Session()
    s.headers.update(_YF_HEADERS)
    return s

def _warm_session(s):
    try:
        s.get("https://finance.yahoo.com/", timeout=10)
        time.sleep(0.3)
    except Exception:
        pass
    return s

def _get_session(force=False):
    global _yf_session, _session_born, _crumb, _crumb_born
    with _session_lock:
        if force or _yf_session is None or (time.time() - _session_born) > _SESSION_TTL:
            _yf_session  = _warm_session(_build_session())
            _session_born = time.time()
            with _crumb_lock:
                _crumb = None; _crumb_born = 0.0
        return _yf_session

def _fetch_crumb_raw(sess):
    for base in ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"):
        try:
            r = sess.get(f"{base}/v1/test/getcrumb", timeout=6)
            if r.status_code == 200 and r.text.strip() not in ("", "null"):
                return r.text.strip().strip('"')
        except Exception:
            pass
    return None

def _get_crumb():
    global _crumb, _crumb_born
    with _crumb_lock:
        if _crumb and (time.time() - _crumb_born) < _CRUMB_TTL:
            return _crumb
    sess = _get_session()
    c = _fetch_crumb_raw(sess)
    with _crumb_lock:
        if c:
            _crumb = c; _crumb_born = time.time()
    return c

def _invalidate_crumb():
    global _crumb, _crumb_born
    with _crumb_lock:
        _crumb = None; _crumb_born = 0.0

def _startup():
    _get_session(force=True)
    time.sleep(0.5)
    _get_crumb()
threading.Thread(target=_startup, daemon=True).start()

_BASES = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
_yf_sem = threading.Semaphore(3)

# ══════════════════════════════════════════════════════════════════
# FALLBACK SYMBOLS
# ══════════════════════════════════════════════════════════════════
_IDX_FALLBACK = {
    "^NSEI":      ["^NSEI",     "NIFTY_50.NS"],
    "^NSEBANK":   ["^NSEBANK",  "NIFTY_BANK.NS"],
    "^CNXIT":     ["^CNXIT",    "NIFTY_IT.NS"],
    "^CNXAUTO":   ["^CNXAUTO",  "NIFTY_AUTO.NS"],
    "^CNXPHARMA": ["^CNXPHARMA","NIFTY_PHARMA.NS"],
    "^CNXFMCG":   ["^CNXFMCG",  "NIFTY_FMCG.NS"],
    "^CNXMETAL":  ["^CNXMETAL", "NIFTY_METAL.NS"],
    "^CNXREALTY": ["^CNXREALTY","NIFTY_REALTY.NS"],
    "^NSEMDCP50": ["^NSEMDCP50","NIFTY_MIDCAP_50.NS"],
    "^CNXSC":     ["^CNXSC",    "NIFTY_SMLCAP_100.NS"],
    "^BSESN":     ["^BSESN"],
    "^INDIAVIX":  ["^INDIAVIX", "INDIA_VIX.NS"],
}
def _cands(sym): return _IDX_FALLBACK.get(sym, [sym])

# ══════════════════════════════════════════════════════════════════
# FAST REAL-TIME PRICE  — Yahoo Finance /v7/finance/quote
# This is the FASTEST path: single HTTP call, returns live bid/ask
# and the regularMarketPrice which matches what you see on Yahoo.
# ══════════════════════════════════════════════════════════════════
def _v7_quote(sym):
    """
    Fetch live quote from Yahoo /v7/finance/quote.
    Returns dict or None. This is the fastest and most accurate
    path — it's the same endpoint Yahoo's own site uses.
    """
    sess  = _get_session()
    crumb = _get_crumb()
    for candidate in _cands(sym):
        for base in _BASES:
            url = f"{base}/v7/finance/quote"
            params = {
                "symbols":            candidate,
                "fields":             "regularMarketPrice,regularMarketPreviousClose,"
                                      "regularMarketDayHigh,regularMarketDayLow,"
                                      "regularMarketOpen,regularMarketChange,"
                                      "regularMarketChangePercent,currency,"
                                      "marketState,regularMarketTime",
                "formatted":          "false",
                "corsDomain":         "finance.yahoo.com",
            }
            if crumb: params["crumb"] = crumb
            try:
                r = sess.get(url, params=params, timeout=5)
                if r.status_code == 401:
                    _invalidate_crumb(); _get_session(force=True); crumb = _get_crumb()
                    params["crumb"] = crumb
                    r = sess.get(url, params=params, timeout=5)
                if r.status_code != 200: continue
                data = r.json()
                result = ((data.get("quoteResponse") or {}).get("result") or [None])[0]
                if not result: continue
                p = result.get("regularMarketPrice")
                if p:
                    return {
                        "price":     float(p),
                        "prev":      float(result.get("regularMarketPreviousClose") or p),
                        "high":      float(result.get("regularMarketDayHigh")       or p),
                        "low":       float(result.get("regularMarketDayLow")        or p),
                        "open":      float(result.get("regularMarketOpen")          or p),
                        "change":    float(result.get("regularMarketChange")        or 0),
                        "changePct": float(result.get("regularMarketChangePercent") or 0),
                        "currency":  result.get("currency", ""),
                        "marketState": result.get("marketState",""),
                    }
            except Exception:
                pass
        time.sleep(0.15)
    return None

# ══════════════════════════════════════════════════════════════════
# v8 CHART API  (for OHLCV history)
# ══════════════════════════════════════════════════════════════════
def _v8_price(sym):
    """v8 chart API for price — fallback if v7 fails."""
    sess = _get_session(); crumb = _get_crumb()
    for candidate in _cands(sym):
        for base in _BASES:
            url    = f"{base}/v8/finance/chart/{candidate}"
            params = {"interval":"1d","range":"2d","includePrePost":"false"}
            if crumb: params["crumb"] = crumb
            try:
                r = sess.get(url, params=params, timeout=6)
                if r.status_code == 401:
                    _invalidate_crumb(); _get_session(force=True); crumb = _get_crumb()
                    params["crumb"] = crumb; r = sess.get(url, params=params, timeout=6)
                if r.status_code != 200: continue
                data   = r.json()
                res    = (data.get("chart",{}).get("result") or [None])[0]
                if not res: continue
                meta   = res.get("meta",{})
                p      = meta.get("regularMarketPrice") or meta.get("price")
                if p:
                    prev = meta.get("previousClose") or meta.get("chartPreviousClose") or p
                    return {
                        "price":float(p),"prev":float(prev),
                        "high":float(meta.get("regularMarketDayHigh") or p),
                        "low":float(meta.get("regularMarketDayLow")   or p),
                        "open":float(meta.get("regularMarketOpen")    or p),
                        "change":float(p)-float(prev),
                        "changePct":(float(p)-float(prev))/float(prev)*100 if prev else 0,
                        "currency":meta.get("currency",""),
                    }
            except Exception: pass
        time.sleep(0.15)
    return None

def _v8_history(sym, period, interval):
    sess = _get_session(); crumb = _get_crumb()
    for candidate in _cands(sym):
        for base in _BASES:
            url    = f"{base}/v8/finance/chart/{candidate}"
            params = {"interval":interval,"range":period,"includePrePost":"false","events":"div,split"}
            if crumb: params["crumb"] = crumb
            try:
                r = sess.get(url, params=params, timeout=15)
                if r.status_code == 401:
                    _invalidate_crumb(); _get_session(force=True); crumb = _get_crumb()
                    params["crumb"] = crumb; r = sess.get(url, params=params, timeout=15)
                if r.status_code != 200: continue
                data   = r.json()
                result = (data.get("chart",{}).get("result") or [None])[0]
                if not result: continue
                ts = result.get("timestamp") or []
                q  = (result.get("indicators",{}).get("quote") or [{}])[0]
                if not ts or not q.get("close"): continue
                n  = len(ts)
                df = pd.DataFrame({
                    "Open":q.get("open",[None]*n),"High":q.get("high",[None]*n),
                    "Low":q.get("low",[None]*n),"Close":q.get("close",[None]*n),
                    "Volume":q.get("volume",[0]*n),
                }, index=pd.to_datetime(ts, unit="s", utc=True))
                df.index.name="Datetime"
                df = df.dropna(subset=["Close"])
                if not df.empty: return df
            except Exception: pass
        time.sleep(0.2)
    return pd.DataFrame()

def _yf_fallback(sym, period, interval):
    try:
        with _yf_sem:
            df = yf.download(sym, period=period, interval=interval,
                             auto_adjust=True, progress=False, session=_get_session())
        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex): df.columns = [c[0] for c in df.columns]
            return df
    except Exception: pass
    return pd.DataFrame()

def _history(sym, period, interval):
    is_idx = sym.startswith('^') or sym in _IDX_FALLBACK
    is_nse = sym.endswith('.NS') or sym.endswith('.BO')
    # Build a ladder of (period, interval) fallbacks
    if is_idx and interval in ('1m','2m','3m','5m','10m','15m','30m'):
        ladder = [(period,interval),('5d',interval),('1mo','30m'),('3mo','1d'),('6mo','1d')]
    elif is_idx and interval=='1d':
        ladder = [('6mo','1d'),('1y','1d')]
    elif interval in ('1d','1wk'):
        ladder = [(period,interval),('1y','1d'),('2y','1wk')]
    elif is_nse and interval in ('1m','2m','3m','5m','10m','15m'):
        # NSE equities: try more period options for intraday
        ladder = [(period,interval),('5d',interval),('1mo','15m'),('3mo','30m'),('6mo','1d')]
    elif is_nse and interval == '30m':
        ladder = [(period,'30m'),('1mo','30m'),('3mo','1h'),('6mo','1d')]
    else:
        ladder = [(period,interval),('5d','30m'),('1mo','1h'),('6mo','1d')]
    seen=set(); rungs=[]
    for r in ladder:
        if r not in seen: seen.add(r); rungs.append(r)
    for p,i in rungs:
        df = _v8_history(sym,p,i)
        if not df.empty: return df
        time.sleep(0.15)
    # yfinance fallback — try all ladder rungs
    for p,i in rungs:
        df = _yf_fallback(sym,p,i)
        if not df.empty: return df
    return pd.DataFrame()

def _ticker(sym):
    with _yf_sem: return yf.Ticker(sym, session=_get_session())

# ══════════════════════════════════════════════════════════════════
# LIVE PRICE BROADCASTER (Server-Sent Events)
# Subscribers register a symbol; background thread polls every 2s
# and pushes updates to all connected clients.
# ══════════════════════════════════════════════════════════════════
_subscribers     = {}   # sym → set of queue.Queue
_subscribers_lock = threading.Lock()
_last_price_val  = {}   # sym → last price dict (for dedup)

def _subscribe(sym):
    q = queue.Queue(maxsize=10)
    with _subscribers_lock:
        _subscribers.setdefault(sym, set()).add(q)
    return q

def _unsubscribe(sym, q):
    with _subscribers_lock:
        s = _subscribers.get(sym, set())
        s.discard(q)
        if not s: _subscribers.pop(sym, None)

def _broadcast(sym, data):
    with _subscribers_lock:
        qs = list(_subscribers.get(sym, set()))
    for q in qs:
        try: q.put_nowait(data)
        except queue.Full: pass

def _live_poller():
    """Background thread: polls all subscribed symbols every 2s."""
    while True:
        with _subscribers_lock:
            syms = list(_subscribers.keys())
        for sym in syms:
            try:
                q = _v7_quote(sym) or _v8_price(sym)
                if q:
                    code = detect_currency(sym) or q.get("currency","USD") or "USD"
                    cs   = CSYMS.get(code,"$")
                    p    = q["price"]
                    prev = q.get("prev", p)
                    chg  = q.get("change", p-prev)
                    pct  = q.get("changePct", chg/prev*100 if prev else 0)
                    now_utc = int(datetime.now(timezone.utc).timestamp())
                    data = {
                        "price":     round(p,2),
                        "change":    round(chg,2),
                        "changePct": round(pct,4),
                        "display":   fmt_price(cs,p),
                        "high":      round(q.get("high",p),2),
                        "low":       round(q.get("low",p),2),
                        "open":      round(q.get("open",p),2),
                        "prev":      round(prev,2),
                        "bid":       round(q.get("bid",p),2),
                        "ask":       round(q.get("ask",p),2),
                        "currency":  code, "symbol": cs,
                        "marketState": q.get("marketState",""),
                        "serverUtc": now_utc,
                        "candleUpdate": {
                            "time":  now_utc,
                            "close": round(p,2),
                            "high":  round(q.get("high",p),2),
                            "low":   round(q.get("low",p),2),
                            "open":  round(q.get("open",p),2),
                        }
                    }
                    _last_price_val[sym] = data
                    _broadcast(sym, data)
            except Exception: pass
        time.sleep(0.8)

threading.Thread(target=_live_poller, daemon=True).start()

# ══════════════════════════════════════════════════════════════════
# CACHE  (used for non-SSE endpoints)
# ══════════════════════════════════════════════════════════════════
_price_cache = {}; _cache_ttl = 3; _cache_lock = threading.Lock()

def _cache_get(sym):
    with _cache_lock:
        e = _price_cache.get(sym)
        if e and (time.time()-e[0]) < _cache_ttl: return dict(e[1])
    return None

def _cache_set(sym, data):
    with _cache_lock: _price_cache[sym] = (time.time(), dict(data))

# ══════════════════════════════════════════════════════════════════
# CURRENCY
# ══════════════════════════════════════════════════════════════════
CSYMS = {"INR":"₹","USD":"$","EUR":"€","GBP":"£","JPY":"¥","CNY":"¥",
         "AUD":"A$","CAD":"C$","SGD":"S$","HKD":"HK$","KRW":"₩"}

def detect_currency(sym):
    s = str(sym).upper()
    if s.endswith(".NS") or s.endswith(".BO"): return "INR"
    if any(x in s for x in ["^NSEI","^BSESN","^NSE","^CNX","NIFTY","SENSEX",
                             "NIFTY_","INDIA_VIX","^INDIAVIX"]): return "INR"
    if s.endswith("=F") or s.endswith("-USD") or s.endswith("=X"): return "USD"
    if s.endswith(".L"): return "GBP"
    if s.endswith(".PA") or s.endswith(".DE"): return "EUR"
    if s.endswith(".HK"): return "HKD"
    if s.endswith(".T"):  return "JPY"
    return None

def safe(v):
    try:
        f = float(v); return None if (np.isnan(f) or np.isinf(f)) else round(f,4)
    except: return None

def fmt_inr(p):
    v=float(p); s=f"{v:.2f}".split("."); n,d=s[0],s[1]
    if len(n)>3:
        l3=n[-3:]; rest=n[:-3]; g=[]
        while len(rest)>2: g.insert(0,rest[-2:]); rest=rest[:-2]
        if rest: g.insert(0,rest)
        n=",".join(g)+","+l3
    return f"₹{n}.{d}"

def fmt_price(cs,p):
    if p is None: return "—"
    return fmt_inr(float(p)) if cs=="₹" else f"{cs}{float(p):,.2f}"

# ══════════════════════════════════════════════════════════════════
# INDICES MAP
# ══════════════════════════════════════════════════════════════════
IDX = {
    "nifty 50":"^NSEI","nifty50":"^NSEI","nifty":"^NSEI",
    "bank nifty":"^NSEBANK","banknifty":"^NSEBANK","nifty bank":"^NSEBANK",
    "nifty it":"^CNXIT","nifty auto":"^CNXAUTO","nifty pharma":"^CNXPHARMA",
    "nifty fmcg":"^CNXFMCG","nifty metal":"^CNXMETAL","nifty realty":"^CNXREALTY",
    "nifty midcap":"^NSEMDCP50","nifty smallcap":"^CNXSC","nifty next 50":"^NSMIDCP50",
    "india vix":"^INDIAVIX","indiavix":"^INDIAVIX",
    "sensex":"^BSESN","bse sensex":"^BSESN",
    "dow jones":"^DJI","dow":"^DJI","s&p 500":"^GSPC","sp500":"^GSPC",
    "nasdaq":"^IXIC","nasdaq composite":"^IXIC","vix":"^VIX",
    "nikkei":"^N225","hang seng":"^HSI","ftse 100":"^FTSE","dax":"^GDAXI",
    "crude oil":"CL=F","gold":"GC=F","silver":"SI=F","natural gas":"NG=F",
    "bitcoin":"BTC-USD","ethereum":"ETH-USD","btc":"BTC-USD","eth":"ETH-USD",
    "usd/inr":"USDINR=X","usdinr":"USDINR=X",
}
TV_IDX = {
    "^NSEI":"NSE:NIFTY","^NSEBANK":"NSE:BANKNIFTY","^BSESN":"BSE:SENSEX",
    "^CNXIT":"NSE:CNXIT","^DJI":"DJ:DJI","^GSPC":"SP:SPX","^IXIC":"NASDAQ:NDX",
    "^VIX":"CBOE:VIX","^N225":"TVC:NI225","^HSI":"TVC:HSI","^FTSE":"TVC:UKX",
    "^GDAXI":"TVC:DEU40","^INDIAVIX":"NSE:INDIAVIX",
    "CL=F":"NYMEX:CL1!","GC=F":"COMEX:GC1!",
    "BTC-USD":"BINANCE:BTCUSDT","ETH-USD":"BINANCE:ETHUSDT",
    "USDINR=X":"FX_IDC:USDINR",
}

# Common stock symbol map for instant lookup without yfinance.Search
_QUICK_SYMS = {
    # NSE Indices
    "nifty":"^NSEI","nifty 50":"^NSEI","nifty50":"^NSEI",
    "bank nifty":"^NSEBANK","banknifty":"^NSEBANK","nifty bank":"^NSEBANK",
    "sensex":"^BSESN","bse sensex":"^BSESN",
    "india vix":"^INDIAVIX","indiavix":"^INDIAVIX","vix":"^INDIAVIX",
    # NSE Top stocks (direct symbol, no search needed)
    "tcs":"TCS.NS","reliance":"RELIANCE.NS","infosys":"INFY.NS","infy":"INFY.NS",
    "hdfc bank":"HDFCBANK.NS","hdfcbank":"HDFCBANK.NS","hdfc":"HDFCBANK.NS",
    "icici bank":"ICICIBANK.NS","icicibank":"ICICIBANK.NS","icici":"ICICIBANK.NS",
    "sbi":"SBIN.NS","state bank":"SBIN.NS",
    "airtel":"BHARTIARTL.NS","bharti airtel":"BHARTIARTL.NS",
    "wipro":"WIPRO.NS","hcl":"HCLTECH.NS","hcltech":"HCLTECH.NS",
    "tata motors":"TATAMOTORS.NS","tatamotors":"TATAMOTORS.NS",
    "bajaj finance":"BAJFINANCE.NS","bajfinance":"BAJFINANCE.NS",
    "maruti":"MARUTI.NS","maruti suzuki":"MARUTI.NS",
    "kotak":"KOTAKBANK.NS","kotak bank":"KOTAKBANK.NS","kotakbank":"KOTAKBANK.NS",
    "axis bank":"AXISBANK.NS","axisbank":"AXISBANK.NS","axis":"AXISBANK.NS",
    "l&t":"LT.NS","lt":"LT.NS","larsen":"LT.NS",
    "adani":"ADANIENT.NS","adani enterprises":"ADANIENT.NS",
    "sun pharma":"SUNPHARMA.NS","sunpharma":"SUNPHARMA.NS",
    "ongc":"ONGC.NS","ntpc":"NTPC.NS","tata steel":"TATASTEEL.NS",
    "divi":"DIVISLAB.NS","divis":"DIVISLAB.NS",
    "bajaj finserv":"BAJAJFINSV.NS","hul":"HINDUNILVR.NS","nestle":"NESTLEIND.NS",
    # Global
    "apple":"AAPL","tesla":"TSLA","microsoft":"MSFT","nvidia":"NVDA",
    "google":"GOOGL","amazon":"AMZN","meta":"META","netflix":"NFLX",
    "amd":"AMD","intel":"INTC",
    "gold":"GC=F","crude":"CL=F","crude oil":"CL=F","silver":"SI=F",
    "bitcoin":"BTC-USD","btc":"BTC-USD","ethereum":"ETH-USD","eth":"ETH-USD",
    "sp500":"^GSPC","s&p 500":"^GSPC","nasdaq":"^IXIC","dow jones":"^DJI",
}

def find_symbol(company):
    q = company.strip().lower()
    # 1. Check quick map first (instant, no API call)
    if q in _QUICK_SYMS:
        ys = _QUICK_SYMS[q]
        return TV_IDX.get(ys, ys.replace(".NS","").replace(".BO","")), ys
    # 2. Check IDX map
    if q in IDX:
        ys=IDX[q]; return TV_IDX.get(ys,ys),ys
    for k,ys in IDX.items():
        if q in k or k in q: return TV_IDX.get(ys,ys),ys
    # 3. Direct symbol (uppercase) — handles TCS.NS, AAPL, ^NSEI etc
    qu=company.strip().upper()
    if qu.endswith(".NS") or qu.endswith(".BO"):
        tv = ("NSE:" if qu.endswith(".NS") else "BSE:") + qu.split(".")[0]
        return tv, qu
    if qu.startswith("^") or "=F" in qu or "=X" in qu or "-USD" in qu:
        return TV_IDX.get(qu,qu),qu
    # 4. Partial match in quick map
    for k,ys in _QUICK_SYMS.items():
        if q in k or k.startswith(q):
            return TV_IDX.get(ys, ys.replace(".NS","").replace(".BO","")), ys
    # 5. yfinance Search (fallback)
    try:
        with _yf_sem: s=yf.Search(company,session=_get_session())
        if not s.quotes: return None,None
        q2=s.quotes[0]; ys=q2["symbol"]; ex=q2.get("exchange","")
        if   ys.endswith(".NS") or ex in ("NSE","NSI"): tv="NSE:"+ys.replace(".NS","")
        elif ys.endswith(".BO") or ex in ("BSE","BOM"): tv="BSE:"+ys.replace(".BO","")
        elif ex=="NASDAQ": tv="NASDAQ:"+ys
        elif ex=="NYSE":   tv="NYSE:"+ys
        else:              tv=ys
        return tv,ys
    except: return None,None

@app.route("/autocomplete")
def autocomplete():
    q=request.args.get("q","").strip().lower()
    if not q: return jsonify([])
    out=[]
    # Quick map matches
    for k,ys in _QUICK_SYMS.items():
        if q in k:
            isIdx=ys.startswith("^") or "=F" in ys or "-USD" in ys
            ex="INDEX" if isIdx else ("NSE" if ys.endswith(".NS") else "BSE" if ys.endswith(".BO") else "GLOBAL")
            n=k.title()
            if not any(o["symbol"]==ys for o in out):
                out.append({"symbol":ys,"name":n,"exchange":ex,"isIndex":isIdx})
        if len(out)>=4: break
    # IDX map
    for k,ys in IDX.items():
        if q in k.lower() and not any(o["symbol"]==ys for o in out):
            out.append({"symbol":ys,"name":k.title(),"exchange":"INDEX","isIndex":True})
        if len(out)>=5: break
    if len(q)>=2:
        try:
            with _yf_sem: s=yf.Search(q,max_results=8,session=_get_session())
            for r in (s.quotes or [])[:8]:
                sym=r.get("symbol",""); name=r.get("shortname") or r.get("longname") or sym
                ex=r.get("exchange","")
                if sym and not any(o["symbol"]==sym for o in out):
                    isIdx=sym.startswith("^")
                    out.append({"symbol":sym,"name":name,"exchange":ex,"isIndex":isIdx})
        except: pass
    return jsonify(out[:10])

# ══════════════════════════════════════════════════════════════════
# /price  — one-shot REST endpoint (uses v7 → v8 → yfinance)
# ══════════════════════════════════════════════════════════════════
@app.route("/price")
def price():
    sym     = request.args.get("symbol","").strip()
    now_utc = int(datetime.now(timezone.utc).timestamp())
    na = {"price":"N/A","change":0,"changePct":0,"currency":"INR","symbol":"₹",
          "display":"N/A","high":None,"low":None,"open":None,"prev":None,"serverUtc":now_utc}
    if not sym: return jsonify(na)

    cached = _cache_get(sym)
    if cached: cached["serverUtc"]=now_utc; return jsonify(cached)

    code=detect_currency(sym) or "USD"; cs=CSYMS.get(code,"$")
    p=hi=lo=op=prev=chg=pct=None

    # ── v7 /quote (fastest, matches Yahoo live price) ────────────
    q7 = _v7_quote(sym)
    if q7:
        p=q7["price"]; hi=q7["high"]; lo=q7["low"]; op=q7["open"]; prev=q7["prev"]
        chg=q7["change"]; pct=q7["changePct"]
        if q7.get("currency") and q7["currency"] in CSYMS:
            code=q7["currency"]; cs=CSYMS[code]

    # ── v8 chart fallback ────────────────────────────────────────
    if p is None:
        q8=_v8_price(sym)
        if q8:
            p=q8["price"]; hi=q8["high"]; lo=q8["low"]; op=q8["open"]; prev=q8["prev"]
            chg=q8["change"]; pct=q8["changePct"]
            if q8.get("currency") and q8["currency"] in CSYMS:
                code=q8["currency"]; cs=CSYMS[code]

    # ── yfinance fast_info fallback ──────────────────────────────
    if p is None:
        for cand in _cands(sym):
            try:
                t=_ticker(cand); fi=t.fast_info
                p=float(fi.last_price) if fi.last_price else None
                if p and not np.isnan(p):
                    hi=float(fi.day_high or p); lo=float(fi.day_low or p)
                    op=float(fi.open or p); prev=float(fi.previous_close or p)
                    chg=p-prev; pct=(chg/prev*100) if prev else 0
                    cdet=detect_currency(cand)
                    if not cdet:
                        try: cdet=fi.currency
                        except: pass
                    code=cdet or "USD"; cs=CSYMS.get(code,"$")
                    break
            except: p=None; continue

    if p is None or (isinstance(p,float) and np.isnan(p)): return jsonify(na)

    hi=hi or p; lo=lo or p; op=op or p; prev=prev or p
    if chg is None: chg=p-prev
    if pct is None: pct=(chg/prev*100) if prev else 0

    result={
        "price":round(p,2),"change":round(chg,2),"changePct":round(pct,4),
        "currency":code,"symbol":cs,"display":fmt_price(cs,p),
        "high":round(hi,2),"low":round(lo,2),"open":round(op,2),"prev":round(prev,2),
        "serverUtc":now_utc
    }
    _cache_set(sym,result)
    return jsonify(result)

# ══════════════════════════════════════════════════════════════════
# /stream/price  — Server-Sent Events for live price streaming
# Usage:  const es = new EventSource('/stream/price?symbol=^NSEI');
#         es.onmessage = e => { const d = JSON.parse(e.data); ... }
# Pushes an update whenever the price changes (polls every 2s).
# ══════════════════════════════════════════════════════════════════
@app.route("/stream/price")
def stream_price():
    sym = request.args.get("symbol","").strip()
    if not sym:
        return Response("data: {}\n\n", mimetype="text/event-stream")

    def generate():
        q = _subscribe(sym)
        # Send immediately from cache / last known value
        last = _last_price_val.get(sym)
        if last:
            yield f"data: {json.dumps(last)}\n\n"
        else:
            # Fire an immediate fetch so first message arrives fast
            threading.Thread(target=lambda: _v7_quote(sym), daemon=True).start()
        try:
            while True:
                try:
                    data = q.get(timeout=30)
                    yield f"data: {json.dumps(data)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"   # keep connection alive
        except GeneratorExit:
            pass
        finally:
            _unsubscribe(sym, q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# ══════════════════════════════════════════════════════════════════
# /ohlc
# ══════════════════════════════════════════════════════════════════
@app.route("/ohlc")
def ohlc():
    sym=request.args.get("symbol","").strip()
    period=request.args.get("period","6mo")
    interval=request.args.get("interval","1d")
    if not sym: return jsonify({"error":"No symbol"})
    SECS={"1m":60,"2m":120,"5m":300,"15m":900,"30m":1800,"60m":3600,"1h":3600,"1d":86400,"1wk":604800}
    try:
        code=detect_currency(sym) or "USD"; cs2=CSYMS.get(code,"$")
        df=_history(sym,period,interval)
        if df.empty: return jsonify({"error":f"No chart data for {sym}."})
        is_daily=interval in ("1d","1wk","1mo")
        rows=[]
        for ts,row in df.iterrows():
            o=safe(row.get("Open")); h=safe(row.get("High"))
            l=safe(row.get("Low"));  c=safe(row.get("Close"))
            try:   v=int(row.get("Volume",0) or 0)
            except: v=0
            if not (o and h and l and c): continue
            if is_daily:
                time_val=str(ts)[:10]
            else:
                try:
                    utc_unix=int(ts.tz_convert("UTC").timestamp()) if (hasattr(ts,"tzinfo") and ts.tzinfo) else int(pd.Timestamp(ts,tz="UTC").timestamp())
                except: utc_unix=int(pd.Timestamp(ts).timestamp())
                time_val=utc_unix
            rows.append({"time":time_val,"open":o,"high":h,"low":l,"close":c,"volume":v})
        seen=set(); unique=[]
        for r in rows:
            if r["time"] not in seen: seen.add(r["time"]); unique.append(r)
        if not unique: return jsonify({"error":f"No valid candles for {sym}"})
        return jsonify({"data":unique,"currency":code,"currencySymbol":cs2,
            "isDaily":is_daily,"barSecs":SECS.get(interval,300),
            "lastBarUtc":unique[-1]["time"],"count":len(unique)})
    except Exception as e: return jsonify({"error":str(e)})

# ══════════════════════════════════════════════════════════════════
# /indicators
# ══════════════════════════════════════════════════════════════════
@app.route("/indicators")
def indicators():
    sym=request.args.get("symbol","").strip()
    if not sym: return jsonify({"error":"No symbol"})
    try:
        code=detect_currency(sym) or "USD"; cs2=CSYMS.get(code,"$")
        df=_history(sym,"6mo","1d")
        if df.empty: return jsonify({"error":"No data for "+sym})
        c,h,lo,v=df["Close"],df["High"],df["Low"],df["Volume"]
        dates=[str(x)[:10] for x in df.index]
        ema9=c.ewm(span=9,adjust=False).mean()
        ema21=c.ewm(span=21,adjust=False).mean()
        ema50=c.ewm(span=50,adjust=False).mean()
        ema200=c.ewm(span=200,adjust=False).mean()
        d2=c.diff(); g=d2.where(d2>0,0).rolling(14).mean()
        ls=(-d2.where(d2<0,0)).rolling(14).mean(); rsi=100-(100/(1+g/ls))
        e12=c.ewm(span=12,adjust=False).mean(); e26=c.ewm(span=26,adjust=False).mean()
        macd=e12-e26; sig=macd.ewm(span=9,adjust=False).mean(); hist=macd-sig
        s20=c.rolling(20).mean(); std=c.rolling(20).std()
        bb_u=s20+2*std; bb_l=s20-2*std
        tr=np.maximum(h-lo,np.maximum(abs(h-c.shift()),abs(lo-c.shift())))
        atr=tr.rolling(14).mean()
        lw14=lo.rolling(14).min(); hw14=h.rolling(14).max()
        sk=100*(c-lw14)/(hw14-lw14+1e-9); sd=sk.rolling(3).mean()
        pdm=(h.diff()).where((h.diff()>lo.diff())&(h.diff()>0),0)
        mdm=(-lo.diff()).where((-lo.diff()>h.diff())&(-lo.diff()>0),0)
        atrs=tr.rolling(14).sum()
        pdi=100*pdm.rolling(14).sum()/(atrs+1e-9)
        mdi=100*mdm.rolling(14).sum()/(atrs+1e-9)
        adx=(100*abs(pdi-mdi)/(pdi+mdi+1e-9)).rolling(14).mean()
        obv=(np.sign(c.diff())*v).fillna(0).cumsum()
        typ=(h+lo+c)/3; vwap=(typ*v).rolling(20).sum()/(v.rolling(20).sum()+1e-9)
        return jsonify({"dates":dates,"close":[safe(x) for x in c],
            "ema9":[safe(x) for x in ema9],"ema21":[safe(x) for x in ema21],
            "ema50":[safe(x) for x in ema50],"ema200":[safe(x) for x in ema200],
            "rsi":[safe(x) for x in rsi],"macd":[safe(x) for x in macd],
            "signal":[safe(x) for x in sig],"histogram":[safe(x) for x in hist],
            "bb_upper":[safe(x) for x in bb_u],"bb_lower":[safe(x) for x in bb_l],
            "bb_mid":[safe(x) for x in s20],"atr":[safe(x) for x in atr],
            "stoch_k":[safe(x) for x in sk],"stoch_d":[safe(x) for x in sd],
            "adx":[safe(x) for x in adx],"plus_di":[safe(x) for x in pdi],
            "minus_di":[safe(x) for x in mdi],"obv":[safe(x) for x in obv],
            "vwap":[safe(x) for x in vwap],"currency":code,"currencySymbol":cs2})
    except Exception as e: return jsonify({"error":str(e)})

# ══════════════════════════════════════════════════════════════════
# /predict
# ══════════════════════════════════════════════════════════════════
PERIOD_MAP={"1m":"1d","2m":"2d","3m":"3d","5m":"5d","10m":"5d","15m":"1mo",
            "30m":"1mo","60m":"3mo","1h":"3mo","1d":"6mo","1wk":"2y"}
TARGET_MAP={
    "1m":(["Now","+1m","+2m","+3m","+5m"],60),
    "2m":(["Now","+2m","+4m","+6m","+10m"],120),
    "3m":(["Now","+3m","+6m","+9m","+15m"],180),
    "5m":(["Now","+5m","+10m","+15m","+30m"],300),
    "10m":(["Now","+10m","+20m","+30m","+1h"],600),
    "15m":(["Now","+15m","+30m","+45m","+1h"],900),
    "30m":(["Now","+30m","+1h","+1.5h","+2h"],1800),
    "60m":(["Now","+1h","+2h","+3h","+5h"],3600),
    "1h":(["Now","+1h","+2h","+3h","+5h"],3600),
    "1d":(["Now","+1d","+2d","+3d","+5d"],86400),
    "1wk":(["Now","+1w","+2w","+3w","+4w"],604800),
}
TARGET_KEY_MAP={
    "1m":{"1m":"1m","2m":"2m","3m":"3m","5m":"5m"},
    "2m":{"2m":"2m","4m":"4m","6m":"6m","10m":"10m"},
    "3m":{"3m":"3m","6m":"6m","9m":"9m","15m":"15m"},
    "5m":{"5m":"5m","10m":"10m","15m":"15m","30m":"30m"},
    "10m":{"10m":"10m","20m":"20m","30m":"30m","1h":"1h"},
    "15m":{"15m":"15m","30m":"30m","45m":"45m","1h":"1h"},
    "30m":{"30m":"30m","1h":"1h","1.5h":"1.5h","2h":"2h"},
    "60m":{"1h":"1h","2h":"2h","3h":"3h","5h":"5h"},
    "1h":{"1h":"1h","2h":"2h","3h":"3h","5h":"5h"},
    "1d":{"1d":"1d","2d":"2d","3d":"3d","5d":"5d"},
    "1wk":{"1w":"1w","2w":"2w","3w":"3w","4w":"4w"},
}

@app.route("/predict")
def predict():
    sym=request.args.get("symbol","").strip()
    interval=request.args.get("interval","5m")
    if not sym: return jsonify({"error":"No symbol"})
    if interval not in PERIOD_MAP: interval="5m"
    period=PERIOD_MAP[interval]
    labels,bar_s=TARGET_MAP.get(interval,TARGET_MAP["5m"])
    tkeys=list(TARGET_KEY_MAP.get(interval,{"5m":"5m","10m":"10m","15m":"15m","30m":"30m"}).keys())
    try:
        code=detect_currency(sym) or "USD"; cs2=CSYMS.get(code,"$")
        df=_history(sym,period,interval)
        if (df.empty or len(df)<10) and interval not in ('1d','1wk'):
            df=_history(sym,"6mo","1d")
            if not df.empty:
                interval="1d"; bar_s=86400
                labels=["Now","+1d","+2d","+3d","+5d"]; tkeys=["1d","2d","3d","5d"]
        if df.empty or len(df)<5:
            return jsonify({"error":f"No data for {sym}."})
        c,h,lo,v=df["Close"],df["High"],df["Low"],df["Volume"]
        def fv(s):
            val=float(s.iloc[-1]); return 0 if (np.isnan(val) or np.isinf(val)) else val
        ema9=c.ewm(span=9,adjust=False).mean()
        ema21=c.ewm(span=21,adjust=False).mean()
        ema50=c.ewm(span=50,adjust=False).mean()
        d2=c.diff(); g=d2.where(d2>0,0).rolling(min(14,len(c))).mean()
        ls=(-d2.where(d2<0,0)).rolling(min(14,len(c))).mean(); rsi=100-(100/(1+g/ls))
        s20=c.rolling(min(20,len(c))).mean(); std=c.rolling(min(20,len(c))).std()
        bb_u=s20+2*std; bb_l=s20-2*std; bb_p=(c-bb_l)/(bb_u-bb_l+1e-9)
        tr=np.maximum(h-lo,np.maximum(abs(h-c.shift()),abs(lo-c.shift())))
        atr=tr.rolling(min(14,len(c))).mean()
        lw14=lo.rolling(min(14,len(c))).min(); hw14=h.rolling(min(14,len(c))).max()
        sk=100*(c-lw14)/(hw14-lw14+1e-9)
        e12=c.ewm(span=12,adjust=False).mean(); e26=c.ewm(span=26,adjust=False).mean()
        macd=e12-e26; msig=macd.ewm(span=9,adjust=False).mean()
        atr7=tr.rolling(min(7,len(c))).mean(); mid=(h+lo)/2; sv=[]; trend=1
        for i in range(len(c)):
            av=float(atr7.iloc[i]) if not np.isnan(float(atr7.iloc[i])) else 0
            sd_v=float(mid.iloc[i])-3*av; su_v=float(mid.iloc[i])+3*av
            if i==0: sv.append(sd_v); continue
            pv2=sv[-1]
            if trend==1:
                val=max(sd_v,pv2)
                if float(c.iloc[i])<val: trend=-1; val=su_v
            else:
                val=min(su_v,pv2)
                if float(c.iloc[i])>val: trend=1; val=sd_v
            sv.append(val)
        cur=fv(c); e9=fv(ema9); e21_v=fv(ema21); e50_v=fv(ema50)
        r=fv(rsi); bp=fv(bb_p); atrv=fv(atr) or cur*0.005
        k=fv(sk); macdv=fv(macd); msigv=fv(msig); stv=sv[-1]
        avg_v=float(v.iloc[-min(20,len(v)):-1].mean()) if len(v)>=2 else float(v.mean())
        vol_r=float(v.iloc[-1])/avg_v if avg_v>0 else 1
        pts=[]; signals=[]
        def add(pt,ic,tx): pts.append(pt); signals.append({"icon":ic,"text":tx})

        # ── Lagging trend indicators ──────────────────────────────
        if e9>e21_v:  add(2,"✅","EMA9 > EMA21 — Bullish cross")
        else:         add(-2,"🔴","EMA9 < EMA21 — Bearish cross")
        if cur>e50_v: add(1,"✅",f"Above EMA50 ({fmt_price(cs2,e50_v)})")
        else:         add(-1,"🔴",f"Below EMA50 ({fmt_price(cs2,e50_v)})")

        # ── RSI — momentum oscillator ─────────────────────────────
        if r<25:      add(4,"✅",f"RSI {r:.1f} — Extreme Oversold ⚡")
        elif r<35:    add(3,"✅",f"RSI {r:.1f} — Oversold")
        elif r>80:    add(-4,"🔴",f"RSI {r:.1f} — Extreme Overbought ⚠️")
        elif r>65:    add(-2,"🔴",f"RSI {r:.1f} — Overbought")
        elif r>55:    add(1,"🟡",f"RSI {r:.1f} — Bullish zone")
        elif r<45:    add(-1,"🟡",f"RSI {r:.1f} — Bearish zone")
        else:         add(0,"⚪",f"RSI {r:.1f} — Neutral")

        # ── Bollinger Band position ───────────────────────────────
        if bp<0.15:   add(3,"✅","Near lower BB — Strong buy zone")
        elif bp<0.25: add(1,"✅","Lower BB zone — Buy bias")
        elif bp>0.85: add(-3,"🔴","Near upper BB — Strong sell zone")
        elif bp>0.75: add(-1,"🔴","Upper BB zone — Sell bias")
        else:         add(0,"⚪",f"BB position {bp*100:.0f}%")

        # ── Stochastic ────────────────────────────────────────────
        if k<20:      add(2,"✅",f"Stoch {k:.1f} — Oversold")
        elif k>80:    add(-2,"🔴",f"Stoch {k:.1f} — Overbought")
        else:         add(0,"⚪",f"Stoch {k:.1f}")

        # ── MACD — trend + histogram velocity ────────────────────
        if macdv>msigv: add(1,"✅","MACD above signal line")
        else:           add(-1,"🔴","MACD below signal line")
        # MACD histogram velocity (is momentum increasing or decreasing)
        try:
            hist_now  = float(macd.iloc[-1]) - float(msig.iloc[-1])
            hist_prev = float(macd.iloc[-2]) - float(msig.iloc[-2])
            hist_vel  = hist_now - hist_prev
            if hist_vel > atrv * 0.05:
                add(2,"📈","MACD histogram accelerating up")
            elif hist_vel < -atrv * 0.05:
                add(-2,"📉","MACD histogram accelerating down")
        except: pass

        # ── Supertrend ────────────────────────────────────────────
        if cur>stv:   add(2,"✅","Above Supertrend — BUY signal")
        else:         add(-2,"🔴","Below Supertrend — SELL signal")

        # ── Volume confirmation ───────────────────────────────────
        if vol_r>2.0: add(2 if sum(pts)>0 else -2,"📊",f"Strong volume {vol_r:.1f}× avg")
        elif vol_r>1.4: add(1 if sum(pts)>0 else -1,"📊",f"Volume spike {vol_r:.1f}× avg")

        # ── Price Rate of Change (ROC) — THE KEY MOMENTUM SIGNAL ─
        # This is what makes prediction match volatile markets.
        # We look at price change over last 1, 3, and 5 bars.
        try:
            roc1  = (float(c.iloc[-1]) - float(c.iloc[-2])) / (float(c.iloc[-2])+1e-9) * 100
            roc3  = (float(c.iloc[-1]) - float(c.iloc[-4])) / (float(c.iloc[-4])+1e-9) * 100 if len(c)>=4 else 0
            roc5  = (float(c.iloc[-1]) - float(c.iloc[-6])) / (float(c.iloc[-6])+1e-9) * 100 if len(c)>=6 else 0
            # Strong directional ROC overrides lagging indicators
            if roc1 > 0.5:   add(3,"🚀",f"Price up {roc1:.2f}% last bar")
            elif roc1 > 0.2: add(1,"📈",f"Price up {roc1:.2f}% last bar")
            elif roc1 < -0.5: add(-3,"💥",f"Price down {roc1:.2f}% last bar")
            elif roc1 < -0.2: add(-1,"📉",f"Price down {roc1:.2f}% last bar")
            if roc3 > 1.0:   add(2,"📈",f"3-bar momentum +{roc3:.2f}%")
            elif roc3 < -1.0: add(-2,"📉",f"3-bar momentum {roc3:.2f}%")
            mom_str = f"+{roc5:.2f}%" if roc5>=0 else f"{roc5:.2f}%"
            add(2 if roc5>0 else -2 if roc5<0 else 0,
                "✅" if roc5>0 else "🔴" if roc5<0 else "⚪",
                f"5-bar momentum {mom_str}")
        except: pass

        # ── Candle body direction (last 3 bars) ───────────────────
        # Count bullish vs bearish candles in recent bars
        try:
            bull_bars = sum(1 for i in range(-3,0) if float(c.iloc[i])>float(c.iloc[i-1]))
            bear_bars = 3 - bull_bars
            if bull_bars >= 3:    add(2,"🕯️","3 consecutive bullish candles")
            elif bear_bars >= 3:  add(-2,"🕯️","3 consecutive bearish candles")
            elif bull_bars == 2:  add(1,"🕯️","Mostly bullish candles")
            elif bear_bars == 2:  add(-1,"🕯️","Mostly bearish candles")
        except: pass

        # ── Price vs VWAP ─────────────────────────────────────────
        try:
            typ2=(h+lo+c)/3
            vwap_val=float((typ2*v).rolling(min(20,len(c))).sum().iloc[-1] /
                           (v.rolling(min(20,len(c))).sum().iloc[-1]+1e-9))
            if cur > vwap_val * 1.001:  add(1,"✅",f"Above VWAP {fmt_price(cs2,vwap_val)}")
            elif cur < vwap_val * 0.999: add(-1,"🔴",f"Below VWAP {fmt_price(cs2,vwap_val)}")
        except: pass

        # ── Final score with ROC dominance ───────────────────────
        # Normalise: raw score can be large due to more signals
        raw_score = sum(pts)
        # Cap to [-15, 15] then remap to [-10, 10]
        score = max(-10, min(10, raw_score * 10 / 15))
        score = round(score, 1)

        if score>=2.5:    direction,color="BULLISH","#00C48C"
        elif score<=-2.5: direction,color="BEARISH","#E05555"
        else:             direction,color="SIDEWAYS","#F0A82A"
        conf=round(min(95,abs(score)/10*100+20),1)  # min 20% confidence
        entry=round(cur,2)
        if direction=="BULLISH":
            sl=round(cur-atrv,2); t1=round(cur+atrv*.5,2); t2=round(cur+atrv,2)
            t3=round(cur+atrv*1.5,2); t4=round(cur+atrv*2,2)
        elif direction=="BEARISH":
            sl=round(cur+atrv,2); t1=round(cur-atrv*.5,2); t2=round(cur-atrv,2)
            t3=round(cur-atrv*1.5,2); t4=round(cur-atrv*2,2)
        else:
            sl=round(cur-atrv*.8,2); t1=t2=t3=t4=round(cur,2)
        now_utc=int(datetime.now(timezone.utc).timestamp())
        bar_boundary=(now_utc//bar_s)*bar_s
        prices=[cur,t1,t2,t3,t4]; proj=[]
        for i,px in enumerate(prices):
            t_utc=bar_boundary+i*bar_s; prev_p=prices[i-1] if i>0 else cur
            noise=atrv*0.06; op2=round(prev_p,2); cl2=round(px,2)
            proj.append({"time":t_utc,"open":op2,"high":round(max(op2,cl2)+noise,2),
                "low":round(min(op2,cl2)-noise,2),"close":cl2,
                "label":labels[i] if i<len(labels) else f"+{i}"})
        markers=[]; recent=df.tail(20)
        for i,(ts,row) in enumerate(recent.iterrows()):
            try: ut=int(ts.tz_convert("UTC").timestamp()) if (hasattr(ts,"tzinfo") and ts.tzinfo) else int(pd.Timestamp(ts).timestamp())
            except: ut=int(pd.Timestamp(ts).timestamp())
            if i==len(recent)-1:
                markers.append({"time":ut,"position":"belowBar" if direction=="BULLISH" else "aboveBar",
                    "color":color,"shape":"arrowUp" if direction=="BULLISH" else "arrowDown",
                    "text":f"ENTRY {fmt_price(cs2,entry)}","size":2})
            elif i==len(recent)-2:
                markers.append({"time":ut,"position":"aboveBar" if direction=="BULLISH" else "belowBar",
                    "color":"#F45E6D","shape":"circle","text":f"SL {fmt_price(cs2,sl)}","size":1})
        for i,pc in enumerate(proj[1:],1):
            markers.append({"time":pc["time"],"position":"aboveBar" if direction=="BULLISH" else "belowBar",
                "color":"#FFB84D","shape":"circle","text":f"T{i}","size":1})
        st_line=[]
        for i,(ts,row) in enumerate(recent.iterrows()):
            idx2=len(df)-20+i
            if idx2<0 or idx2>=len(sv): continue
            try: ut=int(ts.tz_convert("UTC").timestamp()) if (hasattr(ts,"tzinfo") and ts.tzinfo) else int(pd.Timestamp(ts).timestamp())
            except: ut=int(pd.Timestamp(ts).timestamp())
            st_line.append({"time":ut,"value":round(sv[idx2],2),
                "color":"#00D09C" if float(c.iloc[idx2])>sv[idx2] else "#F45E6D"})
        dfd=_history(sym,"3mo","1d"); macro="⚪ Insufficient data"
        if not dfd.empty and len(dfd)>10:
            cd=dfd["Close"]
            e50d=float(cd.ewm(span=50,adjust=False).mean().iloc[-1])
            e200d=float(cd.ewm(span=200,adjust=False).mean().iloc[-1]) if len(cd)>=200 else e50d
            lp=float(cd.iloc[-1])
            if lp>e50d>e200d:   macro="📈 Daily: Strong Uptrend (above EMA50 & EMA200)"
            elif lp<e50d<e200d: macro="📉 Daily: Strong Downtrend (below EMA50 & EMA200)"
            elif lp>e50d:       macro="🟡 Daily: Bullish — Above EMA50"
            elif lp<e50d:       macro="🟠 Daily: Bearish — Below EMA50"
            else:               macro="↔️ Daily: Consolidating"
        tgt_keys=tkeys if len(tkeys)==4 else ["5m","10m","15m","30m"]
        tgt_vals=[t1,t2,t3,t4]
        targets={tgt_keys[i]:{"price":round(tgt_vals[i],2),"display":fmt_price(cs2,tgt_vals[i])} for i in range(min(4,len(tgt_keys)))}
        return jsonify({"direction":direction,"color":color,"score":round(score,1),"confidence":conf,
            "currentPrice":round(cur,2),"currencySymbol":cs2,"currency":code,
            "entry":entry,"entryDisplay":fmt_price(cs2,entry),
            "stopLoss":sl,"slDisplay":fmt_price(cs2,sl),
            "targets":targets,"projectedCandles":proj,"markers":markers,
            "supertrendLine":st_line,"signals":signals,
            "macroTrend":macro,"rsi":round(r,1),"atr":round(atrv,2),
            "interval":interval,"barSecs":bar_s})
    except Exception as e: return jsonify({"error":str(e)})

# ══════════════════════════════════════════════════════════════════
# OPTIONS CHAIN
# ══════════════════════════════════════════════════════════════════
def _norm_cdf(x): return 0.5*(1.0+math.erf(x/math.sqrt(2.0)))
def _bs_price(S,K,T,r_f,sigma,opt_type='C'):
    if T<=0 or S<=0 or K<=0 or sigma<=0:
        return max(0.0,round((S-K) if opt_type=='C' else (K-S),2))
    d1=(math.log(S/K)+(r_f+0.5*sigma**2)*T)/(sigma*math.sqrt(T))
    d2=d1-sigma*math.sqrt(T)
    if opt_type=='C': price=S*_norm_cdf(d1)-K*math.exp(-r_f*T)*_norm_cdf(d2)
    else:             price=K*math.exp(-r_f*T)*_norm_cdf(-d2)-S*_norm_cdf(-d1)
    return max(0.0,round(price,2))

def _make_expiries():
    today=date.today(); exps=[]; d=today
    while len(exps)<8:
        d+=timedelta(days=1)
        if d.weekday()==3: exps.append(d.strftime('%Y-%m-%d'))
    for dm in range(3):
        m=(today.month+dm-1)%12+1; y=today.year+(today.month+dm-1)//12
        eom=date(y,m,calendar.monthrange(y,m)[1])
        while eom.weekday()!=3: eom-=timedelta(days=1)
        s=eom.strftime('%Y-%m-%d')
        if s not in exps: exps.append(s)
    exps.sort(); return exps[:8]

def _synthetic_options(spot,sym='',expiry_idx=0):
    if spot>=50000: iv2=500
    elif spot>=20000: iv2=200
    elif spot>=10000: iv2=100
    elif spot>=5000:  iv2=50
    elif spot>=1000:  iv2=20
    elif spot>=500:   iv2=10
    elif spot>=100:   iv2=5
    else:             iv2=1
    atm=round(spot/iv2)*iv2; strikes=[atm+i*iv2 for i in range(-8,9)]
    T=max(1,7*(expiry_idx+1))/365.0; r_f=0.065; base_iv=0.18
    rng=random.Random(hash(sym[:8] if sym else 'SYN')%99991+int(spot)%9999)
    base_oi=rng.randint(40000,120000); calls,puts=[],[]
    for K in strikes:
        m=(K-atm)/max(atm,1.0)
        ivc=max(0.08,min(0.85,base_iv+0.07*m**2-0.008*m+rng.uniform(-0.012,0.012)))
        ivp=max(0.08,min(0.85,base_iv+0.07*m**2+0.016*(-m)+rng.uniform(-0.012,0.012)))
        cp=_bs_price(spot,K,T,r_f,ivc,'C'); pp=_bs_price(spot,K,T,r_f,ivp,'P')
        oi=math.exp(-4.5*m**2)*rng.uniform(0.65,1.35)
        coi=int(base_oi*oi*rng.uniform(0.9,1.1)); poi=int(base_oi*oi*rng.uniform(0.85,1.15))
        sc=max(0.05,cp*0.025); sp=max(0.05,pp*0.025)
        calls.append({"strike":round(K,2),"lastPrice":cp,"openInterest":coi,"impliedVolatility":round(ivc,4),
            "volume":int(coi*0.10*rng.uniform(0.4,1.6)),"bid":round(max(0,cp-sc),2),"ask":round(cp+sc,2)})
        puts.append({"strike":round(K,2),"lastPrice":pp,"openInterest":poi,"impliedVolatility":round(ivp,4),
            "volume":int(poi*0.10*rng.uniform(0.4,1.6)),"bid":round(max(0,pp-sp),2),"ask":round(pp+sp,2)})
    return calls,puts

@app.route("/optionchain")
def optionchain():
    sym=request.args.get("symbol","").strip()
    expiry_idx=int(request.args.get("expiry_idx","0"))
    if not sym: return jsonify({"error":"No symbol"})
    try:
        code=detect_currency(sym) or "USD"; cs2=CSYMS.get(code,"$")
        spot=None
        cached=_cache_get(sym)
        if cached and cached.get("price")!="N/A": spot=cached.get("price")
        if not spot:
            q7=_v7_quote(sym)
            if q7: spot=q7["price"]
        calls_data=puts_data=real_exps=None; use_real=False
        try:
            t=_ticker(sym)
            with _yf_sem: real_exps=t.options
            if real_exps:
                idx=min(expiry_idx,len(real_exps)-1)
                with _yf_sem: chain=t.option_chain(real_exps[idx])
                def proc(df):
                    cols=[c for c in ["strike","lastPrice","openInterest","impliedVolatility","volume","bid","ask"] if c in df.columns]
                    return df[cols].head(20).fillna(0).to_dict(orient="records")
                calls_data=proc(chain.calls); puts_data=proc(chain.puts)
                if sum(r.get("openInterest",0) for r in calls_data+puts_data)>0: use_real=True
        except: pass
        if not use_real:
            if not spot:
                df=_history(sym,"5d","1d")
                spot=float(df["Close"].iloc[-1]) if not df.empty else 1000.0
            exps=_make_expiries(); idx=min(expiry_idx,len(exps)-1)
            calls_data,puts_data=_synthetic_options(spot,sym=sym,expiry_idx=idx); exps_list=exps
        else:
            exps_list=list(real_exps); idx=min(expiry_idx,len(exps_list)-1)
        calls,puts=calls_data,puts_data
        coi=sum(r.get("openInterest",0) for r in calls); poi=sum(r.get("openInterest",0) for r in puts)
        cv=sum(r.get("volume",0) for r in calls); pv=sum(r.get("volume",0) for r in puts)
        pcr=round(poi/(coi+1e-9),3)
        pcr_sig=("🟢 Bullish — PCR>1.3" if pcr>1.3 else "🔴 Bearish — PCR<0.7" if pcr<0.7 else "🟡 Neutral")
        merged={}
        for r in calls+puts: merged[r["strike"]]=merged.get(r["strike"],0)+r.get("openInterest",0)
        return jsonify({"expiries":exps_list,"selectedExpiry":exps_list[idx],
            "currency":code,"currencySymbol":cs2,"pcrOI":pcr,"pcrSignal":pcr_sig,
            "totalCallOI":int(coi),"totalPutOI":int(poi),"totalCallVol":int(cv),"totalPutVol":int(pv),
            "maxPain":max(merged,key=merged.get) if merged else None,
            "calls":calls,"puts":puts,"synthetic":(not use_real),
            "spotPrice":round(spot,2) if spot else None})
    except Exception as e: return jsonify({"error":str(e)})

# ══════════════════════════════════════════════════════════════════
# NEWS
# ══════════════════════════════════════════════════════════════════
@app.route("/news")
def news_api():
    sym=request.args.get("symbol","RELIANCE.NS").strip() or "RELIANCE.NS"
    def _parse(items):
        out=[]
        for n in (items or [])[:20]:
            ct=n.get("content",{}) if isinstance(n.get("content"),dict) else {}
            title=ct.get("title") or n.get("title","")
            url=(ct.get("canonicalUrl") or {}).get("url","") or n.get("link","")
            src2=(ct.get("provider") or {}).get("displayName","") or n.get("publisher","Yahoo Finance")
            pub=ct.get("pubDate","") or str(n.get("providerPublishTime",""))[:10]
            thumb=""
            for res in (ct.get("thumbnail") or {}).get("resolutions",[]): thumb=res.get("url",""); break
            if title and url: out.append({"title":title,"url":url,"source":src2,"pubDate":str(pub)[:10],"thumbnail":thumb})
        return out
    try:
        out=_parse(_ticker(sym).news)
        seen={x["url"] for x in out}
        for gs in ["^GSPC","^NSEI","GC=F","CL=F","BTC-USD","AAPL"]:
            if len(out)>=18: break
            try:
                for item in _parse(_ticker(gs).news):
                    if item["url"] not in seen: out.append(item); seen.add(item["url"])
            except: pass
        return jsonify(out[:20])
    except: return jsonify([])


@app.route("/api/chat", methods=["POST"])
def api_chat():
    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY","")
    if not api_key:
        try:
            with open(".anthropic_key","r") as kf: api_key=kf.read().strip()
        except: pass
    if not api_key:
        return jsonify({"error":"ANTHROPIC_API_KEY not set","content":[{"type":"text","text":"Set ANTHROPIC_API_KEY environment variable to enable AI features."}]})
    try:
        body=request.get_json(force=True) or {}
        payload={"model":"claude-haiku-4-5-20251001","max_tokens":1000,
                 "system":body.get("system","You are TradeAI, a professional stock market analyst."),
                 "messages":body.get("messages",[])}
        r=requests.post("https://api.anthropic.com/v1/messages",
                        headers={"x-api-key":api_key,"anthropic-version":"2023-06-01","content-type":"application/json"},
                        json=payload,timeout=30)
        if r.status_code==200: return jsonify(r.json())
        return jsonify({"error":f"Anthropic {r.status_code}","content":[{"type":"text","text":r.text[:200]}]})
    except Exception as e:
        return jsonify({"error":str(e),"content":[{"type":"text","text":f"Server error: {e}"}]})

# ══════════════════════════════════════════════════════════════════
# DEBUG
# ══════════════════════════════════════════════════════════════════
@app.route("/debug/yahoo")
def debug_yahoo():
    crumb=_get_crumb()
    q=_v7_quote("^NSEI")
    return jsonify({"crumb_ok":crumb is not None,"crumb_prefix":crumb[:12]+"…" if crumb else None,
        "nsei":q,"session_age_s":round(time.time()-_session_born,1)})

# ══════════════════════════════════════════════════════════════════
# PAGE ROUTES
# ══════════════════════════════════════════════════════════════════
@app.route("/",methods=["GET","POST"])
def index():
    if request.method=="POST":
        company=request.form.get("company","").strip()
        if not company: return render_template("index.html")
        tv,yaho=find_symbol(company)
        if tv and yaho: return render_template("index.html",symbol=tv,yahoo=yaho)
        return render_template("index.html",
            error=f"Could not find '{company}'. Try: NIFTY 50, Sensex, Reliance, Apple")
    sym=request.args.get("symbol","").strip()
    if sym:
        tv,yaho=find_symbol(sym)
        if tv and yaho: return render_template("index.html",symbol=tv,yahoo=yaho)
    return render_template("index.html")

@app.route("/dashboard")
def dashboard(): return render_template("dashboard.html")
@app.route("/stocks")
def stocks():    return render_template("stocks.html")
@app.route("/options")
def options():   return render_template("options.html")
@app.route("/newspage")
def newspage():  return render_template("news.html")
@app.route("/ai")
def ai():        return render_template("ai.html")

if __name__=="__main__":
    app.run(debug=True, threaded=True, use_reloader=False, host="0.0.0.0", port=5000)
