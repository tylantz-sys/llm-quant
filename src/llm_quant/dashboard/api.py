"""Read-only dashboard API for Grafana/Infinity."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from llm_quant.brain.parser import parse_trading_decision
from llm_quant.config import load_config

app = FastAPI(title="llm-quant dashboard api", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

LOCK_WAIT_SECONDS = float(os.environ.get("LLM_QUANT_DASHBOARD_LOCK_WAIT_SECS", "60"))
LOCK_RETRY_SECONDS = float(os.environ.get("LLM_QUANT_DASHBOARD_LOCK_RETRY_SECS", "0.5"))

_CACHE: dict[str, Any] = {}
_CACHE_TS: dict[str, str] = {}


def _db_path() -> str:
    config = load_config()
    return config.general.db_path


def _connect() -> duckdb.DuckDBPyConnection:
    deadline = time.monotonic() + max(LOCK_WAIT_SECONDS, 0.0)
    while True:
        try:
            return duckdb.connect(_db_path(), read_only=True)
        except duckdb.IOException as exc:
            if not _is_lock_error(exc):
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(max(LOCK_RETRY_SECONDS, 0.0))


def _fetch_all(
    conn: duckdb.DuckDBPyConnection, query: str, params: list[Any]
) -> list[dict[str, Any]]:
    cursor = conn.execute(query, params)
    cols = [col[0] for col in cursor.description]
    return [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]


def _is_lock_error(exc: Exception) -> bool:
    message = str(exc)
    return "Conflicting lock" in message or "Could not set lock" in message


def _cache_put(key: str, payload: dict[str, Any]) -> None:
    _CACHE[key] = payload
    _CACHE_TS[key] = datetime.now(tz=UTC).isoformat()


def _cache_get(key: str) -> tuple[dict[str, Any] | None, str | None]:
    return _CACHE.get(key), _CACHE_TS.get(key)


def _query_with_cache(
    *,
    cache_key: str,
    query: str,
    params: list[Any],
) -> dict[str, Any]:
    try:
        conn = _connect()
        try:
            rows = _fetch_all(conn, query, params)
        finally:
            conn.close()
    except duckdb.IOException as exc:
        if _is_lock_error(exc):
            cached, cached_at = _cache_get(cache_key)
            if cached is not None:
                return {
                    "data": cached["data"],
                    "meta": {
                        "stale": True,
                        "cached_at": cached_at,
                        "warning": "database locked; serving cached response",
                    },
                }
            return {
                "data": [],
                "meta": {
                    "stale": True,
                    "cached_at": None,
                    "warning": "database locked; no cached response yet",
                },
            }
        raise

    payload = {"data": rows, "meta": {"stale": False, "cached_at": None}}
    _cache_put(cache_key, payload)
    return payload


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "db_path": _db_path(),
        "time": datetime.now(tz=UTC).isoformat(),
    }


@app.get("/runs")
def list_runs(
    limit: int = Query(100, ge=1, le=1000),
    pod_id: str = "default",
) -> dict[str, Any]:
    query = """
        WITH latest_snap AS (
            SELECT pod_id, date, max(snapshot_id) AS snapshot_id
            FROM portfolio_snapshots
            GROUP BY pod_id, date
        ),
        snaps AS (
            SELECT s.*
            FROM portfolio_snapshots s
            JOIN latest_snap ls ON s.snapshot_id = ls.snapshot_id
        )
        SELECT
            d.decision_id,
            d.created_at,
            d.date,
            d.pod_id,
            d.model,
            d.market_regime,
            d.regime_confidence,
            d.num_signals,
            d.cost_usd,
            COALESCE(COUNT(t.trade_id), 0) AS trades_executed,
            COALESCE(SUM(t.notional), 0) AS notional_executed,
            s.nav,
            s.cash,
            s.gross_exposure,
            s.net_exposure
        FROM llm_decisions d
        LEFT JOIN trades t ON t.llm_decision_id = d.decision_id
        LEFT JOIN snaps s ON s.pod_id = d.pod_id AND s.date = d.date
        WHERE d.pod_id = ?
        GROUP BY
            d.decision_id,
            d.created_at,
            d.date,
            d.pod_id,
            d.model,
            d.market_regime,
            d.regime_confidence,
            d.num_signals,
            d.cost_usd,
            s.nav,
            s.cash,
            s.gross_exposure,
            s.net_exposure
        ORDER BY d.created_at DESC
        LIMIT ?
    """
    return _query_with_cache(
        cache_key=f"runs:{pod_id}:{limit}",
        query=query,
        params=[pod_id, limit],
    )


@app.get("/decisions/{decision_id}")
def get_decision(decision_id: int) -> dict[str, Any]:
    cache_key = f"decision:{decision_id}"
    query = """
        SELECT
            decision_id,
            date,
            pod_id,
            model,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            cost_usd,
            market_regime,
            regime_confidence,
            num_signals,
            raw_response,
            created_at
        FROM llm_decisions
        WHERE decision_id = ?
    """
    try:
        conn = _connect()
        try:
            rows = _fetch_all(conn, query, [decision_id])
        finally:
            conn.close()
    except duckdb.IOException as exc:
        if _is_lock_error(exc):
            cached, cached_at = _cache_get(cache_key)
            if cached is not None:
                cached["meta"] = {
                    "stale": True,
                    "cached_at": cached_at,
                    "warning": "database locked; serving cached response",
                }
                return cached
            return {
                "decision": {},
                "portfolio_commentary": "",
                "signals": [],
                "parse_error": "database locked; no cached response yet",
                "meta": {
                    "stale": True,
                    "cached_at": None,
                    "warning": "database locked; no cached response yet",
                },
            }
        raise

    if not rows:
        raise HTTPException(status_code=404, detail="decision_id not found")

    decision = rows[0]
    raw_response = decision.get("raw_response") or ""

    signals: list[dict[str, Any]] = []
    parse_error: str | None = None
    portfolio_commentary = ""
    try:
        parsed = parse_trading_decision(raw_response, decision["date"])
        portfolio_commentary = parsed.portfolio_commentary
        signals = [
            {
                "symbol": s.symbol,
                "action": s.action.value,
                "conviction": s.conviction.value,
                "target_weight": s.target_weight,
                "stop_loss": s.stop_loss,
                "take_profit": getattr(s, "take_profit", 0.0),
                "reasoning": s.reasoning,
            }
            for s in parsed.signals
        ]
    except Exception as exc:  # noqa: BLE001 - surface parse errors in the API
        parse_error = str(exc)

    response = {
        "decision": decision,
        "portfolio_commentary": portfolio_commentary,
        "signals": signals,
        "parse_error": parse_error,
        "meta": {"stale": False, "cached_at": None},
    }
    _cache_put(cache_key, response)
    return response


@app.get("/trades")
def list_trades(
    limit: int = Query(200, ge=1, le=2000),
    pod_id: str = "default",
) -> dict[str, Any]:
    query = """
        SELECT
            trade_id,
            date,
            pod_id,
            symbol,
            action,
            shares,
            price,
            notional,
            conviction,
            reasoning,
            llm_decision_id,
            broker_order_id,
            broker_status,
            created_at
        FROM trades
        WHERE pod_id = ?
        ORDER BY trade_id DESC
        LIMIT ?
    """
    return _query_with_cache(
        cache_key=f"trades:{pod_id}:{limit}",
        query=query,
        params=[pod_id, limit],
    )


@app.get("/positions/latest")
def latest_positions(pod_id: str = "default") -> dict[str, Any]:
    query = """
        WITH latest AS (
            SELECT pod_id, max(snapshot_id) AS snapshot_id
            FROM portfolio_snapshots
            WHERE pod_id = ?
            GROUP BY pod_id
        )
        SELECT
            s.snapshot_id,
            s.date,
            s.nav,
            s.cash,
            s.gross_exposure,
            s.net_exposure,
            p.symbol,
            p.shares,
            p.avg_cost,
            p.current_price,
            p.market_value,
            p.unrealized_pnl,
            p.weight,
            p.stop_loss
        FROM positions p
        JOIN latest l ON p.snapshot_id = l.snapshot_id
        JOIN portfolio_snapshots s ON s.snapshot_id = p.snapshot_id
        ORDER BY p.symbol
    """
    return _query_with_cache(
        cache_key=f"positions:{pod_id}",
        query=query,
        params=[pod_id],
    )
