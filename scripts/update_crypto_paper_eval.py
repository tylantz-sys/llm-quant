"""Update crypto paper-stage gate metrics from DB telemetry."""

from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

import duckdb
import yaml

# Ensure src/ is importable when run as a script from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_quant.backtest.metrics import compute_max_drawdown, compute_sharpe
from llm_quant.strategies.runtime import load_strategy_catalog


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update paper-trading gate status for a crypto candidate slug."
    )
    parser.add_argument("--slug", required=True, help="Strategy slug to update.")
    parser.add_argument("--pod", required=True, help="Pod ID for telemetry lookup.")
    parser.add_argument("--db", default="data/llm_quant.duckdb", help="DuckDB path.")
    parser.add_argument("--data-dir", default="data", help="Data directory.")
    parser.add_argument("--config-dir", default="config", help="Config directory.")
    parser.add_argument(
        "--auto-promote",
        action="store_true",
        help="Move slug candidate_crypto -> promoted_crypto when gate passes.",
    )
    return parser.parse_args()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False)


def _load_latest_registry_entry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    latest: dict[str, Any] = {}
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            stripped = raw.strip()
            if stripped:
                latest = json.loads(stripped)
    return latest


def _default_paper_payload(slug: str) -> dict[str, Any]:
    return {
        "strategy_slug": slug,
        "status": "pending",
        "start_date": None,
        "baseline": {},
        "performance": {},
        "gate_criteria": {
            "min_days": 30,
            "min_trades": 50,
            "sharpe_floor": 0.60,
            "max_drawdown_limit": 0.25,
            "operational_checks_required": True,
        },
        "operational_checks": {},
        "gate_status": {"passed": False, "reasons": []},
    }


def _update_baseline(paper: dict[str, Any], latest: dict[str, Any]) -> None:
    baseline = paper.setdefault("baseline", {})
    if not latest or baseline.get("experiment_id"):
        return
    baseline["experiment_id"] = latest.get("experiment_id")
    baseline["sharpe_ratio"] = float(latest.get("sharpe_ratio", 0.0) or 0.0)
    baseline["max_drawdown"] = float(latest.get("max_drawdown", 0.0) or 0.0)
    baseline["total_return"] = float(latest.get("total_return", 0.0) or 0.0)
    baseline["dsr"] = float(latest.get("dsr", 0.0) or 0.0)
    baseline["total_trades"] = int(latest.get("total_trades", 0) or 0)


