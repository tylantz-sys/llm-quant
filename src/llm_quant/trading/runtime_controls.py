"""Runtime control helpers for intraday sleeves.

These helpers keep run-time safety and throttles testable and centralized.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import duckdb

from llm_quant.brain.models import Action, TradeSignal


def compute_peak_nav(
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
    initial_capital: float,
) -> float:
    """Return pod peak NAV from snapshots, falling back to initial capital."""
    try:
        snap_cols = {
            row[0] for row in conn.execute("DESCRIBE portfolio_snapshots").fetchall()
        }
    except duckdb.Error:
        return float(initial_capital)
    if "pod_id" in snap_cols:
        row = conn.execute(
            "SELECT MAX(nav) FROM portfolio_snapshots WHERE pod_id = ?",
            [pod_id],
        ).fetchone()
    else:
        row = conn.execute("SELECT MAX(nav) FROM portfolio_snapshots").fetchone()

    peak_nav = float(row[0]) if row and row[0] is not None else float(initial_capital)
    if peak_nav <= 0:
        peak_nav = float(initial_capital)
    return max(peak_nav, float(initial_capital))


def assess_intraday_symbol_freshness(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
    now_ts: datetime,
    max_age_minutes: int,
) -> tuple[list[str], list[str], dict[str, datetime]]:
    """Return (missing_symbols, stale_symbols, latest_by_symbol)."""
    unique_symbols = sorted({s for s in symbols if s})
    if not unique_symbols:
        return [], [], {}

    placeholders = ", ".join(["?"] * len(unique_symbols))
    try:
        rows = conn.execute(
            f"""
            SELECT symbol, MAX(timestamp) AS ts
            FROM market_data_intraday
            WHERE symbol IN ({placeholders})
            GROUP BY symbol
            """,
            unique_symbols,
        ).fetchall()
    except duckdb.Error:
        return unique_symbols, [], {}

    now_utc = _as_utc(now_ts)
    max_age = timedelta(minutes=max(max_age_minutes, 0))
    latest_by_symbol: dict[str, datetime] = {}
    missing_symbols: list[str] = []
    stale_symbols: list[str] = []

    ts_map = {str(row[0]): row[1] for row in rows}
    for symbol in unique_symbols:
        ts = ts_map.get(symbol)
        if ts is None:
            missing_symbols.append(symbol)
            continue
        ts_utc = _as_utc(ts)
        latest_by_symbol[symbol] = ts_utc
        if now_utc - ts_utc > max_age:
            stale_symbols.append(symbol)

    return missing_symbols, stale_symbols, latest_by_symbol


def compute_recent_realized_expectancy(
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
    lookback_closed_trades: int,
) -> tuple[float | None, int]:
    """Compute average realized PnL over last N closed trade matches (FIFO)."""
    if lookback_closed_trades <= 0:
        return None, 0

    try:
        trade_cols = {row[0] for row in conn.execute("DESCRIBE trades").fetchall()}
    except duckdb.Error:
        return None, 0
    has_pod = "pod_id" in trade_cols
    if has_pod:
        rows = conn.execute(
            """
            SELECT symbol, action, shares, price
            FROM trades
            WHERE pod_id = ?
            ORDER BY trade_id ASC
            """,
            [pod_id],
        ).fetchall()
    else:
        rows = conn.execute("""
            SELECT symbol, action, shares, price
            FROM trades
            ORDER BY trade_id ASC
            """).fetchall()

    lots: dict[str, list[list[float]]] = defaultdict(list)
    realized: list[float] = []

    for symbol, action, shares, price in rows:
        qty = float(shares or 0.0)
        px = float(price or 0.0)
        if not symbol or qty <= 0 or px <= 0:
            continue

        action_l = str(action).lower()
        if action_l == "buy":
            lots[str(symbol)].append([qty, px])
            continue
        if action_l not in {"sell", "close"}:
            continue

        queue = lots[str(symbol)]
        remaining = qty
        while remaining > 0 and queue:
            lot_qty, lot_px = queue[0]
            matched = min(remaining, lot_qty)
            realized.append((px - lot_px) * matched)

            lot_qty -= matched
            remaining -= matched
            if lot_qty <= 0:
                queue.pop(0)
            else:
                queue[0][0] = lot_qty

    sample_size = len(realized)
    if sample_size < lookback_closed_trades:
        return None, sample_size

    window = realized[-lookback_closed_trades:]
    return sum(window) / lookback_closed_trades, sample_size


def apply_expectancy_buy_scale(
    signals: list[TradeSignal],
    scale: float,
) -> int:
    """Scale BUY target weights in place; return number of scaled BUY signals."""
    if scale <= 0:
        return 0

    scaled = 0
    for signal in signals:
        if signal.action != Action.BUY:
            continue
        signal.target_weight = round(max(signal.target_weight * scale, 0.0), 4)
        scaled += 1
    return scaled


def filter_signals_by_asset_class(
    signals: list[TradeSignal],
    asset_class_map: dict[str, str],
    allowed_asset_classes: list[str] | set[str],
) -> tuple[list[TradeSignal], int]:
    """Return signals limited to allowed asset classes and count filtered out."""
    allowed = {str(cls).lower() for cls in allowed_asset_classes if cls}
    if not allowed:
        return signals, 0

    allowed_symbols = {
        symbol
        for symbol, asset_class in asset_class_map.items()
        if str(asset_class).lower() in allowed
    }
    filtered = [signal for signal in signals if signal.symbol in allowed_symbols]
    return filtered, len(signals) - len(filtered)


def _as_utc(ts: Any) -> datetime:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC)
    raise TypeError(f"Expected datetime, got {type(ts).__name__}")
