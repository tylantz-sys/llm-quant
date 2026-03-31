"""DuckDB read/write layer for market data."""

from __future__ import annotations

import logging
import signal
import time
from uuid import uuid4
from datetime import UTC, datetime
from datetime import date as date_type

import duckdb
import polars as pl

logger = logging.getLogger(__name__)


class UpsertTimeoutError(RuntimeError):
    """Raised when a DB upsert exceeds the configured timeout."""


def _is_lock_error(exc: Exception) -> bool:
    message = str(exc)
    return "Conflicting lock" in message or "Could not set lock" in message


class _Timeout:
    """Signal-based timeout helper (POSIX only)."""

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds
        self._old_handler = None

    def __enter__(self) -> None:
        if self._seconds <= 0 or not hasattr(signal, "SIGALRM"):
            return

        def _handle(_signum, _frame):
            raise UpsertTimeoutError(
                f"DB upsert exceeded {self._seconds:.0f}s timeout"
            )

        self._old_handler = signal.signal(signal.SIGALRM, _handle)
        signal.setitimer(signal.ITIMER_REAL, self._seconds)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._seconds <= 0 or not hasattr(signal, "SIGALRM"):
            return
        signal.setitimer(signal.ITIMER_REAL, 0)
        if self._old_handler is not None:
            signal.signal(signal.SIGALRM, self._old_handler)


def _bulk_upsert(
    conn: duckdb.DuckDBPyConnection,
    df: pl.DataFrame,
    table: str,
    columns: list[str],
) -> None:
    """Bulk upsert using DuckDB's relation API."""
    view_name = f"_upsert_{uuid4().hex}"
    cols_sql = ", ".join(columns)
    conn.register(view_name, df)
    try:
        conn.execute(
            f"INSERT OR REPLACE INTO {table} ({cols_sql}) "
            f"SELECT {cols_sql} FROM {view_name}"
        )
    finally:
        try:
            conn.unregister(view_name)
        except duckdb.Error:
            pass

# Canonical column order matching the market_data_daily table schema.
_ALL_COLUMNS: list[str] = [
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
    "sma_20",
    "sma_50",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_hist",
    "atr_14",
]

_OHLCV_COLUMNS: list[str] = [
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
]

_INTRADAY_COLUMNS: list[str] = [
    "symbol",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vwap",
    "sma_20",
    "sma_50",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_hist",
    "atr_14",
]


def upsert_market_data(
    conn: duckdb.DuckDBPyConnection,
    df: pl.DataFrame,
) -> int:
    """Insert or replace rows into ``market_data_daily``.

    The incoming DataFrame must contain at least the OHLCV columns
    (``symbol, date, open, high, low, close, volume, adj_close``).
    Indicator columns (``sma_20``, ``rsi_14``, etc.) are optional; when
    absent they will be inserted as ``NULL``.

    Parameters
    ----------
    conn:
        An open DuckDB connection with the schema already initialised.
    df:
        Polars DataFrame with market data rows.

    Returns
    -------
    int
        Number of rows upserted.
    """
    if df is None or len(df) == 0:
        logger.info("Empty DataFrame — nothing to upsert")
        return 0

    # Ensure all table columns exist in the DataFrame (fill missing with null)
    for col in _ALL_COLUMNS:
        if col not in df.columns:
            if col == "symbol":
                df = df.with_columns(pl.lit(None).cast(pl.Utf8).alias(col))
            elif col == "date":
                df = df.with_columns(pl.lit(None).cast(pl.Date).alias(col))
            elif col == "volume":
                df = df.with_columns(pl.lit(None).cast(pl.Int64).alias(col))
            else:
                df = df.with_columns(pl.lit(None).cast(pl.Float64).alias(col))

    # Select columns in canonical order
    df = df.select(_ALL_COLUMNS)

    # Coerce date column to Date type if needed
    if df.schema["date"] != pl.Date:
        df = df.with_columns(pl.col("date").cast(pl.Date))

    row_count = len(df)

    # Prefer bulk upsert (faster, reduces lock time); fallback to row-by-row.
    try:
        _bulk_upsert(conn, df, "market_data_daily", _ALL_COLUMNS)
        conn.commit()
    except duckdb.Error as exc:
        logger.warning("Bulk upsert failed; falling back to row-by-row: %s", exc)
        try:
            cols = ", ".join(_ALL_COLUMNS)
            placeholders = ", ".join(["?"] * len(_ALL_COLUMNS))
            stmt = (
                f"INSERT OR REPLACE INTO market_data_daily ({cols}) VALUES ({placeholders})"
            )
            rows = df.rows()
            conn.executemany(stmt, rows)
            conn.commit()
        except duckdb.Error:
            logger.exception(
                "Failed to upsert %d rows into market_data_daily", row_count
            )
            raise
    except Exception:
        logger.exception("Failed to upsert %d rows into market_data_daily", row_count)
        raise

    logger.info("Upserted %d rows into market_data_daily", row_count)
    return row_count


