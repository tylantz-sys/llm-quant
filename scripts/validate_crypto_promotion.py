#!/usr/bin/env python3
"""Validate crypto strategy lifecycle readiness for promotion sets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

# Ensure src/ is importable when run as a script from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_latest_registry_entry(path: Path) -> dict:
    if not path.exists():
        return {}
    latest = {}
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            stripped = raw.strip()
            if not stripped:
                continue
            latest = json.loads(stripped)
    return latest


def _pass_backtest(entry: dict) -> bool:
    if not entry:
        return False
    sharpe = float(entry.get("sharpe_ratio", 0.0) or 0.0)
    max_dd = abs(float(entry.get("max_drawdown", 1.0) or 1.0))
    dsr = float(entry.get("dsr", 0.0) or 0.0)
    return sharpe > 0.0 and dsr >= 0.95 and max_dd <= 0.25


def _pass_walk_forward(path: Path) -> bool:
    payload = _load_yaml(path)
    return bool(payload.get("passed", False))


def _pass_robustness(path: Path) -> bool:
    payload = _load_yaml(path)
    if not payload:
        return False
    return bool(payload.get("overall_passed", False))


def _pass_paper_shadow(path: Path) -> bool:
    payload = _load_yaml(path)
    if not payload:
        return False
    status = str(payload.get("status", "")).lower()
    if status in {"pass", "passed", "ready", "complete"}:
        return True
    return bool(payload.get("passed", False))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate crypto strategy readiness for promotion."
    )
    parser.add_argument(
        "--set",
        default="promoted_crypto",
        help="Strategy set name in config/strategies/catalog.toml",
    )
    parser.add_argument(
        "--config-dir",
        default="config",
        help="Config directory containing strategies/catalog.toml",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Data directory containing strategies/<slug>/ artifacts",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero exit code if any strategy is not ready.",
    )
    parser.add_argument(
        "--require-paper",
        choices=("auto", "yes", "no"),
        default="auto",
        help=(
            "Whether paper-trading gate is required. "
            "'auto' requires paper for promoted* sets and skips paper for candidate* sets."
        ),
    )
    args = parser.parse_args()

    if args.require_paper == "yes":
        require_paper = True
    elif args.require_paper == "no":
        require_paper = False
    else:
        set_name = str(args.set).lower()
        require_paper = not set_name.startswith("candidate")

    from llm_quant.strategies.runtime import load_strategy_catalog

    catalog = load_strategy_catalog(config_dir=Path(args.config_dir))
    slugs = catalog.get(args.set, [])
    if not slugs:
        print(f"No strategies found for set '{args.set}'.")
        return 1 if args.strict else 0

    base = Path(args.data_dir) / "strategies"
    rows: list[tuple[str, bool, bool, bool, bool, bool, bool]] = []
    for slug in slugs:
        strat_dir = base / slug
        spec = _load_yaml(strat_dir / "research-spec.yaml")
        frozen = bool(spec.get("frozen", False))
        backtest_ok = _pass_backtest(
            _load_latest_registry_entry(strat_dir / "experiment-registry.jsonl")
        )
        walk_ok = _pass_walk_forward(strat_dir / "walk-forward.yaml")
        robust_ok = _pass_robustness(strat_dir / "robustness.yaml")
        paper_ok = _pass_paper_shadow(strat_dir / "paper-trading.yaml")
        if require_paper:
            ready = frozen and backtest_ok and walk_ok and robust_ok and paper_ok
        else:
            ready = frozen and backtest_ok and walk_ok and robust_ok
        rows.append((slug, frozen, backtest_ok, walk_ok, robust_ok, paper_ok, ready))

    header = (
        "slug",
        "frozen_spec",
        "backtest",
        "walk_forward",
        "robustness",
        "paper_shadow",
        "ready",
    )
    widths = [
        max(len(str(col[i])) for col in [header, *rows]) for i in range(len(header))
    ]
    print(" ".join(str(header[i]).ljust(widths[i]) for i in range(len(header))))
    print(" ".join("-" * widths[i] for i in range(len(header))))
    for row in rows:
        print(" ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))))

    ready_count = sum(1 for row in rows if row[-1])
    gate_label = (
        "frozen+backtest+walk_forward+robustness+paper"
        if require_paper
        else "frozen+backtest+walk_forward+robustness"
    )
    print(f"\nGate: {gate_label}")
    print(f"Ready: {ready_count}/{len(rows)} in set '{args.set}'")
    if args.strict and ready_count != len(rows):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
