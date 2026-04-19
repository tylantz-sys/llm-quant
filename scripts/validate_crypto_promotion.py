from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

REQUIRED_BASE_ARTIFACTS = (
    "research-spec.yaml",
    "experiment-registry.jsonl",
    "walk-forward.yaml",
    "robustness.yaml",
)
REQUIRED_PROMOTED_EXTRA_ARTIFACTS = ("paper-trading.yaml",)


def _load_catalog(path: Path) -> dict[str, list[str]]:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    sets = raw.get("sets", {})
    if not isinstance(sets, dict):
        return {}
    catalog: dict[str, list[str]] = {}
    for key, value in sets.items():
        if isinstance(value, list):
            catalog[str(key)] = [str(item) for item in value]
    return catalog


def _required_artifacts(set_name: str) -> tuple[str, ...]:
    if set_name.startswith("promoted"):
        return REQUIRED_BASE_ARTIFACTS + REQUIRED_PROMOTED_EXTRA_ARTIFACTS
    return REQUIRED_BASE_ARTIFACTS


def _validate_set(set_name: str, slugs: list[str], data_dir: Path) -> list[str]:
    errors: list[str] = []
    required = _required_artifacts(set_name)
    for slug in slugs:
        strategy_dir = data_dir / "strategies" / slug
        if not strategy_dir.exists():
            errors.append(f"{slug}: missing strategy directory {strategy_dir}")
            continue
        for artifact in required:
            if not (strategy_dir / artifact).exists():
                errors.append(
                    f"{slug}: missing required artifact {artifact} for set {set_name}"
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate crypto promotion artifacts")
    parser.add_argument("--set", required=True, dest="set_name")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()

    config_dir = Path(args.config_dir)
    data_dir = Path(args.data_dir)
    catalog_path = config_dir / "strategies" / "catalog.toml"

    if not catalog_path.exists():
        print(f"Missing catalog: {catalog_path}")
        return 1

    catalog = _load_catalog(catalog_path)
    slugs = catalog.get(args.set_name)
    if slugs is None:
        print(f"Unknown set: {args.set_name}")
        return 1

    errors = _validate_set(args.set_name, slugs, data_dir)
    if errors:
        for err in errors:
            print(err)
        return 1 if args.strict else 0

    print(f"Validation passed for set {args.set_name} ({len(slugs)} strategies)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
