import yfinance as yf


def generate_signal(symbol):
    try:
        data = yf.download(symbol, period="6mo")

        if data.empty:
            return "NO DATA"

        data["MA20"] = data["Close"].rolling(window=20).mean()
        data["MA50"] = data["Close"].rolling(window=50).mean()

        data = data.dropna()

        if data.empty:
            return "NO DATA"

        latest = data.iloc[-1]

        ma20 = float(latest["MA20"])
        ma50 = float(latest["MA50"])

        if ma20 > ma50:
            return "BUY"
        elif ma20 < ma50:
            return "SELL"
        else:
            return "HOLD"

    except Exception as e:
        print("Signal error:", e)
        return "ERROR"
