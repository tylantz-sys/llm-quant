"""Fetch intraday OHLCV bars from Alpaca Market Data API v2."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl
import requests

logger = logging.getLogger(__name__)


class AlpacaDataError(RuntimeError):
    """Raised when Alpaca Market Data API requests fail."""


class AlpacaDataClient:
    """Thin REST client for Alpaca Market Data v2 bars."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str | None = None,
        crypto_base_url: str | None = None,
        feed: str | None = None,
        timeout: int = 30,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url or os.environ.get(
            "ALPACA_DATA_URL", "https://data.alpaca.markets/v2"
        )
        self.crypto_base_url = crypto_base_url or os.environ.get(
            "ALPACA_CRYPTO_DATA_URL", "https://data.alpaca.markets/v1beta3/crypto/us"
        )
        self.feed = feed or os.environ.get("ALPACA_DATA_FEED", "iex")
        self.timeout = timeout

    @classmethod
    def from_env(cls, timeout: int = 30) -> AlpacaDataClient:
        from llm_quant.utils.env import load_dotenv_if_present

        load_dotenv_if_present()
        api_key = os.environ.get("ALPACA_API_KEY") or os.environ.get("APCA_API_KEY_ID")
        api_secret = os.environ.get("ALPACA_SECRET_KEY") or os.environ.get(
            "APCA_API_SECRET_KEY"
        )
        if not api_key or not api_secret:
            msg = "Missing ALPACA_API_KEY / ALPACA_SECRET_KEY in environment."
            raise AlpacaDataError(msg)
        return cls(api_key, api_secret, timeout=timeout)

    def fetch_bars(
        self,
        symbols: list[str],
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10000,
    ) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "timestamp": pl.Datetime,
                    "open": pl.Float64,
                    "high": pl.Float64,
                    "low": pl.Float64,
                    "close": pl.Float64,
                    "volume": pl.Int64,
                    "vwap": pl.Float64,
                }
            )

        url = f"{self.base_url}/stocks/bars"
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }

        params: dict[str, Any] = {
            "symbols": ",".join(symbols),
            "timeframe": timeframe,
            "start": _format_ts(start),
            "end": _format_ts(end),
            "limit": limit,
            "feed": self.feed,
            "sort": "asc",
        }

        rows: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            if page_token:
                params["page_token"] = page_token
            try:
                resp = requests.get(
                    url, headers=headers, params=params, timeout=self.timeout
                )
            except requests.RequestException as exc:
                raise AlpacaDataError(f"Alpaca data request failed: {exc}") from exc

            if resp.status_code != 200:
                raise AlpacaDataError(
                    f"Alpaca data request failed ({resp.status_code}): {resp.text}"
                )

            payload = resp.json()
            bars = payload.get("bars", {})

            if isinstance(bars, dict):
                for symbol, bar_list in bars.items():
                    rows.extend(_normalize_bars(symbol, bar_list))
            elif isinstance(bars, list):
                rows.extend(_normalize_bars(None, bars))

            page_token = payload.get("next_page_token")
            if not page_token:
                break

        if not rows:
            logger.warning("No intraday bars returned for %s", symbols)
            return pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "timestamp": pl.Datetime,
                    "open": pl.Float64,
                    "high": pl.Float64,
                    "low": pl.Float64,
                    "close": pl.Float64,
                    "volume": pl.Int64,
                    "vwap": pl.Float64,
                }
            )

        df = pl.DataFrame(rows, infer_schema_length=None)
        df = df.with_columns(pl.col("timestamp").cast(pl.Datetime))
        return df.sort(["symbol", "timestamp"])

    def fetch_crypto_bars(
        self,
        symbols: list[str],
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10000,
    ) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "timestamp": pl.Datetime,
                    "open": pl.Float64,
                    "high": pl.Float64,
                    "low": pl.Float64,
                    "close": pl.Float64,
                    "volume": pl.Float64,
                    "vwap": pl.Float64,
                }
            )

        url = f"{self.crypto_base_url}/bars"
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }

        params: dict[str, Any] = {
            "symbols": ",".join(symbols),
            "timeframe": timeframe,
            "start": _format_ts(start),
            "end": _format_ts(end),
            "limit": limit,
            "sort": "asc",
        }

        rows: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            if page_token:
                params["page_token"] = page_token
            try:
                resp = requests.get(
                    url, headers=headers, params=params, timeout=self.timeout
                )
            except requests.RequestException as exc:
                raise AlpacaDataError(
                    f"Alpaca crypto data request failed: {exc}"
                ) from exc

            if resp.status_code != 200:
                err = (
                    "Alpaca crypto data request failed "
                    f"({resp.status_code}): {resp.text}"
                )
                raise AlpacaDataError(err)

            payload = resp.json()
            bars = payload.get("bars", {})

            if isinstance(bars, dict):
                for symbol, bar_list in bars.items():
                    rows.extend(_normalize_bars(symbol, bar_list))
            elif isinstance(bars, list):
                rows.extend(_normalize_bars(None, bars))

            page_token = payload.get("next_page_token")
            if not page_token:
                break

        if not rows:
            logger.warning("No intraday crypto bars returned for %s", symbols)
            return pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "timestamp": pl.Datetime,
                    "open": pl.Float64,
                    "high": pl.Float64,
                    "low": pl.Float64,
                    "close": pl.Float64,
                    "volume": pl.Float64,
                    "vwap": pl.Float64,
                }
            )

        df = pl.DataFrame(rows, infer_schema_length=None)
        df = df.with_columns(
            pl.col("timestamp").cast(pl.Datetime),
            pl.col("volume")
            .cast(pl.Float64, strict=False)
            .fill_null(0.0)
            .round(0)
            .cast(pl.Int64)
            .alias("volume"),
        )
        return df.sort(["symbol", "timestamp"])


