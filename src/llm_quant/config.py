"""Configuration loading and validation via Pydantic."""

import logging
import os
import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

POD_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}[a-z0-9]$")


def _find_config_dir() -> Path:
    """Find the config directory relative to the project root."""
    # Check env var first
    env_path = os.environ.get("LLM_QUANT_CONFIG_DIR")
    if env_path:
        return Path(env_path)
    # Walk up from this file to find config/
    current = Path(__file__).resolve().parent
    for _ in range(5):
        candidate = current / "config"
        if candidate.is_dir():
            return candidate
        current = current.parent
    # Fallback to CWD
    return Path.cwd() / "config"


CONFIG_DIR = _find_config_dir()


class LLMConfig(BaseModel):
    model: str = "claude-sonnet-4-20250514"
    temperature: float = 0.0
    max_tokens: int = 4096
    max_trades_per_session: int = 5


class GeneralConfig(BaseModel):
    db_path: str = "data/llm_quant.duckdb"
    initial_capital: float = 100_000.0
    base_currency: str = "USD"


class DataConfig(BaseModel):
    lookback_days: int = 252
    fetch_timeout: int = 30
    db_lock_timeout_seconds: float = 30.0
    db_lock_retry_seconds: float = 0.5
    db_upsert_timeout_seconds: float = 30.0
    db_upsert_max_retries: int = 2
    db_upsert_retry_seconds: float = 1.0


class ExecutionConfig(BaseModel):
    """Execution and runtime behavior flags (intraday + profit-taking)."""

    signal_source: str = "auto"  # auto | llm | strategy_overlay
    strategy_set: str = "promoted_default"
    overlay_auth_required: bool = False
    overlay_governor_strict: bool = True
    overlay_max_upscale: float = 1.25
    overlay_max_downscale: float = 0.0
    intraday_enabled: bool = False
    intraday_timeframe_minutes: int = 5
    intraday_lookback_days: int = 10
    intraday_rth_guard: bool = True
    asset_class_filter: list[str] = Field(default_factory=list)
    intraday_use_oco: bool = True
    skip_daily_fetch_when_intraday: bool = False
    initial_capital_source: str = "config"
    claude_overlay_only: bool = True
    log_decisions_when_rth_closed: bool = True
    profit_take_partial_pct: float = 0.02
    profit_take_partial_size: float = 0.50
    profit_take_remainder_tp_mult: float = 2.0
    trailing_stop_pct: float = 0.015
    scale_in_tranches: int = 3
    reentry_cooldown_bars: int = 1
    expectancy_gate_enabled: bool = True
    expectancy_lookback_closed_trades: int = 20
    expectancy_negative_scale: float = 0.50
    crypto_order_sizing: str = "qty"
    crypto_time_in_force: str = "gtc"
    crypto_symbol_map: dict[str, str] = Field(default_factory=dict)
    # Symbols explicitly excluded from trade execution (e.g. to avoid cross-pod conflicts)
    symbol_exclude: list[str] = Field(default_factory=list)


class StrategyRotationConfig(BaseModel):
    enabled: bool = True
    window_days: int = 60
    top_n: int = 5
    min_trades: int = 10
    cooldown_days: int = 5


class StrategyAllocationConfig(BaseModel):
    strategy_group_caps: dict[str, float] = Field(default_factory=dict)
    regime_weight_mult: dict[str, dict[str, float]] = Field(default_factory=dict)


