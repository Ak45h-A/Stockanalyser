import requests
import pandas as pd

url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"

headers = {
"user-agent": "Mozilla/5.0",
"accept-language": "en-US,en;q=0.9"
}

session = requests.Session()
data = session.get(url, headers=headers).json()

records = data['records']['data']

calls = []
puts = []

for item in records:
    if 'CE' in item:
        calls.append({
            "strike": item['strikePrice'],
            "oi": item['CE']['openInterest'],
            "ltp": item['CE']['lastPrice']
        })
        
    if 'PE' in item:
        puts.append({
            "strike": item['strikePrice'],
            "oi": item['PE']['openInterest'],
            "ltp": item['PE']['lastPrice']
        })

call_df = pd.DataFrame(calls)
put_df = pd.DataFrame(puts)

print(call_df.head())
print(put_df.head())
