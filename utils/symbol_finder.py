import yfinance as yf


def find_symbol(company):

    try:

        search = yf.Search(company)

        if not search.quotes:
            return None

        symbol = search.quotes[0]["symbol"]

        exchange = search.quotes[0].get("exchange", "")

        if exchange == "NSE":
            return f"NSE:{symbol}"

        if exchange == "BSE":
            return f"BSE:{symbol}"

        if exchange == "NASDAQ":
            return f"NASDAQ:{symbol}"

        if exchange == "NYSE":
            return f"NYSE:{symbol}"

        return symbol

    except:
        return None