class RiskLimits(BaseModel):
    max_position_weight: float = 0.10
    short_max_position_weight: float = 0.10
    max_positions: int = 8
    max_trade_size: float = 0.02
    max_gross_exposure: float = 2.0
    max_net_exposure: float = 1.0
    max_short_exposure: float = 0.20
    max_sector_concentration: float = 0.30
    max_trades_per_session: int = 5
    min_cash_reserve: float = 0.05
    short_margin_rate: float = 0.50
    require_locate: bool = False
    require_stop_loss: bool = True
    default_stop_loss_pct: float = 0.05
    max_drawdown_pct: float = 0.15  # Portfolio drawdown circuit breaker
    # ATR-calibrated stop-loss multipliers
    atr_stop_multiplier: float = 2.0          # 2x ATR for equities (Turtle Traders)
    atr_stop_multiplier_crypto: float = 2.5   # wider for crypto overnight gaps
    atr_stop_multiplier_commodity: float = 2.5  # wider for volatile commodities
    # ATR-based position sizing
    target_risk_pct: float = 0.01    # fraction of NAV to risk per trade
    deviation_buffer: float = 0.20   # buffer before triggering rebalance alert
    atr_period: int = 14             # ATR lookback for equities / fixed income
    atr_period_crypto: int = 7       # shorter ATR lookback for crypto
    # Take-profit defaults (configurable, overrides LLM when mode = pct)
    take_profit_mode: str = "pct"    # pct | rr
    take_profit_pct: float = 0.03    # fixed take-profit percent (3%)
    take_profit_rr: float = 2.0      # risk-reward multiple (if mode = rr)
    partial_take_profit_enabled: bool = True
    partial_take_profit_pct: float = 0.02
    partial_take_profit_size: float = 0.50
    remainder_take_profit_mult: float = 2.0
    trailing_stop_enabled: bool = True
    trailing_stop_pct: float = 0.015
    fail_on_unprotected_exits: bool = True
    # End-of-day flatten control
    eod_flatten_enabled: bool = True
    eod_flatten_time: str = "15:55"  # US/Eastern
    # Per-asset-class overrides (crypto is more volatile, forex less so)
    crypto_max_position_weight: float = 0.05
    crypto_default_stop_loss_pct: float = 0.15
    forex_max_position_weight: float = 0.08
    forex_default_stop_loss_pct: float = 0.03
    # Crypto basket equal-weight sizing: when enabled, all BUY crypto signals are
    # clamped to crypto_basket_target_weight before execution, enforcing a flat
    # allocation across basket constituents regardless of LLM conviction.
    crypto_basket_equal_weight: bool = True
    crypto_basket_target_weight: float = 0.03  # 3% per position (1-2 crypto slots)


class TrackBLimits(BaseModel):
    """Risk limits for Track B — Aggressive Alpha strategies."""

    max_position_weight: float = 0.15
    short_max_position_weight: float = 0.15
    max_positions: int = 8
    max_trade_size: float = 0.03
    max_gross_exposure: float = 2.0
    max_net_exposure: float = 1.0
    max_short_exposure: float = 0.30
    max_sector_concentration: float = 0.30
    max_trades_per_session: int = 5
    min_cash_reserve: float = 0.03
    short_margin_rate: float = 0.50
    require_locate: bool = False
    require_stop_loss: bool = True
    default_stop_loss_pct: float = 0.08
    atr_stop_multiplier: float = 2.0
    atr_stop_multiplier_crypto: float = 3.0
    atr_stop_multiplier_commodity: float = 2.5
    target_risk_pct: float = 0.015
    deviation_buffer: float = 0.25
    max_drawdown_pct: float = 0.30
    crypto_max_position_weight: float = 0.08
    crypto_default_stop_loss_pct: float = 0.20
    leveraged_etf_max_position_weight: float = 0.10
    # Crypto basket equal-weight sizing (Track B allows slightly larger basket slots)
    crypto_basket_equal_weight: bool = True
    crypto_basket_target_weight: float = 0.04  # 4% per position (fits ~2 crypto slots)
    # Take-profit defaults (configurable, overrides LLM when mode = pct)
    take_profit_mode: str = "pct"
    take_profit_pct: float = 0.03
    take_profit_rr: float = 2.0
    partial_take_profit_enabled: bool = True
    partial_take_profit_pct: float = 0.02
    partial_take_profit_size: float = 0.50
    remainder_take_profit_mult: float = 2.0
    trailing_stop_enabled: bool = True
    trailing_stop_pct: float = 0.015
    fail_on_unprotected_exits: bool = True
    # End-of-day flatten control
    eod_flatten_enabled: bool = True
    eod_flatten_time: str = "15:55"


