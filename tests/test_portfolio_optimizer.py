from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts import portfolio_optimizer


def _write_artifact(
    data_dir: Path,
    slug: str,
    exp_id: str,
    *,
    daily_returns: list[float] | None = None,
    sharpe: float = 1.0,
) -> None:
    artifact_path = data_dir / "strategies" / slug / "experiments" / f"{exp_id}.yaml"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "daily_returns": daily_returns if daily_returns is not None else [0.01, -0.005, 0.002],
        "metrics_1x": {
            "sharpe_ratio": sharpe,
            "sortino_ratio": sharpe,
            "max_drawdown": 0.1,
            "total_return": 0.05,
            "dsr": 0.95,
        },
        "start_date": "2020-01-01",
        "end_date": "2020-12-31",
    }
    artifact_path.write_text(yaml.safe_dump(artifact), encoding="utf-8")


def test_load_daily_returns_only_loads_artifact_backed_strategies(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    registry = portfolio_optimizer.STRATEGY_EXPERIMENTS

    _write_artifact(
        data_dir,
        "soxx-qqq-lead-lag",
        registry["soxx-qqq-lead-lag"],
        sharpe=1.5,
    )
    _write_artifact(
        data_dir,
        "lqd-spy-credit-lead",
        registry["lqd-spy-credit-lead"],
        sharpe=0.9,
    )

    strategies = portfolio_optimizer.load_daily_returns(data_dir)

    assert set(strategies) == {"soxx-qqq-lead-lag", "lqd-spy-credit-lead"}
    assert strategies["soxx-qqq-lead-lag"]["sharpe"] == 1.5
    assert strategies["lqd-spy-credit-lead"]["family"] == "F1: Credit Lead-Lag"


def test_load_daily_returns_skips_artifact_without_daily_returns(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    registry = portfolio_optimizer.STRATEGY_EXPERIMENTS

    _write_artifact(
        data_dir,
        "soxx-qqq-lead-lag",
        registry["soxx-qqq-lead-lag"],
        daily_returns=[],
    )

    strategies = portfolio_optimizer.load_daily_returns(data_dir)

    assert strategies == {}


def test_main_exits_when_loaded_strategies_below_minimum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    registry = portfolio_optimizer.STRATEGY_EXPERIMENTS

    _write_artifact(
        data_dir,
        "soxx-qqq-lead-lag",
        registry["soxx-qqq-lead-lag"],
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "portfolio_optimizer.py",
            "--data-dir",
            str(data_dir),
            "--min-strategies",
            "2",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        portfolio_optimizer.main()

    assert excinfo.value.code == 1


def test_main_can_bypass_minimum_with_ignore_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = tmp_path / "data"
    registry = portfolio_optimizer.STRATEGY_EXPERIMENTS

    _write_artifact(
        data_dir,
        "soxx-qqq-lead-lag",
        registry["soxx-qqq-lead-lag"],
    )
    _write_artifact(
        data_dir,
        "lqd-spy-credit-lead",
        registry["lqd-spy-credit-lead"],
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "portfolio_optimizer.py",
            "--data-dir",
            str(data_dir),
            "--min-strategies",
            "5",
            "--ignore-missing",
        ],
    )

    portfolio_optimizer.main()
    output = capsys.readouterr().out

    assert "PORTFOLIO OPTIMIZER REPORT" in output
    assert "soxx-qqq-lead-lag" in output
    assert "lqd-spy-credit-lead" in output