def _load_db_telemetry(
    db_path: Path,
    pod: str,
    *,
    max_retries: int = 20,
    retry_seconds: float = 1.0,
) -> tuple[list[tuple[Any, float]], int, int]:
    attempts = 0
    while True:
        attempts += 1
        try:
            conn = duckdb.connect(str(db_path), read_only=True)
            try:
                snapshots = conn.execute(
                    """
                    SELECT date, nav
                    FROM portfolio_snapshots
                    WHERE pod_id = ?
                    ORDER BY date
                    """,
                    [pod],
                ).fetchall()
                closed_trades = int(
                    _first_count(
                        conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM trades
                        WHERE pod_id = ? AND lower(action) IN ('sell', 'cover', 'close')
                        """,
                        [pod],
                        ).fetchone()
                    )
                )
                decisions = int(
                    _first_count(
                        conn.execute(
                            "SELECT COUNT(*) FROM llm_decisions WHERE pod_id = ?",
                            [pod],
                        ).fetchone()
                    )
                )
            finally:
                conn.close()
        except duckdb.IOException as exc:
            is_lock = "Could not set lock on file" in str(exc)
            if (not is_lock) or attempts >= max_retries:
                raise
            time.sleep(retry_seconds)
        else:
            return snapshots, closed_trades, decisions


def _compute_days_observed(snapshot_dates: list[date]) -> int:
    if not snapshot_dates:
        return 0
    return max(1, (snapshot_dates[-1] - snapshot_dates[0]).days + 1)


def _compute_performance(snapshots: list[tuple[Any, float]]) -> dict[str, Any]:
    nav_series = [float(nav) for _, nav in snapshots]
    dates = [d for d, _ in snapshots]
    returns = [
        curr / prev - 1.0 if prev else 0.0 for prev, curr in pairwise(nav_series)
    ]
    sharpe = compute_sharpe(returns, annualize=True) if returns else 0.0
    max_dd = compute_max_drawdown(nav_series)[0] if len(nav_series) > 1 else 0.0
    return {
        "start_date": str(dates[0]) if dates else None,
        "initial_nav": nav_series[0] if nav_series else None,
        "current_nav": nav_series[-1] if nav_series else None,
        "peak_nav": max(nav_series) if nav_series else None,
        "sharpe": round(sharpe, 6) if returns else None,
        "max_drawdown": round(max_dd, 6) if nav_series else None,
        "days_observed": _compute_days_observed(dates),
        "has_nav": bool(nav_series),
        "raw_sharpe": sharpe,
        "raw_max_dd": max_dd,
    }


def _update_operational_checks(
    paper: dict[str, Any],
    decisions: int,
    has_activity: bool,
    has_nav: bool,
) -> dict[str, Any]:
    checks = cast(dict[str, Any], dict(paper.setdefault("operational_checks", {})))
    checks["timer_healthy"] = has_nav
    checks["decision_logging"] = decisions > 0
    checks["order_flow_healthy"] = has_activity
    checks.setdefault("db_lock_errors_last_24h", None)
    paper["operational_checks"] = checks
    return checks


def _first_count(row: tuple[Any, ...] | None) -> int:
    if row is None:
        return 0
    return int(row[0])


def _evaluate_gate(
    paper: dict[str, Any],
    perf: dict[str, Any],
    closed_trades: int,
    op_checks: dict[str, Any],
) -> tuple[bool, list[str]]:
    criteria = paper.setdefault("gate_criteria", {})
    min_days = int(criteria.get("min_days", 30) or 30)
    min_trades = int(criteria.get("min_trades", 50) or 50)
    sharpe_floor = float(criteria.get("sharpe_floor", 0.60) or 0.60)
    max_dd_limit = float(criteria.get("max_drawdown_limit", 0.25) or 0.25)
    require_ops = bool(criteria.get("operational_checks_required", True))

    reasons: list[str] = []
    if perf["days_observed"] < min_days:
        reasons.append(f"days_observed={perf['days_observed']} < min_days={min_days}")
    if closed_trades < min_trades:
        reasons.append(f"closed_trades={closed_trades} < min_trades={min_trades}")
    if perf["sharpe"] is not None and perf["raw_sharpe"] < sharpe_floor:
        reasons.append(
            f"sharpe={perf['raw_sharpe']:.3f} < sharpe_floor={sharpe_floor:.3f}"
        )
    if perf["has_nav"] and perf["raw_max_dd"] > max_dd_limit:
        reasons.append(
            f"max_drawdown={perf['raw_max_dd']:.3f} > limit={max_dd_limit:.3f}"
        )
    if require_ops:
        failed = [k for k, v in op_checks.items() if isinstance(v, bool) and not v]
        if failed:
            reasons.append("operational_checks_failed=" + ",".join(sorted(failed)))
    passed = perf["has_nav"] and not reasons
    return passed, reasons


def _write_catalog(catalog_path: Path, sets: dict[str, list[str]]) -> None:
    existing_sets = {}
    if catalog_path.exists():
        with catalog_path.open("rb") as f:
            raw = tomllib.load(f)
        loaded_sets = raw.get("sets", {}) if isinstance(raw, dict) else {}
        if isinstance(loaded_sets, dict):
            existing_sets = loaded_sets

    ordered_keys = list(existing_sets.keys())
    for name in sets:
        if name not in ordered_keys:
            ordered_keys.append(name)

    lines: list[str] = ["[sets]"]
    for set_name in ordered_keys:
        lines.append(f"{set_name} = [")
        values = sets.get(set_name, [])
        lines.extend([f'  "{slug}",' for slug in values])
        lines.extend(["]", ""])
    catalog_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _promote_slug(slug: str, config_dir: Path) -> bool:
    catalog_path = config_dir / "strategies" / "catalog.toml"
    catalog = load_strategy_catalog(config_dir=config_dir)
    candidate = list(catalog.get("candidate_crypto", []))
    promoted = list(catalog.get("promoted_crypto", []))
    if slug not in candidate:
        return False
    catalog["candidate_crypto"] = [s for s in candidate if s != slug]
    if slug not in promoted:
        promoted.append(slug)
    catalog["promoted_crypto"] = promoted
    _write_catalog(catalog_path, catalog)
    return True


def main() -> int:
    args = _parse_args()
    slug = args.slug
    now_iso = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    strat_dir = Path(args.data_dir) / "strategies" / slug
    paper_path = strat_dir / "paper-trading.yaml"
    registry_path = strat_dir / "experiment-registry.jsonl"

    paper = _load_yaml(paper_path) or _default_paper_payload(slug)
    _update_baseline(paper, _load_latest_registry_entry(registry_path))

    snapshots, closed_trades, decisions = _load_db_telemetry(Path(args.db), args.pod)
    perf = _compute_performance(snapshots)
    op_checks = _update_operational_checks(
        paper=paper,
        decisions=decisions,
        has_activity=bool(closed_trades > 0 or decisions > 0),
        has_nav=perf["has_nav"],
    )
    passed, reasons = _evaluate_gate(paper, perf, closed_trades, op_checks)

    performance = paper.setdefault("performance", {})
    performance.update(
        {
            "initial_nav": perf["initial_nav"],
            "current_nav": perf["current_nav"],
            "peak_nav": perf["peak_nav"],
            "sharpe": perf["sharpe"],
            "max_drawdown": perf["max_drawdown"],
            "closed_trades": closed_trades,
            "days_observed": perf["days_observed"],
        }
    )
    if perf["start_date"] and not paper.get("start_date"):
        paper["start_date"] = perf["start_date"]
    paper["status"] = "pass" if passed else ("active" if perf["has_nav"] else "pending")
    paper["gate_status"] = {"passed": passed, "reasons": reasons}
    paper["updated_at"] = now_iso
    _save_yaml(paper_path, paper)

    promoted = bool(
        passed and args.auto_promote and _promote_slug(slug, Path(args.config_dir))
    )
    print(f"Updated: {paper_path}")
    print(f"status={paper['status']} passed={passed} reasons={len(reasons)}")
    if promoted:
        print(
            "Promotion handoff complete: moved slug from candidate_crypto to promoted_crypto."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
