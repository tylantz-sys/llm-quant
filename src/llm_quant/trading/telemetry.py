"""Decision telemetry logging helpers."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import duckdb

from llm_quant.brain.models import MarketContext


def log_decision_context(
    conn: duckdb.DuckDBPyConnection,
    decision_id: int,
    pod_id: str,
    context: MarketContext,
    extra: dict[str, Any] | None = None,
) -> None:
    payload_dict: dict[str, Any] = asdict(context)
    if extra:
        payload_dict["runtime"] = extra
    payload = json.dumps(payload_dict, default=str)
    conn.execute(
        """
        INSERT INTO decision_contexts (
            decision_id, pod_id, timestamp, context_json
        ) VALUES (?, ?, ?, ?)
        """,
        [decision_id, pod_id, datetime.now(tz=UTC), payload],
    )
    conn.commit()


__all__ = ["log_decision_context"]