class TrackCLimits(BaseModel):
    """Risk limits for Track C — Structural Arb / Event-Driven strategies.

    Near-zero-beta arb strategies sit between Track A (defensive) and Track B
    (aggressive).  They allow wider single-leg sizing because legs partially
    offset each other, but enforce a tight *net* exposure cap to preserve
    market-neutrality.  A higher cash reserve supports event-driven staging.
    """

    max_position_weight: float = 0.20           # 20% per strategy
    max_positions: int = 8
    max_trade_size: float = 0.05                # 5% per trade
    max_gross_exposure: float = 2.0             # 200% (both legs summed)
    max_net_exposure: float = 0.30              # 30% — enforces near-zero beta
    max_sector_concentration: float = 0.30
    max_exchange_concentration: float = 0.25    # 25% on any single exchange
    max_trades_per_session: int = 5
    min_cash_reserve: float = 0.10              # 10% — hold dry powder for events
    require_stop_loss: bool = True
    default_stop_loss_pct: float = 0.05
    atr_stop_multiplier: float = 2.0
    atr_stop_multiplier_crypto: float = 2.5
    atr_stop_multiplier_commodity: float = 2.5
    target_risk_pct: float = 0.01
    deviation_buffer: float = 0.20
    max_drawdown_pct: float = 0.10              # 10% — tighter; drawdown signals leg break

    # Kill-switch thresholds (Track C-specific)
    max_beta_to_spy: float = 0.25               # rolling-30d SPY beta limit
    min_spread_bps: float = 5.0                 # spread collapse threshold (bps)
    max_funding_rate_pct: float = 0.50          # funding reversal threshold (bps/day)


class RegimeDriftConfig(BaseModel):
    rolling_window_days: int = 21
    sharpe_decay_warn: float = 0.30
    sharpe_decay_halt: float = 0.50
    win_rate_decay_warn: float = 0.15
    win_rate_decay_halt: float = 0.25
    vol_spike_warn: float = 1.5
    vol_spike_halt: float = 2.0


class AlphaDecayConfig(BaseModel):
    rolling_window_days: int = 63
    decay_warn: float = 0.40
    decay_halt: float = 0.60


class RiskDriftConfig(BaseModel):
    exposure_warn_buffer: float = 0.10
    concentration_warn_buffer: float = 0.10


class DataQualityConfig(BaseModel):
    max_stale_days: int = 3
    gap_threshold_pct: float = 0.20
    plausibility_min_price: float = 0.01


class ProcessDriftConfig(BaseModel):
    tracked_files: list[str] = Field(
        default_factory=lambda: [
            "config/default.toml",
            "config/risk.toml",
            "config/universe.toml",
            "config/governance.toml",
            "config/prompts/trader_decision.md",
        ]
    )


class OperationalHealthConfig(BaseModel):
    max_snapshot_gap_days: int = 3
    max_price_staleness_hours: int = 48
    hash_chain_required: bool = True


class KillSwitchConfig(BaseModel):
    max_drawdown_pct: float = 0.15
    max_daily_loss_pct: float = 0.05
    max_consecutive_losses: int = 5
    max_correlation_breach: float = 0.85
    data_blackout_hours: int = 72
    risk_check_failure_streak: int = 3


class PromotionConfig(BaseModel):
    min_deflated_sharpe: float = 0.95
    max_pbo: float = 0.10
    max_spa_p_value: float = 0.05
    min_trl: int = 1
    scorecard_pass_threshold: int = 85
    min_paper_trades: int = 50
    min_paper_days: int = 30
    min_paper_sharpe: float = 0.60
    canary_allocation_pct: float = 0.10
    canary_min_days: int = 14
    canary_max_drawdown_pct: float = 0.10


class ProfitTakingScoreConfig(BaseModel):
    capture_ratio_weight: float = 0.35
    giveback_penalty_weight: float = 0.25
    tp1_hit_rate_weight: float = 0.15
    trailing_preservation_weight: float = 0.15
    runner_retention_weight: float = 0.10


class ProfitTakingMandateConfig(BaseModel):
    enabled: bool = True
    mandate_type: str = "balanced_harvest"
    harvest_priority: int = 50
    tp1_target_pct: float = 0.02
    tp1_size: float = 0.50
    runner_tp_mult: float = 2.0
    trailing_stop_pct: float = 0.015
    max_giveback_pct: float = 0.35
    min_harvest_ratio: float = 0.45
    stale_winner_days: int = 5
    allow_reentry_after_partial: bool = False
    eod_flatten: bool = False


class ProfitTakingMandatesConfig(BaseModel):
    default: ProfitTakingMandateConfig = Field(
        default_factory=ProfitTakingMandateConfig
    )
    crypto: ProfitTakingMandateConfig = Field(
        default_factory=lambda: ProfitTakingMandateConfig(
            mandate_type="crypto_synthetic_harvest",
            harvest_priority=80,
            tp1_target_pct=0.015,
            tp1_size=0.50,
            runner_tp_mult=2.0,
            trailing_stop_pct=0.0125,
            max_giveback_pct=0.30,
            min_harvest_ratio=0.50,
            stale_winner_days=2,
            allow_reentry_after_partial=False,
            eod_flatten=False,
        )
    )

    def get_by_name(self, mandate_name: str) -> ProfitTakingMandateConfig:
        """Return a named mandate, falling back to attribute lookup semantics."""
        mandate = getattr(self, mandate_name, None)
        if not isinstance(mandate, ProfitTakingMandateConfig):
            raise KeyError(f"Unknown profit-taking mandate '{mandate_name}'")
        return mandate


class ProfitTakingPromotionConfig(BaseModel):
    min_harvest_ratio: float = 0.45
    max_open_gain_giveback_pct: float = 0.35
    min_tp1_hit_rate: float = 0.40
    min_trailing_preservation_rate: float = 0.40
    min_realized_to_unrealized_ratio: float = 0.55
    min_paper_trades_for_harvest_eval: int = 30
    promotion_block_on_poor_monetization: bool = True


class ProfitTakingRotationConfig(BaseModel):
    enabled: bool = True
    weight: float = 0.25
    prefer_harvest_over_new_entries: bool = True
    stale_winner_trim_required: bool = True
    max_days_since_last_harvest: int = 10


class ProfitTakingSelectionConfig(BaseModel):
    enabled: bool = True
    reserve_cash_for_rotation: float = 0.10
    require_trim_before_new_entry: bool = False
    block_readd_after_partial: bool = True
    min_realized_to_unrealized_ratio: float = 0.55


class ProfitTakingOverlayEvaluationConfig(BaseModel):
    enabled: bool = True
    require_mandate_alignment: bool = True
    realized_edge_weight: float = 0.50
    harvest_rate_edge_weight: float = 0.30
    giveback_control_weight: float = 0.20


class ProfitTakingGovernanceActionsConfig(BaseModel):
    allocation_shrink_scale: float = 0.50
    apply_conservative_mandate: bool = True
    conservative_mandate_name: str = "default"
    temporary_eod_flatten: bool = True
    demote_on_halt: bool = True
    paper_revalidate_on_halt: bool = True


class ProfitTakingGovernanceConfig(BaseModel):
    enabled: bool = True
    lookback_days: int = 30
    min_profit_take_events: int = 5
    min_capture_ratio: float = 0.45
    max_giveback_ratio: float = 0.55
    min_trailing_salvage_rate: float = 0.40
    min_realized_retention: float = 0.45
    min_tp1_effectiveness: float = 0.40
    actions: ProfitTakingGovernanceActionsConfig = Field(
        default_factory=ProfitTakingGovernanceActionsConfig
    )


class ProfitTakingConfig(BaseModel):
    enabled: bool = True
    score: ProfitTakingScoreConfig = Field(default_factory=ProfitTakingScoreConfig)
    mandates: ProfitTakingMandatesConfig = Field(
        default_factory=ProfitTakingMandatesConfig
    )
    promotion: ProfitTakingPromotionConfig = Field(
        default_factory=ProfitTakingPromotionConfig
    )
    rotation: ProfitTakingRotationConfig = Field(
        default_factory=ProfitTakingRotationConfig
    )
    selection: ProfitTakingSelectionConfig = Field(
        default_factory=ProfitTakingSelectionConfig
    )
    overlay_evaluation: ProfitTakingOverlayEvaluationConfig = Field(
        default_factory=ProfitTakingOverlayEvaluationConfig
    )
    governance: ProfitTakingGovernanceConfig = Field(
        default_factory=ProfitTakingGovernanceConfig
    )


class GovernanceConfig(BaseModel):
    regime_drift: RegimeDriftConfig = Field(default_factory=RegimeDriftConfig)
    alpha_decay: AlphaDecayConfig = Field(default_factory=AlphaDecayConfig)
    risk_drift: RiskDriftConfig = Field(default_factory=RiskDriftConfig)
    data_quality: DataQualityConfig = Field(default_factory=DataQualityConfig)
    process_drift: ProcessDriftConfig = Field(default_factory=ProcessDriftConfig)
    operational_health: OperationalHealthConfig = Field(
        default_factory=OperationalHealthConfig
    )
    kill_switches: KillSwitchConfig = Field(default_factory=KillSwitchConfig)
    promotion: PromotionConfig = Field(default_factory=PromotionConfig)
    profit_taking: ProfitTakingConfig = Field(default_factory=ProfitTakingConfig)


class StrategyMetadataConfig(BaseModel):
    sleeve: str = "default"
    profit_taking_mandate: str | None = None

    @property
    def active_profit_taking_mandate_name(self) -> str:
        """Return the explicitly selected mandate or the sleeve-derived default."""
        if self.profit_taking_mandate:
            return self.profit_taking_mandate
        if self.sleeve == "crypto":
            return "crypto"
        return "default"


class AssetEntry(BaseModel):
    symbol: str
    name: str
    category: str
    sector: str
    asset_class: str = "equity"  # equity, crypto, forex
    tradeable: bool = True
    cftc_code: str | None = None  # 6-digit CFTC code for COT overlay (GLD, SLV, USO)


# Backward-compatible alias
ETFEntry = AssetEntry


class UniverseConfig(BaseModel):
    name: str = "Multi-Asset Universe"
    description: str = ""
    assets: list[AssetEntry] = Field(default_factory=list)

    @property
    def etfs(self) -> list[AssetEntry]:
        """Backward-compatible alias for assets."""
        return self.assets


