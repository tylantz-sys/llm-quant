from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import duckdb

from llm_quant.brain.models import Action, TradeSignal
from llm_quant.config import AppConfig, ProfitTakingMandateConfig


@dataclass
class HarvestGovernanceRuntimeResult:
    metrics: dict[str, Any] = field(default_factory=dict)
    breached_rules: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    allocation_scale: float = 1.0
    active_mandate_name: str | None = None
    active_mandate_type: str | None = None
    active_mandate: ProfitTakingMandateConfig | None = None
    conservative_mandate_name: str | None = None
    force_flatten: bool = False
    lifecycle_recommendation: str | None = None

    @property
    def has_actions(self) -> bool:
        return bool(self.actions or self.breached_rules)


def compute_peak_nav(
    conn: duckdb.DuckDBPyConnection,
    pod_id: str,
    initial_capital: float,
) -> float:
    row = conn.execute(
        """
        SELECT MAX(nav)
        FROM portfolio_snapshots
        WHERE pod_id = ?
        """,
        [pod_id],
    ).fetchone()
    peak_nav = float(row[0]) if row and row[0] is not None else float(initial_capital)
    return max(float(initial_capital), peak_nav)


def _normalize_runtime_timestamp(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def assess_intraday_symbol_freshness(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
    now_ts: datetime,
    max_age_minutes: int,
) -> tuple[list[str], list[str], dict[str, datetime]]:
    if not symbols:
        return [], [], {}

    latest_rows = conn.execute(
        """
        SELECT symbol, MAX(timestamp) AS latest_ts
        FROM market_data_intraday
        WHERE symbol IN ({placeholders})
        GROUP BY symbol
        """.format(placeholders=", ".join(["?"] * len(symbols))),
        symbols,
    ).fetchall()

    normalized_now_ts = _normalize_runtime_timestamp(now_ts)
    latest_by_symbol: dict[str, datetime] = {
        str(symbol): _normalize_runtime_timestamp(ts)
        for symbol, ts in latest_rows
        if ts is not None
    }
    max_age = timedelta(minutes=max_age_minutes)

    missing = sorted(symbol for symbol in symbols if symbol not in latest_by_symbol)
    stale = sorted(
        symbol
        for symbol, ts in latest_by_symbol.items()
        if (normalized_now_ts - ts) > max_age
    )
    return missing, stale, latest_by_symbol


def compute_recent_realized_expectancy(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
    lookback_closed_trades: int,
) -> tuple[float | None, int]:
    rows = conn.execute(
        """
        SELECT action, shares, price
        FROM trades
        WHERE pod_id = ?
        ORDER BY trade_id DESC
        """,
        [pod_id],
    ).fetchall()

    closed_pnls: list[float] = []
    open_lots: list[tuple[float, float]] = []

    for action, shares, price in reversed(rows):
        normalized_action = str(action).lower()
        qty = float(shares or 0.0)
        px = float(price or 0.0)

        if qty <= 0:
            continue

        if normalized_action == "buy":
            open_lots.append((qty, px))
            continue

        if normalized_action not in {"sell", "close"}:
            continue

        remaining = qty
        realized_pnl = 0.0
        while remaining > 0 and open_lots:
            lot_qty, lot_price = open_lots[0]
            matched = min(remaining, lot_qty)
            realized_pnl += (px - lot_price) * matched
            remaining -= matched
            lot_qty -= matched
            if lot_qty <= 0:
                open_lots.pop(0)
            else:
                open_lots[0] = (lot_qty, lot_price)

        closed_pnls.append(realized_pnl)
        if len(closed_pnls) >= lookback_closed_trades:
            break

    if not closed_pnls:
        return None, 0

    sample = list(reversed(closed_pnls[:lookback_closed_trades]))
    expectancy = sum(sample) / len(sample)
    return expectancy, len(sample)


def apply_expectancy_buy_scale(signals: list[TradeSignal], scale: float) -> int:
    applied = 0
    clamped_scale = max(0.0, scale)
    for idx, signal in enumerate(signals):
        if signal.action != Action.BUY:
            continue
        signals[idx] = replace(signal, target_weight=signal.target_weight * clamped_scale)
        applied += 1
    return applied


def filter_signals_by_asset_class(
    signals: list[TradeSignal],
    asset_class_map: dict[str, str],
    allowed_asset_classes: list[str] | None,
) -> tuple[list[TradeSignal], int]:
    if not allowed_asset_classes:
        return signals, 0

    allowed = {asset_class.lower() for asset_class in allowed_asset_classes}
    filtered = [
        signal
        for signal in signals
        if asset_class_map.get(signal.symbol, "").lower() in allowed
    ]
    return filtered, len(signals) - len(filtered)


def has_unprotected_crypto_positions(
    positions: dict[str, Any],
    asset_class_map: dict[str, str],
    exit_runtime: Any,
) -> bool:
    if getattr(exit_runtime, "exit_mode", "") == "synthetic":
        return False

    for symbol, position in positions.items():
        if asset_class_map.get(symbol, "").lower() != "crypto":
            continue
        stop_loss = float(getattr(position, "stop_loss", 0.0) or 0.0)
        shares = float(getattr(position, "shares", 0.0) or 0.0)
        if shares > 0 and stop_loss <= 0:
            return True
    return False


def resolve_active_profit_taking_mandate(
    config: AppConfig,
    pod_id: str = "default",
) -> tuple[str | None, ProfitTakingMandateConfig | None]:
    resolver = getattr(config, "resolve_active_profit_taking_mandate", None)
    if callable(resolver):
        resolved = resolver(pod_id)
        if isinstance(resolved, tuple) and len(resolved) == 2:
            return resolved[0], resolved[1]
        if resolved is not None:
            return getattr(config, "active_profit_taking_mandate_name", None), resolved

    active_name = getattr(config, "active_profit_taking_mandate_name", None)
    active_mandate = getattr(config, "active_profit_taking_mandate", None)
    if active_name is not None and active_mandate is not None:
        return active_name, active_mandate

    governance = getattr(config, "governance", None)
    profit_taking = getattr(governance, "profit_taking", None)
    mandates = getattr(profit_taking, "mandates", None)
    fallback_name = "crypto" if pod_id == "crypto" and hasattr(mandates, "crypto") else "default"
    fallback = getattr(mandates, fallback_name, None)
    return fallback_name if fallback is not None else None, fallback


def load_latest_harvest_governance_result(
    conn: duckdb.DuckDBPyConnection,
    *,
    config: AppConfig | None = None,
    pod_id: str = "default",
) -> HarvestGovernanceRuntimeResult:
    active_mandate_name = None
    active_mandate = None
    if config is not None:
        active_mandate_name, active_mandate = resolve_active_profit_taking_mandate(
            config,
            pod_id,
        )
    active_mandate_type = (
        active_mandate.mandate_type if active_mandate is not None else None
    )

    try:
        row = conn.execute(
            """
            SELECT checks_json
            FROM surveillance_scans
            WHERE pod_id = ?
            ORDER BY scan_timestamp DESC
            LIMIT 1
            """,
            [pod_id],
        ).fetchone()
    except duckdb.BinderException:
        row = conn.execute(
            """
            SELECT checks_json
            FROM surveillance_scans
            ORDER BY scan_timestamp DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None or row[0] is None:
        return HarvestGovernanceRuntimeResult(
            active_mandate_name=active_mandate_name,
            active_mandate_type=active_mandate_type,
            active_mandate=active_mandate,
        )

    try:
        checks = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return HarvestGovernanceRuntimeResult(
            active_mandate_name=active_mandate_name,
            active_mandate_type=active_mandate_type,
            active_mandate=active_mandate,
        )

    for check in checks:
        if check.get("detector") != "harvest_governance":
            continue
        details = check.get("details") or {}
        actions = list(details.get("recommended_actions") or [])
        allocation_scale = 1.0
        conservative_mandate_name = None
        force_flatten = False
        lifecycle_recommendation = None

        for action in actions:
            action_name = action.get("action")
            if action_name == "allocation_shrink":
                allocation_scale = float(action.get("scale", 1.0))
            elif action_name == "apply_conservative_mandate":
                conservative_mandate_name = action.get("mandate_name")
            elif action_name == "temporary_eod_flatten":
                force_flatten = bool(action.get("enabled", True))
            elif action_name in {"demote_strategy", "paper_revalidate"}:
                lifecycle_recommendation = action_name

        return HarvestGovernanceRuntimeResult(
            metrics=dict(details.get("observed_metrics") or {}),
            breached_rules=list(details.get("breached_metrics") or []),
            actions=actions,
            allocation_scale=allocation_scale,
            active_mandate_name=active_mandate_name,
            active_mandate_type=active_mandate_type,
            active_mandate=active_mandate,
            conservative_mandate_name=conservative_mandate_name,
            force_flatten=force_flatten,
            lifecycle_recommendation=lifecycle_recommendation,
        )

    return HarvestGovernanceRuntimeResult(
        active_mandate_name=active_mandate_name,
        active_mandate_type=active_mandate_type,
        active_mandate=active_mandate,
    )


def apply_harvest_governance_controls(
    signals: list[TradeSignal],
    runtime_result: HarvestGovernanceRuntimeResult,
    portfolio_symbols: set[str] | None = None,
) -> list[TradeSignal]:
    adjusted: list[TradeSignal] = []
    portfolio_symbols = portfolio_symbols or set()

    if runtime_result.force_flatten:
        existing_close_symbols = {
            signal.symbol for signal in signals if signal.action == Action.CLOSE
        }
        adjusted.extend(signals)
        for symbol in sorted(portfolio_symbols - existing_close_symbols):
            adjusted.append(
                TradeSignal(
                    symbol=symbol,
                    action=Action.CLOSE,
                    target_weight=0.0,
                    conviction=signals[0].conviction if signals else "medium",
                    reasoning="Harvest governance forced temporary EOD flatten.",
                    stop_loss=0.0,
                    take_profit=0.0,
                    strategy_id="harvest_governance",
                    exit_reason="harvest_governance_flatten",
                )
            )
        return adjusted

    scale = max(0.0, min(runtime_result.allocation_scale, 1.0))
    for signal in signals:
        if signal.action == Action.BUY:
            adjusted.append(
                replace(signal, target_weight=signal.target_weight * scale)
            )
        else:
            adjusted.append(signal)
    return adjusted


def log_harvest_governance_action(
    conn: duckdb.DuckDBPyConnection,
    *,
    pod_id: str,
    runtime_result: HarvestGovernanceRuntimeResult,
) -> None:
    if not runtime_result.has_actions:
        return

    payload = json.dumps(
        {
            "pod_id": pod_id,
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "metrics": runtime_result.metrics,
            "breached_rules": runtime_result.breached_rules,
            "actions": runtime_result.actions,
            "allocation_scale": runtime_result.allocation_scale,
            "active_mandate_name": runtime_result.active_mandate_name,
            "active_mandate_type": runtime_result.active_mandate_type,
            "active_mandate": (
                asdict(runtime_result.active_mandate)
                if runtime_result.active_mandate is not None
                else None
            ),
            "conservative_mandate_name": runtime_result.conservative_mandate_name,
            "force_flatten": runtime_result.force_flatten,
            "lifecycle_recommendation": runtime_result.lifecycle_recommendation,
        },
        default=str,
    )

    conn.execute(
        """
        INSERT INTO decision_contexts (
            decision_id, pod_id, timestamp, context_json
        ) VALUES (nextval('seq_decision_id'), ?, ?, ?)
        """,
        [pod_id, datetime.now(tz=UTC), payload],
    )
    conn.commit()
