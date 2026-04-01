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


def test_profit_taking_governance_defaults_loaded():
    """Base config exposes profit-taking scorecard and governance defaults."""
    cfg = load_config()

    assert cfg.governance.profit_taking.enabled is True
    assert cfg.governance.profit_taking.score.capture_ratio_weight == 0.35
    assert cfg.governance.profit_taking.score.giveback_penalty_weight == 0.25
    assert cfg.governance.profit_taking.promotion.min_harvest_ratio == 0.45
    assert cfg.governance.profit_taking.rotation.prefer_harvest_over_new_entries is True
    assert cfg.governance.profit_taking.selection.block_readd_after_partial is True
    assert cfg.governance.profit_taking.overlay_evaluation.require_mandate_alignment is True


def test_profit_taking_mandates_loaded():
    """Base config exposes default and crypto harvest mandates."""
    cfg = load_config()

    default_mandate = cfg.governance.profit_taking.mandates.default
    crypto_mandate = cfg.governance.profit_taking.mandates.crypto

    assert default_mandate.mandate_type == "balanced_harvest"
    assert default_mandate.tp1_target_pct == 0.02
    assert default_mandate.allow_reentry_after_partial is False

    assert crypto_mandate.mandate_type == "crypto_synthetic_harvest"
    assert crypto_mandate.harvest_priority == 80
    assert crypto_mandate.tp1_target_pct == 0.015
    assert crypto_mandate.trailing_stop_pct == 0.0125
    assert crypto_mandate.stale_winner_days == 2


def test_pod_specific_profit_taking_mandate_resolution():
    """Sleeves resolve to the correct active mandate contract by pod."""
    default_cfg = load_config_for_pod("default")
    crypto_cfg = load_config_for_pod("crypto")

    default_name = default_cfg.active_profit_taking_mandate_name
    crypto_name = crypto_cfg.active_profit_taking_mandate_name

    assert default_name == "default"
    assert crypto_name == "crypto"

    assert (
        default_cfg.active_profit_taking_mandate.mandate_type == "balanced_harvest"
    )
    assert (
        crypto_cfg.active_profit_taking_mandate.mandate_type
        == "crypto_synthetic_harvest"
    )


def test_profit_taking_config_backwards_compatible_when_missing_governance_file(tmp_path):
    """Missing governance.toml still yields usable profit-taking defaults."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    cfg = load_config(config_dir=config_dir)

    assert cfg.governance.profit_taking.enabled is True
    assert cfg.governance.profit_taking.promotion.min_realized_to_unrealized_ratio == 0.55
    assert cfg.governance.profit_taking.mandates.default.mandate_type == "balanced_harvest"


def test_profit_taking_score_weights_sum_to_one():
    """Configured Phase 4 score weights remain normalized for governance math."""
    cfg = load_config()

    score = cfg.governance.profit_taking.score
    total_weight = (
        score.capture_ratio_weight
        + score.giveback_penalty_weight
        + score.tp1_hit_rate_weight
        + score.trailing_preservation_weight
        + score.runner_retention_weight
    )

    assert total_weight == 1.0


def test_profit_taking_overlay_evaluation_weights_sum_to_one():
    """Overlay evaluation weights remain normalized for realized-edge scoring."""
    cfg = load_config()

    overlay = cfg.governance.profit_taking.overlay_evaluation
    total_weight = (
        overlay.realized_edge_weight
        + overlay.harvest_rate_edge_weight
        + overlay.giveback_control_weight
    )

    assert total_weight == 1.0


def test_profit_taking_mandate_defaults_are_asset_specific():
    """Phase 4 mandates preserve distinct default vs crypto harvest behavior."""
    cfg = load_config()

    default_mandate = cfg.governance.profit_taking.mandates.default
    crypto_mandate = cfg.governance.profit_taking.mandates.crypto

    assert default_mandate.harvest_priority < crypto_mandate.harvest_priority
    assert default_mandate.tp1_target_pct > crypto_mandate.tp1_target_pct
    assert default_mandate.trailing_stop_pct > crypto_mandate.trailing_stop_pct
    assert default_mandate.min_harvest_ratio < crypto_mandate.min_harvest_ratio
    assert default_mandate.stale_winner_days > crypto_mandate.stale_winner_days


def test_profit_taking_defaults_exist_without_governance_file():
    """Model defaults include Phase 4 governance structures even without TOML."""
    cfg = load_config(config_dir=None)
    defaults = cfg.governance.model_construct()

    profit_taking = defaults.profit_taking
    assert profit_taking.enabled is True
    assert profit_taking.rotation.weight == 0.25
    assert profit_taking.selection.reserve_cash_for_rotation == 0.10
    assert profit_taking.overlay_evaluation.realized_edge_weight == 0.50
    assert profit_taking.mandates.crypto.mandate_type == "crypto_synthetic_harvest"