def fetch_intraday_ohlcv(
    symbols: list[str],
    timeframe_minutes: int,
    lookback_days: int,
    timeout: int = 30,
) -> pl.DataFrame:
    """Convenience wrapper to fetch recent intraday bars for symbols."""
    client = AlpacaDataClient.from_env(timeout=timeout)
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=lookback_days)
    timeframe = f"{timeframe_minutes}Min"
    return client.fetch_bars(symbols, timeframe=timeframe, start=start, end=end)


def normalize_crypto_symbols(
    symbols: list[str],
    symbol_map: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Normalize crypto symbols for Alpaca and return reverse map."""
    mapping = symbol_map or {}
    normalized: list[str] = []
    reverse: dict[str, str] = {}
    for symbol in symbols:
        mapped = mapping.get(symbol, symbol)
        if "-" in mapped and "/" not in mapped:
            mapped = mapped.replace("-", "/")
        normalized.append(mapped)
        reverse[mapped] = symbol
    return normalized, reverse


def fetch_intraday_crypto_ohlcv(
    symbols: list[str],
    timeframe_minutes: int,
    lookback_days: int,
    timeout: int = 30,
    symbol_map: dict[str, str] | None = None,
) -> pl.DataFrame:
    """Fetch recent intraday crypto bars, normalizing symbols for Alpaca."""
    client = AlpacaDataClient.from_env(timeout=timeout)
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=lookback_days)
    timeframe = f"{timeframe_minutes}Min"
    normalized, reverse = normalize_crypto_symbols(symbols, symbol_map)
    df = client.fetch_crypto_bars(normalized, timeframe=timeframe, start=start, end=end)
    if df.is_empty():
        return df
    if reverse:
        df = df.with_columns(
            pl.col("symbol")
            .map_elements(lambda s: reverse.get(s, s), return_dtype=pl.Utf8)
            .alias("symbol")
        )
    return df


def _format_ts(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).replace(tzinfo=None).isoformat() + "Z"


def _normalize_bars(
    symbol: str | None, bars: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bar in bars:
        sym = symbol or bar.get("S") or bar.get("symbol")
        ts = bar.get("t") or bar.get("timestamp")
        if not sym or not ts:
            continue
        dt = _parse_ts(ts)
        rows.append(
            {
                "symbol": sym,
                "timestamp": dt,
                "open": bar.get("o") or bar.get("open"),
                "high": bar.get("h") or bar.get("high"),
                "low": bar.get("l") or bar.get("low"),
                "close": bar.get("c") or bar.get("close"),
                "volume": bar.get("v") or bar.get("volume"),
                "vwap": bar.get("vw") or bar.get("vwap"),
            }
        )
    return rows


def _parse_ts(ts: str) -> datetime:
    # Alpaca returns RFC3339 with Z suffix; normalize to naive UTC.
    if ts.endswith("Z"):
        ts = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt
