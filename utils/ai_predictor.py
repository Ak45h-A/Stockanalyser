import yfinance as yf
import pandas as pd
from prophet import Prophet

def predict_stock(symbol):

    data = yf.download(symbol, period="1y")

    df = data.reset_index()[["Date","Close"]]
    df.columns = ["ds","y"]

    model = Prophet()
    model.fit(df)

    future = model.make_future_dataframe(periods=30)

    forecast = model.predict(future)

    return forecast[["ds","yhat"]].tail(30)