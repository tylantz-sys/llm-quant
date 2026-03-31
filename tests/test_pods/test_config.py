"""Tests for pod config loading and validation."""

from llm_quant.config import load_config, load_config_for_pod, validate_pod_id


def test_validate_pod_id_valid():
    """Valid pod IDs: lowercase alphanumeric with hyphens, 2+ chars."""
    assert validate_pod_id("default") is True
    assert validate_pod_id("momentum-01") is True
    assert validate_pod_id("ab") is True


def test_validate_pod_id_invalid():
    """Invalid pod IDs: uppercase, spaces, leading/trailing hyphens, empty."""
    assert validate_pod_id("A") is False
    assert validate_pod_id("with spaces") is False
    assert validate_pod_id("-leading") is False
    assert validate_pod_id("trailing-") is False
    assert validate_pod_id("") is False


def test_load_config_for_pod_default():
    """load_config_for_pod('default') returns same config as load_config()."""
    base = load_config()
    pod_cfg = load_config_for_pod("default")
    assert base.model_dump() == pod_cfg.model_dump()


def test_load_config_for_pod_benchmark():
    """load_config_for_pod('benchmark') overlays benchmark risk limits."""
    cfg = load_config_for_pod("benchmark")
    assert cfg.risk.max_position_weight == 0.25
    assert cfg.risk.max_trade_size == 0.10
    assert cfg.risk.max_gross_exposure == 1.0
    assert cfg.risk.max_sector_concentration == 0.50
    assert cfg.risk.max_drawdown_pct == 0.30


def test_load_config_for_pod_missing(tmp_path):
    """Unknown pod returns base config (no overlay file)."""
    # Create a minimal config dir with no strategies sub-dir
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cfg = load_config_for_pod("nonexistent-pod", config_dir=config_dir)
    # Should fall back to defaults
    base = load_config(config_dir=config_dir)
    assert cfg.risk.max_position_weight == base.risk.max_position_weight


def test_default_pod_overlay_mandate():
    """Default pod remains promoted overlay-only for equity/fixed-income sleeves."""
    cfg = load_config_for_pod("default")
    assert cfg.execution.claude_overlay_only is True
    assert cfg.execution.signal_source == "strategy_overlay"
    assert cfg.execution.strategy_set == "promoted_default"
    assert cfg.execution.asset_class_filter == ["equity", "fixed_income"]
    assert cfg.execution.intraday_rth_guard is True


def test_commodities_pod_mandate():
    """Commodities pod has explicit commodity-only intraday settings."""
    cfg = load_config_for_pod("commodities")
    assert cfg.execution.intraday_enabled is True
    assert cfg.execution.signal_source == "llm"
    assert cfg.execution.asset_class_filter == ["commodity"]
    assert cfg.execution.claude_overlay_only is False
    assert cfg.execution.intraday_use_oco is True
    assert cfg.execution.profit_take_partial_pct == 0.015
    assert cfg.execution.trailing_stop_pct == 0.010
    assert cfg.execution.scale_in_tranches == 2


def test_crypto_pod_mandate():
    """Crypto pod remains 24/7 with synthetic exits and faster scale-in."""
    cfg = load_config_for_pod("crypto")
    assert cfg.execution.intraday_enabled is True
    assert cfg.execution.signal_source == "strategy_overlay"
    assert cfg.execution.strategy_set == "promoted_crypto"
    assert cfg.execution.asset_class_filter == ["crypto"]
    assert cfg.execution.claude_overlay_only is True
    assert cfg.execution.intraday_rth_guard is False
    assert cfg.execution.intraday_use_oco is False
    assert cfg.execution.scale_in_tranches == 2


def test_crypto_ethbtc_paper_pod_mandate():
    """Dedicated ETH/BTC paper pod uses candidate set with conservative risk."""
    cfg = load_config_for_pod("crypto-ethbtc-paper")
    assert cfg.execution.signal_source == "strategy_overlay"
    assert cfg.execution.strategy_set == "candidate_crypto"
    assert cfg.execution.claude_overlay_only is True
    assert cfg.execution.asset_class_filter == ["crypto"]
    assert cfg.execution.intraday_enabled is True
    assert cfg.execution.intraday_rth_guard is False
    assert cfg.execution.intraday_use_oco is False
    assert cfg.risk.max_trade_size == 0.02
    assert cfg.risk.crypto_max_position_weight == 0.025