def upsert_market_data_with_retry(
    conn: duckdb.DuckDBPyConnection,
    df: pl.DataFrame,
    *,
    max_retries: int = 2,
    retry_delay_seconds: float = 1.0,
    timeout_seconds: float = 30.0,
) -> int:
    """Upsert daily data with retries + timeout to avoid hangs."""
    attempt = 0
    while True:
        try:
            with _Timeout(timeout_seconds):
                return upsert_market_data(conn, df)
        except UpsertTimeoutError:
            attempt += 1
            if attempt > max_retries:
                raise
            time.sleep(retry_delay_seconds)
        except duckdb.IOException as exc:
            if not _is_lock_error(exc):
                raise
            attempt += 1
            if attempt > max_retries:
                raise
            time.sleep(retry_delay_seconds)


def upsert_intraday_data(
    conn: duckdb.DuckDBPyConnection,
    df: pl.DataFrame,
) -> int:
    """Insert or replace rows into ``market_data_intraday``.

    The incoming DataFrame must contain at least ``symbol`` and ``timestamp``.
    Indicator columns are optional; missing columns are filled with NULLs.
    """
    if df is None or len(df) == 0:
        logger.info("Empty intraday DataFrame — nothing to upsert")
        return 0

    for col in _INTRADAY_COLUMNS:
        if col not in df.columns:
            if col == "symbol":
                df = df.with_columns(pl.lit(None).cast(pl.Utf8).alias(col))
            elif col == "timestamp":
                df = df.with_columns(pl.lit(None).cast(pl.Datetime).alias(col))
            elif col == "volume":
                df = df.with_columns(pl.lit(None).cast(pl.Int64).alias(col))
            else:
                df = df.with_columns(pl.lit(None).cast(pl.Float64).alias(col))

    df = df.select(_INTRADAY_COLUMNS)

    if df.schema["timestamp"] != pl.Datetime:
        df = df.with_columns(pl.col("timestamp").cast(pl.Datetime))

    row_count = len(df)
    try:
        _bulk_upsert(conn, df, "market_data_intraday", _INTRADAY_COLUMNS)
        conn.commit()
    except duckdb.Error as exc:
        logger.warning("Bulk intraday upsert failed; falling back: %s", exc)
        try:
            cols = ", ".join(_INTRADAY_COLUMNS)
            placeholders = ", ".join(["?"] * len(_INTRADAY_COLUMNS))
            stmt = (
                f"INSERT OR REPLACE INTO market_data_intraday ({cols}) VALUES ({placeholders})"
            )
            conn.executemany(stmt, df.rows())
            conn.commit()
        except duckdb.Error:
            logger.exception("Failed to upsert %d intraday rows", row_count)
            raise
    except Exception:
        logger.exception("Failed to upsert %d intraday rows", row_count)
        raise

    logger.info("Upserted %d rows into market_data_intraday", row_count)
    return row_count


def upsert_intraday_data_with_retry(
    conn: duckdb.DuckDBPyConnection,
    df: pl.DataFrame,
    *,
    max_retries: int = 2,
    retry_delay_seconds: float = 1.0,
    timeout_seconds: float = 30.0,
) -> int:
    """Upsert intraday data with retries + timeout to avoid hangs."""
    attempt = 0
    while True:
        try:
            with _Timeout(timeout_seconds):
                return upsert_intraday_data(conn, df)
        except UpsertTimeoutError:
            attempt += 1
            if attempt > max_retries:
                raise
            time.sleep(retry_delay_seconds)
        except duckdb.IOException as exc:
            if not _is_lock_error(exc):
                raise
            attempt += 1
            if attempt > max_retries:
                raise
            time.sleep(retry_delay_seconds)