class AppConfig(BaseModel):
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    strategy: StrategyMetadataConfig = Field(default_factory=StrategyMetadataConfig)
    strategy_rotation: StrategyRotationConfig = Field(
        default_factory=StrategyRotationConfig
    )
    allocation: StrategyAllocationConfig = Field(
        default_factory=StrategyAllocationConfig
    )
    risk: RiskLimits = Field(default_factory=RiskLimits)
    track_b: TrackBLimits = Field(default_factory=TrackBLimits)
    track_c: TrackCLimits = Field(default_factory=TrackCLimits)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)

    @property
    def active_profit_taking_mandate_name(self) -> str:
        """Return the mandate name selected for the active sleeve/pod."""
        return self.strategy.active_profit_taking_mandate_name

    @property
    def active_profit_taking_mandate(self) -> ProfitTakingMandateConfig:
        """Return the active profit-taking mandate config for the current pod."""
        return self.governance.profit_taking.mandates.get_by_name(
            self.active_profit_taking_mandate_name
        )


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file and return as dict."""
    with path.open("rb") as f:
        return tomllib.load(f)


def load_config(config_dir: Path | None = None) -> AppConfig:
    """Load all TOML configs and merge into AppConfig."""
    config_dir = config_dir or CONFIG_DIR

    general_data: dict[str, Any] = {}
    llm_data: dict[str, Any] = {}
    data_data: dict[str, Any] = {}
    execution_data: dict[str, Any] = {}
    strategy_data: dict[str, Any] = {}
    rotation_data: dict[str, Any] = {}
    allocation_data: dict[str, Any] = {}
    risk_data: dict[str, Any] = {}
    track_b_data: dict[str, Any] = {}
    track_c_data: dict[str, Any] = {}
    universe_data: dict[str, Any] = {}

    # Load default.toml
    default_path = config_dir / "default.toml"
    if default_path.exists():
        raw = _load_toml(default_path)
        general_data = raw.get("general", {})
        llm_data = raw.get("llm", {})
        data_data = raw.get("data", {})
        execution_data = raw.get("execution", {})
        strategy_data = raw.get("strategy", {})
        rotation_data = raw.get("strategy_rotation", {})
        allocation_data = raw.get("allocation", {})

    # Load risk.toml
    risk_path = config_dir / "risk.toml"
    if risk_path.exists():
        raw = _load_toml(risk_path)
        risk_data = raw.get("limits", {})
        track_b_data = raw.get("track_b", {})
        track_c_data = raw.get("track_c", {})

    # Load universe.toml
    universe_path = config_dir / "universe.toml"
    if universe_path.exists():
        raw = _load_toml(universe_path)
        universe_data = raw.get("universe", {})
        # Accept both "assets" (new) and "etfs" (legacy) keys
        universe_data["assets"] = raw.get("assets", []) or raw.get("etfs", [])

    # Override db_path from env
    env_db = os.environ.get("LLM_QUANT_DB_PATH")
    if env_db:
        general_data["db_path"] = env_db

    # Override model from env
    env_model = os.environ.get("LLM_QUANT_MODEL")
    if env_model:
        llm_data["model"] = env_model

    # Load governance.toml
    governance_data: dict[str, Any] = {}
    governance_path = config_dir / "governance.toml"
    if governance_path.exists():
        governance_data = _load_toml(governance_path)

    return AppConfig(
        general=GeneralConfig(**general_data),
        llm=LLMConfig(**llm_data),
        data=DataConfig(**data_data),
        execution=ExecutionConfig(**execution_data),
        strategy=StrategyMetadataConfig(**strategy_data),
        strategy_rotation=StrategyRotationConfig(**rotation_data),
        allocation=StrategyAllocationConfig(**allocation_data),
        risk=RiskLimits(**risk_data),
        track_b=TrackBLimits(**track_b_data),
        track_c=TrackCLimits(**track_c_data),
        universe=UniverseConfig(**universe_data),
        governance=GovernanceConfig(**governance_data),
    )


def validate_pod_id(pod_id: str) -> bool:
    """Check whether *pod_id* is syntactically valid."""
    return pod_id == "default" or bool(POD_ID_RE.match(pod_id))


def load_config_for_pod(
    pod_id: str = "default",
    config_dir: Path | None = None,
) -> AppConfig:
    """Load base config, then overlay strategy-specific TOML if it exists."""
    base = load_config(config_dir)
    if pod_id == "default":
        return base

    cfg_dir = config_dir or _find_config_dir()
    strategy_path = cfg_dir / "strategies" / f"{pod_id}.toml"
    if not strategy_path.exists():
        logger.warning("No strategy overlay for pod '%s' at %s", pod_id, strategy_path)
        return base

    with strategy_path.open("rb") as f:
        overlay = tomllib.load(f)

    # Merge overlay into base config dict
    base_dict = base.model_dump()
    for section, values in overlay.items():
        if (
            section in base_dict
            and isinstance(values, dict)
            and isinstance(base_dict[section], dict)
        ):
            base_dict[section].update(values)

    return AppConfig(**base_dict)
