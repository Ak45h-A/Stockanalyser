// 3. After your chart + candleSeries are built, call:
LivePrice.init({
  symbol:       yahooSymbol,          // e.g. "^NSEI"
  priceEl:      "#livePrice",
  changeEl:     "#liveChange",
  statusEl:     "#mktStatus",
  highEl:       "#dayHigh",
  lowEl:        "#dayLow",
  candleSeries: candleSeries,         // your LW chart series object
  barSecs:      300,                  // 300 = 5m, 86400 = 1d, etc.
  isDaily:      false,
});

// 4. When user switches timeframe, call:
LivePrice.setSeries(newCandleSeries, newBarSecs);