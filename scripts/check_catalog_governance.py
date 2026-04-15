from __future__ import annotations

import sys
import tomllib
from pathlib import Path

REQUIRED_PROMOTED_ARTIFACTS = (
    "research-spec.yaml",
    "robustness.yaml",
    "walk-forward.yaml",
    "paper-trading.yaml",
)


def load_catalog(path: Path) -> dict[str, list[str]]:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    sets = raw.get("sets", {})
    if not isinstance(sets, dict):
        raise ValueError(f"Invalid catalog structure in {path}")
    return {
        str(name): [str(item) for item in values]
        for name, values in sets.items()
        if isinstance(values, list)
    }


def validate_promoted_slug(slug: str, base_dir: Path) -> list[str]:
    slug_dir = base_dir / slug
    errors: list[str] = []

    if not slug_dir.exists():
        return [f"{slug}: missing strategy directory {slug_dir}"]

    for artifact in REQUIRED_PROMOTED_ARTIFACTS:
        artifact_path = slug_dir / artifact
        if not artifact_path.exists():
            errors.append(f"{slug}: missing required promoted artifact {artifact}")

    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    catalog_path = repo_root / "config" / "strategies" / "catalog.toml"
    strategies_dir = repo_root / "data" / "strategies"

    catalog = load_catalog(catalog_path)
    promoted_default = catalog.get("promoted_default", [])
    promoted_crypto = catalog.get("promoted_crypto", [])

    errors: list[str] = []

    for slug in promoted_default:
        errors.extend(validate_promoted_slug(slug, strategies_dir))

    for slug in promoted_crypto:
        errors.extend(validate_promoted_slug(slug, strategies_dir))

    if errors:
        print("Catalog governance check failed.")
        print(
            "Promoted sets may only contain lifecycle-complete strategies with the "
            "required on-disk promoted artifacts."
        )
        for error in errors:
            print(f"- {error}")
        return 1

    print("Catalog governance check passed.")
    print(
        f"Validated promoted_default={len(promoted_default)} and "
        f"promoted_crypto={len(promoted_crypto)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