def get_intraday_data(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
    start_ts: datetime,
    end_ts: datetime | None = None,
) -> pl.DataFrame:
    """Read intraday bars for given symbols and timestamp range."""
    if not symbols:
        logger.warning("No symbols provided — returning empty intraday DataFrame")
        return pl.DataFrame(
            schema={
                c: (
                    pl.Utf8
                    if c == "symbol"
                    else pl.Datetime
                    if c == "timestamp"
                    else pl.Float64
                )
                for c in _INTRADAY_COLUMNS
            }
        )

    if end_ts is None:
        end_ts = datetime.now(tz=UTC)

    placeholders = ", ".join(["?"] * len(symbols))
    query = (
        f"SELECT * FROM market_data_intraday "
        f"WHERE symbol IN ({placeholders}) "
        f"AND timestamp >= ? AND timestamp <= ? "
        f"ORDER BY symbol, timestamp"
    )
    params = [*symbols, start_ts, end_ts]
    try:
        df = conn.execute(query, params).pl()
    except duckdb.Error:
        logger.exception(
            "Failed to query market_data_intraday for symbols=%s, range=[%s, %s]",
            symbols,
            start_ts,
            end_ts,
        )
        raise

    logger.info(
        "Retrieved %d intraday rows for %d symbols from %s to %s",
        len(df),
        len(symbols),
        start_ts,
        end_ts,
    )
    return df


def get_market_data(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
    start_date: date_type | str,
    end_date: date_type | str | None = None,
) -> pl.DataFrame:
    """Read market data from the database for given symbols and date range.

    Parameters
    ----------
    conn:
        An open DuckDB connection.
    symbols:
        List of ticker symbols to retrieve.
    start_date:
        Earliest date (inclusive).  Accepts :class:`datetime.date` or an
        ISO-format string (``"2024-01-15"``).
    end_date:
        Latest date (inclusive).  When *None*, defaults to today.

    Returns
    -------
    pl.DataFrame
        Polars DataFrame with all columns from ``market_data_daily``,
        sorted by ``(symbol, date)``.  Empty DataFrame (with correct schema)
        when no rows match.
    """
    if not symbols:
        logger.warning("No symbols provided — returning empty DataFrame")
        return pl.DataFrame(
            schema={
                c: (
                    pl.Utf8 if c == "symbol" else pl.Date if c == "date" else pl.Float64
                )
                for c in _ALL_COLUMNS
            }
        )

    if end_date is None:
        end_date = datetime.now(tz=UTC).date()

    start_str = str(start_date)
    end_str = str(end_date)

    # Build parameterised query
    placeholders = ", ".join(["?"] * len(symbols))
    query = (
        f"SELECT * FROM market_data_daily "
        f"WHERE symbol IN ({placeholders}) "
        f"AND date >= ? AND date <= ? "
        f"ORDER BY symbol, date"
    )
    params = [*symbols, start_str, end_str]

    try:
        df = conn.execute(query, params).pl()
    except duckdb.Error:
        logger.exception(
            "Failed to query market_data_daily for symbols=%s, range=[%s, %s]",
            symbols,
            start_str,
            end_str,
        )
        raise

    logger.info(
        "Retrieved %d rows for %d symbols from %s to %s",
        len(df),
        len(symbols),
        start_str,
        end_str,
    )
    return df


def get_latest_date(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
) -> date_type | None:
    """Return the most recent date stored for *symbol*, or None.

    Useful for determining where an incremental fetch should start.

    Parameters
    ----------
    conn:
        An open DuckDB connection.
    symbol:
        The ticker symbol to look up.

    Returns
    -------
    datetime.date | None
        The latest date, or ``None`` if the symbol has no data.
    """
    try:
        result = conn.execute(
            "SELECT MAX(date) AS latest FROM market_data_daily WHERE symbol = ?",
            [symbol],
        ).fetchone()
    except duckdb.Error:
        logger.exception("Failed to query latest date for %s", symbol)
        raise

    if result is None or result[0] is None:
        logger.debug("No data found for symbol %s", symbol)
        return None

    latest = result[0]

    # DuckDB may return a datetime.date, datetime.datetime, or string
    if isinstance(latest, str):
        latest = datetime.strptime(latest, "%Y-%m-%d").replace(tzinfo=UTC).date()
    elif hasattr(latest, "date"):
        # datetime object
        latest = latest.date()

    logger.debug("Latest date for %s: %s", symbol, latest)
    return latest
