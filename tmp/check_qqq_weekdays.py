from llm_quant.data.fetcher import fetch_ohlcv
import polars as pl

df = fetch_ohlcv(["BTC-USD", "QQQ"], lookback_days=120)
qqq = df.filter(pl.col("symbol") == "QQQ")
summary = (
    qqq.select(pl.col("date").dt.weekday().alias("weekday"))
    .group_by("weekday")
    .len()
    .sort("weekday")
)
print(summary)
