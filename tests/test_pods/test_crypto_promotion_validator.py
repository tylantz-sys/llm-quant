import importlib.util
import sys
from pathlib import Path


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def _seed_ready_artifacts(base_data: Path, slug: str, include_paper: bool) -> None:
    strat_dir = base_data / "strategies" / slug
    _write_text(
        strat_dir / "research-spec.yaml",
        """
strategy_slug: "test-slug"
frozen: true
""",
    )
    _write_text(
        strat_dir / "experiment-registry.jsonl",
        """
{"experiment_id":"exp1","sharpe_ratio":0.8,"max_drawdown":0.2,"dsr":0.96}
""",
    )
    _write_text(
        strat_dir / "walk-forward.yaml",
        """
passed: true
""",
    )
    _write_text(
        strat_dir / "robustness.yaml",
        """
overall_passed: true
""",
    )
    if include_paper:
        _write_text(
            strat_dir / "paper-trading.yaml",
            """
status: pass
""",
        )


def _load_validator_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "validate_crypto_promotion.py"
    spec = importlib.util.spec_from_file_location(
        "validate_crypto_promotion",
        script_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_validator(module, argv: list[str], monkeypatch) -> int:
    monkeypatch.setattr(sys, "argv", ["validate_crypto_promotion.py", *argv])
    return int(module.main())


def test_validator_candidate_vs_promoted_requirements(tmp_path, monkeypatch):
    validator = _load_validator_module()
    cfg_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    slug = "eth-btc-ratio-mean-reversion-v2"

    _write_text(
        cfg_dir / "strategies" / "catalog.toml",
        f"""
[sets]
candidate_crypto = ["{slug}"]
promoted_crypto = ["{slug}"]
""",
    )
    _seed_ready_artifacts(data_dir, slug, include_paper=False)

    candidate_rc = _run_validator(
        validator,
        [
            "--set",
            "candidate_crypto",
            "--strict",
            "--config-dir",
            str(cfg_dir),
            "--data-dir",
            str(data_dir),
        ],
        monkeypatch,
    )
    assert candidate_rc == 0

    promoted_rc = _run_validator(
        validator,
        [
            "--set",
            "promoted_crypto",
            "--strict",
            "--config-dir",
            str(cfg_dir),
            "--data-dir",
            str(data_dir),
        ],
        monkeypatch,
    )
    assert promoted_rc != 0

    _seed_ready_artifacts(data_dir, slug, include_paper=True)
    promoted_after_paper_rc = _run_validator(
        validator,
        [
            "--set",
            "promoted_crypto",
            "--strict",
            "--config-dir",
            str(cfg_dir),
            "--data-dir",
            str(data_dir),
        ],
        monkeypatch,
    )
    assert promoted_after_paper_rc == 0
