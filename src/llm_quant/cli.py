"""CLI entry point for llm-quant paper trading system."""

import dataclasses
import logging
import os
import re
import time
from datetime import UTC, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="pq",
    help="llm-quant: LLM-powered paper trading system",
    no_args_is_help=True,
)
console = Console()

logger = logging.getLogger("llm_quant")

_ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Pods sub-command group
# ---------------------------------------------------------------------------

pods_app = typer.Typer(name="pods", help="Manage trading pods")
app.add_typer(pods_app, name="pods")

crypto_app = typer.Typer(name="crypto", help="Crypto pod utilities")
app.add_typer(crypto_app, name="crypto")


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _get_config():
    from llm_quant.config import load_config

    return load_config()


def _get_config_for_pod(pod_id: str = "default"):
    from llm_quant.config import load_config_for_pod

    return load_config_for_pod(pod_id)


def _get_db_path(config=None):
    if config is None:
        config = _get_config()
    return Path(config.general.db_path)


def _parse_eod_time(value: str) -> dt_time:
    from llm_quant.trading.exits import parse_eod_time

    return parse_eod_time(value)


def _resolve_pod_capital(
    capital: float | None,
    capital_source: str,
    config,
) -> float:
    if capital is not None:
        return float(capital)

    source = (capital_source or "config").lower()
    if source.startswith("alpaca_"):
        return _get_alpaca_account_value(source)

    return float(config.general.initial_capital)


def _resolve_initial_capital(config, broker: str) -> float:
    source = getattr(config.execution, "initial_capital_source", "config") or "config"
    if broker.lower() != "alpaca" or source == "config":
        return float(config.general.initial_capital)

    return _get_alpaca_account_value(source)


def _get_alpaca_account_value(source: str) -> float:
    try:
        account = _get_alpaca_account()
        key_map = {
            "alpaca_equity": "equity",
            "alpaca_cash": "cash",
            "alpaca_buying_power": "buying_power",
        }
        key = key_map.get(source, "equity")
        value = float(account.get(key, 0.0))
        if value > 0:
            return value
        console.print(
            f"[red]FAIL[/red] Alpaca account returned {key}={value}. "
            "Cannot use Alpaca account value."
        )
        raise typer.Exit(1)
    except Exception as exc:  # noqa: BLE001
        console.print(
            "[red]FAIL[/red] Could not fetch Alpaca account. "
            "Ensure ALPACA_API_KEY / ALPACA_SECRET_KEY are set (or in .env)."
        )
        raise typer.Exit(1) from exc


def _get_alpaca_account() -> dict[str, float | str]:
    from llm_quant.broker.alpaca import AlpacaClient

    client = AlpacaClient.from_env()
    return client.get_account()


def _overlay_auth_present() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _resolve_signal_source(config: Any) -> tuple[bool, str]:
    signal_source = str(config.execution.signal_source or "auto").lower()
    if signal_source == "auto":
        use_strategy_overlay = bool(config.execution.claude_overlay_only)
    else:
        use_strategy_overlay = signal_source == "strategy_overlay"
    resolved_signal_source = "strategy_overlay" if use_strategy_overlay else "llm"
    return use_strategy_overlay, resolved_signal_source


def _resolve_overlay_auth_required(
    config: Any,
    *,
    use_strategy_overlay: bool,
    validation_mode: str,
) -> bool:
    return bool(config.execution.overlay_auth_required) or (
        use_strategy_overlay and validation_mode == "paper"
    )


def _sync_live_alpaca_positions(portfolio: Any) -> int:
    """Close positions in portfolio that Alpaca no longer holds (filled stop/TP between runs).

    Only CLOSES positions — never opens new ones based on broker state.
    Returns the number of positions closed.
    """
    from llm_quant.broker.alpaca import AlpacaClient

    try:
        client = AlpacaClient.from_env()
        broker_positions = client.list_positions()
    except Exception as exc:  # noqa: BLE001
        console.print(
            f"[yellow]WARN[/yellow] Could not fetch Alpaca positions for sync: {exc}"
        )
        return 0

    broker_symbols = {
        _normalize_symbol(str(p.get("symbol", "") if isinstance(p, dict) else getattr(p, "symbol", "")))
        for p in broker_positions
    }

    closed = 0
    for symbol in list(portfolio.positions.keys()):
        position = portfolio.positions.get(symbol)
        if position is None:
            continue
        shares = float(getattr(position, "shares", 0.0) or 0.0)
        if shares <= 0.0:
            continue
        if _normalize_symbol(str(symbol)) not in broker_symbols:
            # Position was closed at Alpaca (stop-loss / take-profit / manual) between runs.
            # Recover cash using last known price so NAV stays consistent.
            price = float(getattr(position, "current_price", None) or getattr(position, "avg_cost", 0.0))
            proceeds = shares * price
            portfolio.cash += proceeds
            del portfolio.positions[symbol]
            console.print(
                f"  [yellow]SYNC[/yellow] Position {symbol} closed at Alpaca between runs "
                f"(qty={shares:.4f} price={price:.4f}); recovered ${proceeds:,.2f} to cash"
            )
            closed += 1

    if closed:
        console.print(f"  [green]OK[/green] Synced {closed} position(s) closed at Alpaca between runs")
    return closed


def _sync_live_alpaca_cash(portfolio: Any) -> dict[str, float] | None:
    try:
        account = _get_alpaca_account()
    except Exception as exc:  # noqa: BLE001
        console.print(
            f"[yellow]WARN[/yellow] Could not sync live Alpaca account cash: {exc}"
        )
        return None

    live_cash = float(account.get("cash", portfolio.cash) or portfolio.cash)
    live_equity = float(account.get("equity", portfolio.nav) or portfolio.nav)
    portfolio.cash = live_cash
    console.print(
        "  [green]OK[/green] Synced live Alpaca account context: "
        f"cash=${live_cash:,.2f} equity=${live_equity:,.2f}"
    )
    return {"cash": live_cash, "equity": live_equity}


def _broker_side_for_signal(action: object) -> str:
    value = getattr(action, "value", str(action)).lower()
    return "buy" if value == "buy" else "sell"


def _release_runtime_locks(global_lock: Any, run_lock: Any) -> None:
    if global_lock:
        global_lock.release()
    if run_lock:
        run_lock.release()


def _normalize_symbol(sym: str) -> str:
    return sym.replace("/", "").replace("-", "").upper()


def _monitor_and_reconcile_broker_drift(
    *,
    conn: Any,
    client: Any,
    portfolio: Any,
    pod_id: str,
    tracked_symbols: list[str] | None = None,
    decision_id: int | None = None,
    resolved_signal_source: str | None = None,
    strategy_set: str | None = None,
    exit_policy_state: dict[str, object] | None = None,
) -> tuple[bool, Any | None]:
    from llm_quant.broker.monitor import monitor_open_positions
    from llm_quant.broker.reconciliation import reconcile_broker_orders
    from llm_quant.trading.ledger import log_broker_fills

    monitored = monitor_open_positions(client, tracked_symbols=tracked_symbols)
    monitored_normalized = {
        _normalize_symbol(e.symbol) for e in monitored if getattr(e, "symbol", "")
    }
    portfolio_normalized = {
        _normalize_symbol(str(symbol))
        for symbol, position in getattr(portfolio, "positions", {}).items()
        if float(getattr(position, "shares", 0.0) or 0.0) > 0.0
    }
    drift_detected = monitored_normalized != portfolio_normalized
    if not drift_detected:
        return False, None

    reconciliation = reconcile_broker_orders(
        conn,
        client,
        portfolio=portfolio,
        ledger_conn=conn,
        pod_id=pod_id,
        broker_positions=client.list_positions(),
        log_fills_fn=log_broker_fills,
        trade_date=datetime.now(tz=UTC).date(),
        log_kwargs={
            "decision_id": decision_id,
            "decision_source": resolved_signal_source,
            "sleeve": strategy_set,
            "source_decision_id": decision_id,
            "exit_policy_state": exit_policy_state or {},
        },
    )
    return True, reconciliation


# ---------------------------------------------------------------------------
# init / fetch (unchanged — not pod-scoped)
# ---------------------------------------------------------------------------


@app.command()
def init():
    """Create DuckDB schema and default configs."""
    _setup_logging()
    config = _get_config()
    db_path = _get_db_path(config)

    from llm_quant.data.universe import sync_universe_to_db
    from llm_quant.db.schema import init_schema

    conn = init_schema(db_path)
    count = sync_universe_to_db(conn, config)
    conn.close()

    console.print(f"[green]OK[/green] Database initialized at [bold]{db_path}[/bold]")
    console.print(f"[green]OK[/green] Universe synced: {count} symbols")


@app.command()
def fetch():
    """Fetch/update market data from Yahoo Finance."""
    _setup_logging()
    config = _get_config()
    db_path = _get_db_path(config)

    from llm_quant.data.fetcher import fetch_ohlcv
    from llm_quant.data.indicators import compute_indicators
    from llm_quant.data.store import (
        get_intraday_data,
        get_market_data,
        upsert_intraday_data,
        upsert_market_data,
    )
    from llm_quant.data.alpaca_intraday import fetch_intraday_ohlcv
    from llm_quant.data.universe import get_all_fetch_symbols
    from llm_quant.db.schema import get_connection

    # Use get_all_fetch_symbols to include non-tradeable reference data
    # (VIX, VIX3M) which are required for regime and crash-detection signals.
    symbols = get_all_fetch_symbols(config)
    console.print(f"Fetching data for {len(symbols)} symbols...")

    with console.status("[bold blue]Downloading from Yahoo Finance..."):
        df = fetch_ohlcv(
            symbols,
            lookback_days=config.data.lookback_days,
            timeout=config.data.fetch_timeout,
        )

    if df.is_empty():
        console.print(
            "[red]FAIL[/red] No data fetched. Check your internet connection."
        )
        raise typer.Exit(1)

    console.print(f"  Fetched {len(df)} rows for {df['symbol'].n_unique()} symbols")

    with console.status("[bold blue]Computing indicators..."):
        df = compute_indicators(df)

    conn = get_connection(db_path)
    count = upsert_market_data(conn, df)
    conn.close()

    console.print(f"[green]OK[/green] Stored {count} rows in database")


# --- run - pod-aware -------------------------------------------------------


def _run_single_pod(
    pod_id: str,
    *,
    dry_run: bool = False,
    broker: str = "paper",
    validation_mode: str = "diagnostic",
) -> None:
    """Execute a full trading cycle for a single pod."""
    from llm_quant.brain.context import build_market_context
    from llm_quant.brain.engine import SignalEngine
    from llm_quant.brain.governor import (
        enforce_governor_constraints,
        fallback_governor_decision,
    )
    from llm_quant.brain.models import Action, TradingDecision
    from llm_quant.broker.execution.exit_adapter import convert_exit_to_orders
    from llm_quant.brain.overlay import OverlayEngine, OverlayUnavailableError
    from llm_quant.broker.alpaca import AlpacaClient, AlpacaError
    from llm_quant.broker.intraday_orders import (
        load_order_states,
        place_oco_exits_for_buys,
        reconcile_orders,
        update_trailing_stops,
        upsert_order_states,
    )
    from llm_quant.broker.monitor import monitor_open_positions
    from llm_quant.broker.reconciliation import reconcile_broker_orders
    from llm_quant.broker.rth import should_skip_intraday
    from llm_quant.data.alpaca_intraday import (
        fetch_intraday_crypto_ohlcv,
        fetch_intraday_ohlcv,
    )
    from llm_quant.data.fetcher import fetch_ohlcv
    from llm_quant.data.indicators import compute_indicators
    from llm_quant.data.store import (
        get_intraday_data,
        get_market_data,
        upsert_intraday_data_with_retry,
        upsert_market_data_with_retry,
    )
    from llm_quant.data.universe import get_tradeable_symbols
    from llm_quant.db.locks import acquire_file_lock
    from llm_quant.db.schema import get_connection
    from llm_quant.risk.manager import RiskManager
    from llm_quant.trading.executor import execute_signals
    from llm_quant.trading.exits import (
        EODFlattenDecision,
        build_exit_policy,
        build_exit_runtime,
        build_exit_telemetry_payload,
        evaluate_broker_exit_status,
        evaluate_position_exits,
        parse_eod_time,
    )
    from llm_quant.trading.intraday import (
        apply_reentry_cooldown,
        apply_scale_in,
        load_position_states,
        log_intraday_context,
        merge_intraday_signals,
        update_peak_prices,
        update_state_from_trades,
        upsert_position_states,
    )
    from llm_quant.trading.telemetry import (
        log_decision_context,
        log_profit_take_event,
    )
    from llm_quant.trading.runtime_controls import (
        apply_expectancy_buy_scale,
        apply_harvest_governance_controls,
        assess_intraday_symbol_freshness,
        compute_peak_nav,
        compute_recent_realized_expectancy,
        filter_signals_by_asset_class,
        has_unprotected_crypto_positions,
        load_latest_harvest_governance_result,
        log_harvest_governance_action,
    )
    from llm_quant.trading.run_lock import acquire_run_lock, slot_for_time
    from llm_quant.strategies.runtime import (
        apply_group_caps,
        apply_max_position_cap,
        apply_regime_multipliers,
        generate_strategy_signals,
        load_specs_for_set,
        merge_strategy_signals,
        required_symbols,
    )
    from llm_quant.strategies.rotation import select_rotated_specs
    from llm_quant.trading.ledger import (
        log_broker_fills,
        log_trades,
        persist_reconciliation_snapshot,
        save_portfolio_snapshot,
    )
    alpaca_client = None
    from llm_quant.trading.portfolio import Portfolio
    from llm_quant.broker.executor import (
        build_entry_order_intents,
        submit_alpaca_orders,
        submit_order_intents,
    )
    from llm_quant.broker.reconciliation import persist_submitted_orders

    config = _get_config_for_pod(pod_id)
    db_path = _get_db_path(config)
    resolved_validation_mode = str(validation_mode or "diagnostic").lower()
    use_strategy_overlay, resolved_signal_source = _resolve_signal_source(config)
    signal_source = str(config.execution.signal_source or "auto").lower()
    strategy_set = str(config.execution.strategy_set or "promoted_default")
    overlay_auth_required = _resolve_overlay_auth_required(
        config,
        use_strategy_overlay=use_strategy_overlay,
        validation_mode=resolved_validation_mode,
    )
    asset_class_map = {
        asset.symbol: asset.asset_class for asset in config.universe.assets
    }
    if use_strategy_overlay and overlay_auth_required and not _overlay_auth_present():
        console.print(
            "[red]FAIL[/red] strategy_overlay requires ANTHROPIC_API_KEY for "
            f"{resolved_validation_mode} validation mode."
        )
        raise typer.Exit(1)
    run_lock = None
    global_lock = None
    log_only = False
    if config.execution.intraday_enabled:
        slot = slot_for_time(
            datetime.now(tz=UTC),
            config.execution.intraday_timeframe_minutes,
        )
        run_lock = acquire_run_lock(pod_id, slot)
        if run_lock is None:
            try:
                skip_conn = get_connection(db_path)
                log_intraday_context(
                    skip_conn,
                    pod_id,
                    datetime.now(tz=UTC),
                    {
                        "timestamp": slot,
                        "signal_source": resolved_signal_source,
                        "strategy_set": strategy_set if use_strategy_overlay else "",
                        "skip_reason": "already_executed",
                        "skip_status": "skipped",
                        "slot": slot,
                    },
                )
                skip_conn.close()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to log already-executed intraday skip context for pod %s",
                    pod_id,
                )
            console.print(
                f"[yellow]Intraday slot {slot} already executed — skipping.[/yellow]"
            )
            return

    global_lock = acquire_file_lock(
        Path("data") / "locks" / "run_global.lock",
        timeout_seconds=config.data.db_lock_timeout_seconds,
        retry_seconds=config.data.db_lock_retry_seconds,
    )
    if global_lock is None:
        console.print(
            "[yellow]WARN[/yellow] Another run is using the DB; skipping this cycle."
        )
        if run_lock:
            run_lock.release()
        return

    conn = get_connection(db_path)
    today = datetime.now(tz=UTC).date()

    if config.execution.intraday_enabled and config.execution.intraday_rth_guard:
        asset_class_filters = {
            str(asset_class).lower()
            for asset_class in (config.execution.asset_class_filter or [])
        }
        uses_crypto_only_runtime = bool(asset_class_filters) and asset_class_filters <= {
            "crypto"
        }
        if uses_crypto_only_runtime:
            console.print(
                "[yellow]Crypto-only intraday pod detected — skipping equities RTH clock guard.[/yellow]"
            )
        else:
            try:
                rth_client = AlpacaClient.from_env()
                if should_skip_intraday(rth_client.is_market_open(), True):
                    if config.execution.log_decisions_when_rth_closed:
                        log_only = True
                        console.print(
                            "[yellow]RTH closed — logging decisions only.[/yellow]"
                        )
                    else:
                        console.print("[yellow]RTH closed — skipped intraday run.[/yellow]")
                        conn.close()
                        if global_lock:
                            global_lock.release()
                        if run_lock:
                            run_lock.release()
                        return
            except AlpacaError as exc:
                console.print(f"[red]FAIL[/red] Alpaca clock check failed: {exc}")
                conn.close()
                if global_lock:
                    global_lock.release()
                if run_lock:
                    run_lock.release()
                raise typer.Exit(1) from exc
    elif config.execution.intraday_enabled and not config.execution.intraday_rth_guard:
        console.print(
            "[yellow]RTH guard disabled — running intraday regardless of market hours.[/yellow]"
        )

    # Step 1: Fetch latest data
    overlay_required_symbols: list[str] = []
    if use_strategy_overlay:
        # Pre-fetch promoted symbols so overlay has the required intraday inputs.
        overlay_required_symbols = required_symbols(
            load_specs_for_set(strategy_set)
        )

    symbols = get_tradeable_symbols(
        config,
        asset_class_filter=config.execution.asset_class_filter,
    )
    if overlay_required_symbols:
        symbols = sorted(set(symbols) | set(overlay_required_symbols))

    console.print(
        f"\n[bold]Step 1/5:[/bold] Fetching market data for {len(symbols)} symbols..."
    )
    skip_daily = (
        config.execution.intraday_enabled
        and config.execution.skip_daily_fetch_when_intraday
    )
    if skip_daily:
        console.print(
            "  [yellow]Skipping daily Yahoo fetch (intraday-only mode).[/yellow]"
        )
    else:
        df = fetch_ohlcv(symbols, lookback_days=config.data.lookback_days)
        if not df.is_empty():
            df = compute_indicators(df)
            lock = acquire_file_lock(
                Path("data") / "locks" / "daily_upsert.lock",
                timeout_seconds=config.data.db_lock_timeout_seconds,
                retry_seconds=config.data.db_lock_retry_seconds,
            )
            if lock is None:
                console.print(
                    "  [yellow]WARN[/yellow] DB busy; skipping daily upsert this run"
                )
            else:
                try:
                    upsert_market_data_with_retry(
                        conn,
                        df,
                        max_retries=config.data.db_upsert_max_retries,
                        retry_delay_seconds=config.data.db_upsert_retry_seconds,
                        timeout_seconds=config.data.db_upsert_timeout_seconds,
                    )
                    console.print(
                        f"  [green]OK[/green] Updated {df['symbol'].n_unique()} symbols"
                    )
                except Exception as exc:  # noqa: BLE001
                    console.print(f"  [yellow]WARN[/yellow] Daily upsert failed: {exc}")
                finally:
                    lock.release()
        else:
            console.print(
                "  [yellow]WARN[/yellow] No new data fetched, using existing DB data"
            )

    if config.execution.intraday_enabled and not log_only:
        console.print("  [bold]Intraday:[/bold] Fetching 5-min bars from Alpaca...")
        try:
            import polars as pl

            intraday_frames = []
            crypto_symbols = [s for s in symbols if asset_class_map.get(s) == "crypto"]
            equity_symbols = [s for s in symbols if asset_class_map.get(s) != "crypto"]
            if equity_symbols:
                intraday_frames.append(
                    fetch_intraday_ohlcv(
                        equity_symbols,
                        timeframe_minutes=config.execution.intraday_timeframe_minutes,
                        lookback_days=config.execution.intraday_lookback_days,
                        timeout=config.data.fetch_timeout,
                    )
                )
            if crypto_symbols:
                intraday_frames.append(
                    fetch_intraday_crypto_ohlcv(
                        crypto_symbols,
                        timeframe_minutes=config.execution.intraday_timeframe_minutes,
                        lookback_days=config.execution.intraday_lookback_days,
                        timeout=config.data.fetch_timeout,
                        symbol_map=config.execution.crypto_symbol_map,
                    )
                )
            intraday_df = (
                pl.concat(intraday_frames, how="vertical") if intraday_frames else None
            )
        except Exception as exc:
            console.print(f"  [yellow]WARN[/yellow] Intraday fetch failed: {exc}")
            intraday_df = None

        if intraday_df is not None and not intraday_df.is_empty():
            intraday_df = compute_indicators(intraday_df, time_col="timestamp")
            lock = acquire_file_lock(
                Path("data") / "locks" / "intraday_upsert.lock",
                timeout_seconds=config.data.db_lock_timeout_seconds,
                retry_seconds=config.data.db_lock_retry_seconds,
            )
            if lock is None:
                console.print(
                    "  [yellow]WARN[/yellow] DB busy; skipping intraday upsert"
                )
            else:
                try:
                    upsert_intraday_data_with_retry(
                        conn,
                        intraday_df,
                        max_retries=config.data.db_upsert_max_retries,
                        retry_delay_seconds=config.data.db_upsert_retry_seconds,
                        timeout_seconds=config.data.db_upsert_timeout_seconds,
                    )
                    console.print(
                        f"  [green]OK[/green] Intraday bars updated for "
                        f"{intraday_df['symbol'].n_unique()} symbols"
                    )
                except Exception as exc:  # noqa: BLE001
                    console.print(
                        f"  [yellow]WARN[/yellow] Intraday upsert failed: {exc}"
                    )
                finally:
                    lock.release()
        else:
            console.print(
                "  [yellow]WARN[/yellow] No intraday data fetched, using existing DB data"
            )
    elif config.execution.intraday_enabled and log_only:
        console.print(
            "  [yellow]WARN[/yellow] RTH closed; using existing intraday DB data"
        )

    # Step 2: Load portfolio
    console.print("[bold]Step 2/5:[/bold] Loading portfolio...")
    initial_capital = _resolve_initial_capital(config, broker)
    portfolio = Portfolio.from_db(conn, initial_capital, pod_id=pod_id)
    states: dict[str, Any] = {}

    # Get latest prices for portfolio
    if config.execution.intraday_enabled:
        latest = conn.execute("""
            SELECT symbol, close as price FROM market_data_intraday
            WHERE (symbol, timestamp) IN (
                SELECT symbol, MAX(timestamp) FROM market_data_intraday GROUP BY symbol
            )
            """).pl()
    else:
        latest = conn.execute("""
            SELECT symbol, close as price FROM market_data_daily
            WHERE (symbol, date) IN (
                SELECT symbol, MAX(date) FROM market_data_daily GROUP BY symbol
            )
            """).pl()
    prices = dict(
        zip(
            latest["symbol"].to_list(),
            latest["price"].to_list(),
            strict=True,
        )
    )
    portfolio.update_prices(prices)

    live_account_context = None
    if broker.lower() == "alpaca":
        live_account_context = _sync_live_alpaca_cash(portfolio)
        _sync_live_alpaca_positions(portfolio)

    peak_nav = compute_peak_nav(conn, pod_id, initial_capital)
    current_drawdown_pct = (
        ((portfolio.nav - peak_nav) / peak_nav * 100.0) if peak_nav > 0 else 0.0
    )

    console.print(
        f"  NAV: ${portfolio.nav:,.2f} | Cash: ${portfolio.cash:,.2f}"
        f" | Positions: {len(portfolio.positions)}"
    )
    if live_account_context is not None:
        console.print(
            f"  Live Alpaca Equity: ${live_account_context['equity']:,.2f}"
        )
    console.print(
        f"  Peak NAV: ${peak_nav:,.2f} | Drawdown: {current_drawdown_pct:.2f}%"
    )

    # Step 3: Build context and get Claude's signals
    console.print("[bold]Step 3/5:[/bold] Generating signals...")
    portfolio_state = portfolio.to_snapshot_dict()
    context = build_market_context(conn, portfolio_state, config)

    selected_ids: list[str] = []
    selected_specs = []
    strategy_symbols: list[str] = []
    governor_audit: dict[str, object] = {
        "candidate_count": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "scaled_count": 0,
        "policy_violations": [],
        "fallback_required": False,
        "mode": "llm",
        "signal_source": resolved_signal_source,
        "strategy_set": strategy_set if use_strategy_overlay else "",
    }
    overlay_skip_info = {
        "overlay_skipped": False,
        "overlay_skip_reason": "",
        "missing_symbols": [],
        "stale_symbols": [],
    }

    if use_strategy_overlay:
        specs = load_specs_for_set(strategy_set)
        selected_specs = specs
        if config.strategy_rotation.enabled:
            specs, selected_ids = select_rotated_specs(
                conn,
                specs,
                as_of_date=datetime.now(tz=UTC).date(),
                pod_id=pod_id,
                initial_capital=config.general.initial_capital,
                enabled=config.strategy_rotation.enabled,
                window_days=config.strategy_rotation.window_days,
                top_n=config.strategy_rotation.top_n,
                min_trades=config.strategy_rotation.min_trades,
                cooldown_days=config.strategy_rotation.cooldown_days,
            )
            selected_specs = specs

        strategy_symbols = required_symbols(specs)

        # Pre-filter strategy_symbols by symbol_exclude so excluded symbols are never
        # fetched as market data or passed to intraday freshness checks.
        if config.execution.symbol_exclude:
            _excluded_norm = {_normalize_symbol(s) for s in config.execution.symbol_exclude}
            strategy_symbols = [
                s for s in strategy_symbols
                if _normalize_symbol(s) not in _excluded_norm
            ]

        missing_symbols = sorted(set(strategy_symbols) - set(symbols))
        stale_symbols: list[str] = []
        if config.execution.intraday_enabled and not log_only and strategy_symbols:
            stale_age_minutes = max(config.execution.intraday_timeframe_minutes * 2, 1)
            missing_from_bars, stale_symbols, _latest_by_symbol = (
                assess_intraday_symbol_freshness(
                    conn,
                    strategy_symbols,
                    datetime.now(tz=UTC),
                    stale_age_minutes,
                )
            )
            missing_symbols = sorted(set(missing_symbols) | set(missing_from_bars))

        if missing_symbols or stale_symbols:
            reason_parts = []
            if missing_symbols:
                reason_parts.append("missing symbols: " + ", ".join(missing_symbols))
            if stale_symbols:
                reason_parts.append("stale symbols: " + ", ".join(stale_symbols))
            skip_reason = "; ".join(reason_parts)
            overlay_skip_info = {
                "overlay_skipped": True,
                "overlay_skip_reason": skip_reason,
                "missing_symbols": missing_symbols,
                "stale_symbols": stale_symbols,
            }
            console.print(
                "[yellow]WARN[/yellow] Overlay skipped due to symbol availability: "
                f"{skip_reason}"
            )
            decision = TradingDecision(
                date=today,
                market_regime=context.market_regime,
                regime_confidence=0.0,
                regime_reasoning=f"Overlay skipped ({skip_reason}).",
                signals=[],
                portfolio_commentary=(
                    "Overlay skipped because strategy-set intraday inputs were "
                    f"missing or stale ({skip_reason}). No new trades for this slot."
                ),
                decision_type="overlay",
            )
            governor_audit["mode"] = "overlay_skip"
            decision_logger = SignalEngine(config)
        else:
            if config.execution.intraday_enabled:
                start_ts = datetime.now(tz=UTC) - timedelta(
                    days=config.execution.intraday_lookback_days
                )
                indicators_df = get_intraday_data(conn, strategy_symbols, start_ts)
                if "timestamp" in indicators_df.columns:
                    indicators_df = indicators_df.with_columns(
                        indicators_df["timestamp"].alias("date")
                    )
            else:
                start_date = datetime.now(tz=UTC).date() - timedelta(
                    days=config.data.lookback_days
                )
                indicators_df = get_market_data(conn, strategy_symbols, start_date)

            strategy_signals = generate_strategy_signals(
                specs,
                indicators_df,
                portfolio,
                prices,
                datetime.now(tz=UTC).date(),
            )
            strategy_signals = apply_regime_multipliers(
                strategy_signals,
                config.allocation.regime_weight_mult,
                context.market_regime.value,
            )
            merged = merge_strategy_signals(strategy_signals)
            merged = apply_group_caps(merged, config.allocation.strategy_group_caps)
            aggregated = apply_max_position_cap(
                merged, max_position_weight=config.risk.max_position_weight
            )
            candidate_signals = [
                {
                    "symbol": sig.symbol,
                    "action": sig.action.value,
                    "conviction": sig.conviction.value,
                    "target_weight": sig.target_weight,
                    "stop_loss": sig.stop_loss,
                    "take_profit": sig.take_profit,
                    "strategy_id": sig.strategy_id,
                    "reasoning": sig.reasoning,
                }
                for sig in aggregated
            ]
            governor_audit["candidate_count"] = len(candidate_signals)

            overlay_engine = OverlayEngine(config)
            try:
                overlay_decision = overlay_engine.get_overlay_signals(
                    context, candidate_signals
                )
                sanitized_signals, overlay_audit, fallback_required = (
                    enforce_governor_constraints(
                        decision=overlay_decision,
                        candidate_signals=candidate_signals,
                        strict=config.execution.overlay_governor_strict,
                        max_upscale=config.execution.overlay_max_upscale,
                        max_downscale=config.execution.overlay_max_downscale,
                        decision_date=today,
                    )
                )
                governor_audit.update(overlay_audit)
                if fallback_required:
                    fallback_reason = (
                        "strict governor policy violation: "
                        + ", ".join(overlay_audit.get("policy_violations", []))
                    )
                    decision = fallback_governor_decision(
                        context=context,
                        candidate_signals=candidate_signals,
                        reason=fallback_reason,
                    )
                    decision.model = overlay_decision.model
                    decision.prompt_tokens = overlay_decision.prompt_tokens
                    decision.completion_tokens = overlay_decision.completion_tokens
                    decision.total_tokens = overlay_decision.total_tokens
                    decision.cost_usd = overlay_decision.cost_usd
                    decision.raw_response = overlay_decision.raw_response
                    decision.system_prompt = overlay_decision.system_prompt
                    decision.user_prompt = overlay_decision.user_prompt
                    governor_audit["mode"] = "overlay_fallback"
                    console.print(
                        "[yellow]WARN[/yellow] Overlay fallback engaged: "
                        f"{fallback_reason}"
                    )
                else:
                    decision = overlay_decision
                    decision.signals = sanitized_signals
                    governor_audit["mode"] = "overlay_governor"
            except OverlayUnavailableError as exc:
                reason = str(exc)
                decision = fallback_governor_decision(
                    context=context,
                    candidate_signals=candidate_signals,
                    reason=reason,
                )
                governor_audit.update(
                    {
                        "mode": "overlay_fallback",
                        "fallback_required": True,
                        "policy_violations": [reason],
                    }
                )
                console.print(
                    "[yellow]WARN[/yellow] Overlay unavailable; using deterministic "
                    f"governor fallback ({reason})."
                )
            except Exception as exc:  # noqa: BLE001
                reason = f"overlay_error:{exc}"
                decision = fallback_governor_decision(
                    context=context,
                    candidate_signals=candidate_signals,
                    reason=reason,
                )
                governor_audit.update(
                    {
                        "mode": "overlay_fallback",
                        "fallback_required": True,
                        "policy_violations": [reason],
                    }
                )
                console.print(
                    "[yellow]WARN[/yellow] Overlay call failed; using deterministic "
                    "governor fallback."
                )
            decision_logger = SignalEngine(config)
    else:
        console.print("[bold]Step 3/5:[/bold] Consulting Claude...")
        engine = SignalEngine(config)
        decision = engine.get_signals(context)
        decision_logger = engine

    decision.pod_id = pod_id
    if not getattr(decision, "decision_type", None):
        decision.decision_type = "llm"

    decision.signals, filtered_count = filter_signals_by_asset_class(
        decision.signals,
        asset_class_map,
        config.execution.asset_class_filter,
    )
    if filtered_count > 0:
        console.print(
            "[yellow]WARN[/yellow] Filtered out "
            f"{filtered_count} signal(s) outside asset_class_filter."
        )

    # Filter out any symbols explicitly excluded from this pod's execution universe
    if config.execution.symbol_exclude:
        excluded_set = {s.upper().replace("-", "").replace("/", "") for s in config.execution.symbol_exclude}
        before = len(decision.signals)
        decision.signals = [
            sig for sig in decision.signals
            if sig.symbol.upper().replace("-", "").replace("/", "") not in excluded_set
        ]
        dropped = before - len(decision.signals)
        if dropped:
            console.print(
                f"[yellow]WARN[/yellow] Filtered out {dropped} signal(s) in symbol_exclude list."
            )

    # Display decision
    _display_decision(decision)

    if dry_run:
        console.print("\n[yellow]DRY RUN[/yellow] -- no trades executed.")
        conn.close()
        if global_lock:
            global_lock.release()
        if run_lock:
            run_lock.release()
        return

    # Step 4: Risk check and execute
    console.print("[bold]Step 4/5:[/bold] Risk check and execution...")
    risk_mgr = RiskManager(config)
    exit_policy = build_exit_policy(config.risk, config.execution)
    exit_runtime = build_exit_runtime(broker, config.execution)
    exit_policy_state = dataclasses.asdict(exit_policy)

    governance_runtime = load_latest_harvest_governance_result(
        conn,
        config=config,
        pod_id=pod_id,
    )
    signals = apply_harvest_governance_controls(
        decision.signals,
        governance_runtime,
        portfolio_symbols=set(portfolio.positions.keys()),
    )
    if governance_runtime.has_actions:
        log_harvest_governance_action(
            conn,
            pod_id=pod_id,
            runtime_result=governance_runtime,
        )

    signals = signals
    context_payload = None
    now_ts = None
    order_states: dict = {}  # populated early below if intraday_use_oco; reused in OCO section
    if config.execution.intraday_enabled:
        now_row = conn.execute(
            "SELECT MAX(timestamp) FROM market_data_intraday"
        ).fetchone()
        now_ts = now_row[0] if now_row and now_row[0] else datetime.now(tz=UTC)

        states = load_position_states(conn, pod_id)
        update_peak_prices(portfolio, prices, states)

        # Pre-load order_states early so the wash-trade guard below can use it.
        # Do NOT gate on alpaca_client here — it isn't created until line ~1335.
        # DB-only read; safe to do even before broker client is instantiated.
        order_states = (
            load_order_states(conn, pod_id)
            if config.execution.intraday_use_oco
            else {}
        )

        entry_signals = [s for s in signals if s.action == Action.BUY]
        other_signals = [s for s in signals if s.action != Action.BUY]

        entry_signals = apply_scale_in(
            entry_signals,
            portfolio,
            states,
            config.execution.scale_in_tranches,
        )
        entry_signals = apply_reentry_cooldown(
            entry_signals,
            states,
            now_ts,
            config.execution.intraday_timeframe_minutes,
            config.execution.reentry_cooldown_bars,
        )

        # Block new BUY signals for symbols that already have a live protective stop at
        # the broker. Prevents wash-trade conflicts when a prior stop-limit sell covers
        # the full position qty. Normalize symbols (strip /- separators) before comparing
        # since DB may store "XRP/USD" while signals use "XRP-USD".
        if order_states:
            def _norm_sym(s: str) -> str:
                return s.replace("/", "").replace("-", "").upper()

            _protected_normalized = {
                _norm_sym(sym)
                for sym, st in order_states.items()
                if st.oco_stop_order_id and st.stop_status not in ("filled", "cancelled", "expired", "rejected")
            }
            if _protected_normalized:
                before_prot = len(entry_signals)
                entry_signals = [s for s in entry_signals if _norm_sym(s.symbol) not in _protected_normalized]
                if len(entry_signals) < before_prot:
                    console.print(
                        f"[yellow]WARN[/yellow] Blocked {before_prot - len(entry_signals)} BUY signal(s): "
                        "active stop order already covers full position."
                    )

        profit_signals, exit_telemetry = evaluate_position_exits(
            portfolio,
            prices,
            states,
            exit_policy,
            exit_runtime,
        )
        if any(
            item.unprotected for item in exit_telemetry
        ) and exit_policy.fail_on_unprotected_exits:
            console.print(
                "[red]FAIL[/red] Canonical exit engine detected unprotected live position(s)."
            )
            conn.close()
            if global_lock:
                global_lock.release()
            if run_lock:
                run_lock.release()
            raise typer.Exit(1)

        if (
            broker.lower() == "alpaca"
            and exit_policy.fail_on_unprotected_exits
            and has_unprotected_crypto_positions(
                portfolio.positions,
                asset_class_map,
                exit_runtime,
            )
        ):
            console.print(
                "[red]FAIL[/red] Live crypto execution requires broker-managed protection."
            )
            conn.close()
            if global_lock:
                global_lock.release()
            if run_lock:
                run_lock.release()
            raise typer.Exit(1)
        if (
            broker.lower() == "alpaca"
            and config.execution.intraday_enabled
            and not config.execution.intraday_use_oco
        ):
            broker_exit_statuses = evaluate_broker_exit_status(
                portfolio,
                prices,
                states,
                exit_policy,
            )
            synthetic_exit_intents = []
            for status in broker_exit_statuses:
                synthetic_exit_intents.extend(
                    convert_exit_to_orders(
                        status,
                        status.remaining_qty,
                        allow_fractional=asset_class_map.get(status.symbol, "equity").lower()
                        == "crypto",
                    )
                )
            if synthetic_exit_intents:
                if alpaca_client is None:
                    try:
                        alpaca_client = AlpacaClient.from_env()
                    except AlpacaError as exc:
                        console.print(f"[red]FAIL[/red] Alpaca client init failed: {exc}")
                        conn.close()
                        _release_runtime_locks(global_lock, run_lock)
                        raise typer.Exit(1) from exc
                submitted_exit_orders = submit_order_intents(
                    alpaca_client,
                    synthetic_exit_intents,
                    config.execution,
                )
                persist_submitted_orders(conn, submitted_exit_orders, pod_id=pod_id)
                reconcile_broker_orders(
                    conn,
                    alpaca_client,
                    portfolio=portfolio,
                    ledger_conn=conn,
                    pod_id=pod_id,
                    order_ids=[order.order_id for order in submitted_exit_orders],
                    broker_positions=alpaca_client.list_positions(),
                    log_fills_fn=log_broker_fills,
                    trade_date=today,
                    log_kwargs={
                        "decision_id": None,
                        "decision_source": resolved_signal_source,
                        "sleeve": strategy_set if use_strategy_overlay else None,
                        "source_decision_id": None,
                        "exit_policy_state": exit_policy_state,
                    },
                )
        if exit_runtime.exit_mode == "synthetic":
            signals = merge_intraday_signals(
                entry_signals,
                other_signals,
                profit_signals,
            )
        else:
            signals = other_signals + entry_signals

        # Build intraday context snapshot payload for audit (logged below).
        context_payload = {
            "timestamp": str(now_ts),
            "market_context": dataclasses.asdict(context),
            "signals": [
                {
                    "symbol": s.symbol,
                    "action": s.action.value,
                    "target_weight": s.target_weight,
                    "strategy_id": s.strategy_id,
                    "exit_reason": s.exit_reason,
                }
                for s in signals
            ],
            **build_exit_telemetry_payload(exit_telemetry, exit_policy, exit_runtime),
        }
        if use_strategy_overlay:
            context_payload["selected_strategies"] = (
                selected_ids
                if selected_ids
                else [spec.slug for spec in selected_specs]
            )

    expectancy_value = None
    expectancy_sample_size = 0
    expectancy_gate_active = False
    buy_scale_applied = 1.0
    if config.execution.expectancy_gate_enabled:
        expectancy_value, expectancy_sample_size = compute_recent_realized_expectancy(
            conn,
            pod_id=pod_id,
            lookback_closed_trades=config.execution.expectancy_lookback_closed_trades,
        )
        if expectancy_value is not None and expectancy_value < 0:
            buy_scale_applied = config.execution.expectancy_negative_scale
            scaled_count = apply_expectancy_buy_scale(signals, buy_scale_applied)
            expectancy_gate_active = scaled_count > 0
            if expectancy_gate_active:
                console.print(
                    "[yellow]WARN[/yellow] Expectancy gate active: "
                    f"scaled BUY target weights by {buy_scale_applied:.2f}x "
                    f"(expectancy={expectancy_value:.2f}, n={expectancy_sample_size})."
                )

    if context_payload is not None and now_ts is not None:
        context_payload["signals"] = [
            {
                "symbol": s.symbol,
                "action": s.action.value,
                "target_weight": s.target_weight,
                "strategy_id": s.strategy_id,
                "exit_reason": s.exit_reason,
            }
            for s in signals
        ]
        context_payload["overlay_skipped"] = overlay_skip_info["overlay_skipped"]
        context_payload["overlay_skip_reason"] = overlay_skip_info[
            "overlay_skip_reason"
        ]
        context_payload["missing_symbols"] = overlay_skip_info["missing_symbols"]
        context_payload["stale_symbols"] = overlay_skip_info["stale_symbols"]
        context_payload["signal_source"] = resolved_signal_source
        context_payload["strategy_set"] = strategy_set if use_strategy_overlay else ""
        context_payload["governor_mode"] = governor_audit.get("mode", "llm")
        context_payload["candidate_count"] = governor_audit.get("candidate_count", 0)
        context_payload["accepted_count"] = governor_audit.get("accepted_count", 0)
        context_payload["rejected_count"] = governor_audit.get("rejected_count", 0)
        context_payload["scaled_count"] = governor_audit.get("scaled_count", 0)
        context_payload["policy_violations"] = governor_audit.get(
            "policy_violations", []
        )
        context_payload["peak_nav"] = peak_nav
        context_payload["current_drawdown_pct"] = round(current_drawdown_pct, 4)
        context_payload["expectancy_gate_active"] = expectancy_gate_active
        context_payload["expectancy_value"] = (
            round(expectancy_value, 6) if expectancy_value is not None else None
        )
        context_payload["expectancy_sample_size"] = expectancy_sample_size
        context_payload["buy_scale_applied"] = buy_scale_applied
        context_payload["harvest_governance"] = {
            "metrics": governance_runtime.metrics,
            "breached_rules": governance_runtime.breached_rules,
            "actions": governance_runtime.actions,
            "allocation_scale": governance_runtime.allocation_scale,
            "active_mandate_name": governance_runtime.active_mandate_name,
            "active_mandate_type": governance_runtime.active_mandate_type,
            "conservative_mandate_name": governance_runtime.conservative_mandate_name,
            "force_flatten": governance_runtime.force_flatten,
            "lifecycle_recommendation": governance_runtime.lifecycle_recommendation,
        }
        log_intraday_context(conn, pod_id, now_ts, context_payload)

    approved, rejected = risk_mgr.filter_signals(signals, portfolio, prices)

    if rejected:
        console.print(
            f"  [yellow]WARN[/yellow] {len(rejected)} signals rejected by risk manager:"
        )
        for sig, checks in rejected:
            failed = [c for c in checks if not c.passed]
            reasons = ", ".join(c.message for c in failed)
            console.print(f"    {sig.symbol} {sig.action.value}: {reasons}")

    # Log decision + context (always)
    decision_id = decision_logger.log_decision(conn, decision)
    log_decision_context(
        conn,
        decision_id,
        pod_id,
        context,
        extra={
            "signal_source": signal_source,
            "resolved_signal_source": resolved_signal_source,
            "strategy_set": strategy_set if use_strategy_overlay else "",
            "overlay_skipped": overlay_skip_info["overlay_skipped"],
            "overlay_skip_reason": overlay_skip_info["overlay_skip_reason"],
            "governor_audit": governor_audit,
        },
    )

    if broker.lower() == "alpaca":
        try:
            alpaca_client = AlpacaClient.from_env()
        except AlpacaError as exc:
            console.print(f"[red]FAIL[/red] Alpaca client init failed: {exc}")
            conn.close()
            if global_lock:
                global_lock.release()
            raise typer.Exit(1) from exc

        try:
            _monitor_and_reconcile_broker_drift(
                conn=conn,
                client=alpaca_client,
                portfolio=portfolio,
                pod_id=pod_id,
                tracked_symbols=list(asset_class_map),
                decision_id=decision_id,
                resolved_signal_source=resolved_signal_source,
                strategy_set=strategy_set if use_strategy_overlay else None,
                exit_policy_state=exit_policy_state,
            )
        except Exception as exc:
            logger.warning("Broker drift reconciliation failed (non-fatal): %s", exc)
    executed = []
    if log_only:
        console.print(
            "  [yellow]RTH closed — decisions logged, no trades executed.[/yellow]"
        )
    elif approved:
        executed = execute_signals(
            portfolio,
            approved,
            prices,
            portfolio.nav,
            asset_class_map=asset_class_map,
            reserve_cash=max(
                float(getattr(config.risk, "min_cash_reserve", 0.0)) * portfolio.nav,
                0.0,
            ),
        )
        console.print(f"  [green]OK[/green] Executed {len(executed)} trades")

        if config.execution.intraday_enabled and now_ts is not None:
            update_state_from_trades(states, executed, now_ts)
            cooldown_delta = timedelta(
                minutes=config.execution.intraday_timeframe_minutes
                * config.execution.reentry_cooldown_bars
            )
            for state in states.values():
                if state.last_exit_ts:
                    state.cooldown_until_ts = state.last_exit_ts + cooldown_delta
            upsert_position_states(conn, pod_id, states)

        submitted_orders: list = []
        fill_prices: dict[str, float] = {}
        if alpaca_client and executed:
            stop_losses = {sig.symbol: sig.stop_loss for sig in approved}
            try:
                submitted_orders = submit_alpaca_orders(
                    alpaca_client,
                    executed,
                    stop_losses,
                    config.risk,
                    use_brackets=not config.execution.intraday_enabled,
                    asset_class_map=asset_class_map,
                    execution=config.execution,
                )
                persist_submitted_orders(conn, submitted_orders, pod_id=pod_id)
                reconcile_broker_orders(
                    conn,
                    alpaca_client,
                    portfolio=portfolio,
                    ledger_conn=conn,
                    pod_id=pod_id,
                    order_ids=[order.order_id for order in submitted_orders],
                    broker_positions=alpaca_client.list_positions(),
                    log_fills_fn=log_broker_fills,
                    trade_date=today,
                    log_kwargs={
                        "decision_id": None,
                        "decision_source": resolved_signal_source,
                        "sleeve": strategy_set if use_strategy_overlay else None,
                        "source_decision_id": None,
                        "exit_policy_state": exit_policy_state,
                    },
                )
                # H5: roll back portfolio state for rejected/cancelled entry orders
                _rejected = {"rejected", "cancelled", "expired"}
                for order in submitted_orders:
                    if order.intent_type != "entry" or order.side != "buy":
                        continue
                    if (order.status or "").lower() not in _rejected:
                        continue
                    pos = portfolio.positions.get(order.symbol)
                    if pos is not None:
                        refund = pos.shares * pos.avg_cost
                        if refund <= 0 and order.notional:
                            refund = float(order.notional)
                        portfolio.cash += refund
                        del portfolio.positions[order.symbol]
                        logger.warning(
                            "Rolled back ghost position for %s after %s order (order_id=%s)",
                            order.symbol,
                            order.status,
                            order.order_id,
                        )
                # H7: collect actual fill prices for OCO exit placement
                for order in submitted_orders:
                    if order.intent_type == "entry" and order.side == "buy":
                        if order.filled_avg_price and order.filled_avg_price > 0:
                            fill_prices[order.symbol] = order.filled_avg_price
            except AlpacaError as exc:
                console.print(f"[red]FAIL[/red] Alpaca execution failed: {exc}")
                conn.close()
                if global_lock:
                    global_lock.release()
                raise typer.Exit(1) from exc

        # Log trades
        trade_ids = log_trades(
            conn,
            executed,
            today,
            decision_id,
            pod_id=pod_id,
            decision_source=resolved_signal_source,
            sleeve=strategy_set if use_strategy_overlay else None,
            source_decision_id=decision_id,
        )
        for trade, trade_id in zip(executed, trade_ids, strict=True):
            if not trade.exit_reason:
                continue
            log_profit_take_event(
                conn,
                timestamp=now_ts,
                pod_id=pod_id,
                symbol=trade.symbol,
                event_type="executed",
                decision_source=resolved_signal_source,
                sleeve=strategy_set if use_strategy_overlay else None,
                source_decision_id=decision_id,
                decision_id=decision_id,
                trade_id=trade_id,
                entry_batch=trade.entry_batch,
                action=trade.action,
                shares=trade.shares,
                price=trade.price,
                notional=trade.notional,
                trigger_price=prices.get(trade.symbol, trade.price),
                reason=trade.exit_reason,
                metadata={
                    "reasoning": trade.reasoning,
                    "strategy_id": trade.strategy_id,
                },
            )
        console.print(f"  [green]OK[/green] Logged trade IDs: {trade_ids}")
    else:
        console.print("  No trades to execute.")

    if (
        config.execution.intraday_enabled
        and alpaca_client
        and not log_only
        and config.execution.intraday_use_oco
    ):
        # order_states already loaded above (or an empty dict for non-intraday paths)
        if not order_states:
            order_states = load_order_states(conn, pod_id)
        try:
            raw_positions = alpaca_client.list_positions()
            positions = {
                p.get("symbol", ""): float(p.get("qty", 0.0))
                for p in raw_positions
            }
            asset_class_map = {
                p.get("symbol", ""): p.get("asset_class", "us_equity")
                for p in raw_positions
            }
        except AlpacaError as exc:
            console.print(f"[yellow]WARN[/yellow] Alpaca positions failed: {exc}")
            positions = {}
            asset_class_map = {}

        reconcile_orders(
            alpaca_client,
            order_states,
            positions,
            trailing_pct=config.execution.trailing_stop_pct,
            fail_on_unprotected=exit_policy.fail_on_unprotected_exits,
            asset_class_map=asset_class_map,
        )
        update_trailing_stops(
            alpaca_client,
            order_states,
            prices,
            trailing_pct=config.execution.trailing_stop_pct,
        )

        if approved and executed:
            stop_losses = {sig.symbol: sig.stop_loss for sig in approved}
            place_oco_exits_for_buys(
                alpaca_client,
                order_states,
                executed,
                stop_losses,
                partial_tp_pct=exit_policy.partial_take_profit_pct,
                partial_tp_size=exit_policy.partial_take_profit_size,
                remainder_tp_mult=exit_policy.remainder_take_profit_mult,
                default_stop_loss_pct=config.risk.default_stop_loss_pct,
                fail_on_unprotected=exit_policy.fail_on_unprotected_exits,
                fill_prices=fill_prices,
                asset_class_map=asset_class_map,
            )

        elif (
            config.execution.intraday_enabled
            and alpaca_client
            and not log_only
            and not config.execution.intraday_use_oco
            and approved
            and executed
        ):
            # Non-OCO intraday path: attach protection after entry fill confirmation
            from llm_quant.broker.execution.exit_adapter import (
                submit_post_fill_protection_orders,
            )

            execution_cfg = config.execution
            for order in submitted_orders:
                if order.intent_type != "entry" or order.side != "buy":
                    continue
                if order.filled_qty <= 0:
                    logger.warning(
                        "Skipping post-fill protection for %s: entry not yet filled (qty=%.6f)",
                        order.symbol,
                        order.filled_qty,
                    )
                    continue
                try:
                    protection_orders = submit_post_fill_protection_orders(
                        alpaca_client,
                        order,
                        execution_cfg,
                    )
                    if protection_orders:
                        persist_submitted_orders(conn, protection_orders, pod_id=pod_id)
                        logger.info(
                            "Submitted %d post-fill protection orders for %s",
                            len(protection_orders),
                            order.symbol,
                        )
                except Exception as exc:
                    logger.warning(
                        "Post-fill protection failed for %s (non-fatal): %s",
                        order.symbol,
                        exc,
                    )

        upsert_order_states(conn, pod_id, order_states)
        try:
            _monitor_and_reconcile_broker_drift(
                conn=conn,
                client=alpaca_client,
                portfolio=portfolio,
                pod_id=pod_id,
                tracked_symbols=list(order_states),
                decision_id=decision_id,
                resolved_signal_source=resolved_signal_source,
                strategy_set=strategy_set if use_strategy_overlay else None,
                exit_policy_state=exit_policy_state,
            )
        except Exception as exc:
            logger.warning("Broker drift reconciliation failed (non-fatal): %s", exc)

    # Step 5: Save snapshot
    console.print("[bold]Step 5/5:[/bold] Saving portfolio snapshot...")
    snap_id = save_portfolio_snapshot(conn, portfolio, today, pod_id=pod_id)
    persist_reconciliation_snapshot(
        conn,
        pod_id=pod_id,
        snapshot_date=today,
        snapshot={
            "intraday_position_state": context_payload.get("exit_engine", {}).get("positions", {})
            if context_payload
            else {},
            "order_state": {},
            "lifecycle_state": {},
            "exit_policy_state": exit_policy_state,
        },
    )
    console.print(f"  [green]OK[/green] Snapshot #{snap_id} saved")

    conn.close()
    if global_lock:
        global_lock.release()
    if run_lock:
        run_lock.release()
    console.print(f"\n[bold green]Done.[/bold green] NAV: ${portfolio.nav:,.2f}")


@app.command()
def run(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Show signals without executing trades",
    ),
    pod: str = typer.Option("default", "--pod", "-p", help="Pod to operate on"),
    all_pods: bool = typer.Option(
        False, "--all-pods", help="Run all active pods sequentially"
    ),
    broker: str = typer.Option(
        "paper",
        "--broker",
        help="Execution broker: paper | alpaca",
    ),
    validation_mode: str = typer.Option(
        "diagnostic",
        "--validation-mode",
        help="Validation mode: diagnostic | paper",
    ),
):
    """Full cycle: fetch -> indicators -> Claude -> trade -> log."""
    _setup_logging()

    if all_pods:
        config = _get_config()
        db_path = _get_db_path(config)

        from llm_quant.db.schema import get_connection

        conn = get_connection(db_path)
        try:
            rows = conn.execute(
                "SELECT pod_id FROM pods WHERE status = 'active' ORDER BY pod_id"
            ).fetchall()
        except (duckdb.CatalogException, duckdb.BinderException):
            console.print(
                "[red]FAIL[/red] Could not query pods table. "
                "Run [bold]pq init[/bold] first."
            )
            conn.close()
            raise typer.Exit(1) from None
        conn.close()

        if not rows:
            console.print("[yellow]No active pods found.[/yellow]")
            return

        for (pid,) in rows:
            console.rule(f"[bold]Pod: {pid}")
            _run_single_pod(
                pid,
                dry_run=dry_run,
                broker=broker,
                validation_mode=validation_mode,
            )
        return

    _run_single_pod(
        pod,
        dry_run=dry_run,
        broker=broker,
        validation_mode=validation_mode,
    )


@app.command()
def eod_flat(
    pod: str = typer.Option("default", "--pod", "-p", help="Pod to operate on"),
):
    """Flatten all positions at the configured end-of-day time."""
    _setup_logging()

    from llm_quant.broker.alpaca import AlpacaClient, AlpacaError
    from llm_quant.broker.monitor import monitor_open_positions
    from llm_quant.broker.reconciliation import reconcile_broker_orders
    from llm_quant.brain.models import Action, Conviction, TradeSignal
    from llm_quant.db.schema import get_connection
    from llm_quant.trading.executor import execute_signals
    from llm_quant.trading.exits import (
        EODFlattenDecision,
        assess_eod_flatten,
        build_exit_policy,
        build_exit_runtime,
    )
    from llm_quant.trading.ledger import (
        log_broker_fills,
        log_trades,
        save_portfolio_snapshot,
    )
    from llm_quant.trading.portfolio import Portfolio

    config = _get_config_for_pod(pod)
    limits = config.risk
    exit_policy = build_exit_policy(config.risk, config.execution)
    exit_runtime = build_exit_runtime("alpaca", config.execution)

    if not exit_policy.eod_flatten_enabled:
        console.print("[yellow]EOD flatten disabled in config.[/yellow]")
        return

    target_time = _parse_eod_time(exit_policy.eod_flatten_time)
    flatten_decision = EODFlattenDecision(
        enabled=exit_policy.eod_flatten_enabled,
        target_time=target_time,
        due=False,
        reason="before_cutoff",
    )
    if exit_runtime.is_crypto:
        flatten_decision = EODFlattenDecision(
            enabled=False,
            target_time=target_time,
            due=False,
            reason="disabled_for_crypto",
        )

    if flatten_decision.reason == "disabled_for_crypto":
        console.print(
            "[yellow]EOD flatten disabled for crypto runtime semantics.[/yellow]"
        )
        return

    try:
        client = AlpacaClient.from_env()
        now_et = client.clock_timestamp_et()
        flatten_decision = assess_eod_flatten(
            exit_policy,
            now_et=now_et,
            market_is_open=client.is_market_open(),
            runtime=exit_runtime,
        )
        if flatten_decision.reason == "market_closed":
            console.print("[yellow]Market closed — skipping EOD flatten.[/yellow]")
            return
        if not flatten_decision.due:
            console.print(
                f"[yellow]EOD flatten scheduled for {flatten_decision.target_time} ET; "
                f"current time {now_et.time().strftime('%H:%M')} ET.[/yellow]"
            )
            return
    except AlpacaError as exc:
        console.print(f"[red]FAIL[/red] Alpaca clock check failed: {exc}")
        raise typer.Exit(1) from exc

    db_path = _get_db_path(config)
    conn = get_connection(db_path)
    deadline = datetime.now(tz=UTC) + timedelta(seconds=300)
    positions = client.list_positions()
    while positions and datetime.now(tz=UTC) < deadline:
        for pos in positions:
            qty = float(pos.get("qty", 0.0))
            if qty == 0:
                continue
            side = "sell" if qty > 0 else "buy"
            client.submit_market_order(
                symbol=pos.get("symbol", ""),
                qty=abs(qty),
                side=side,
            )
        time.sleep(15)
        positions = client.list_positions()

    reconciliation = reconcile_broker_orders(
        conn,
        client,
        portfolio=None,
        ledger_conn=conn,
        pod_id=pod,
        broker_positions=client.list_positions(),
        log_fills_fn=log_broker_fills,
        trade_date=now_et.date(),
        log_kwargs={
            "decision_id": None,
            "decision_source": "system",
            "sleeve": None,
            "source_decision_id": None,
            "exit_policy_state": dataclasses.asdict(exit_policy),
        },
    )
    if client.list_positions():
        conn.close()
        raise RuntimeError("EOD FLATTEN FAILED")

    portfolio = Portfolio.from_db(conn, config.general.initial_capital, pod_id=pod)
    try:
        _monitor_and_reconcile_broker_drift(
            conn=conn,
            client=client,
            portfolio=portfolio,
            pod_id=pod,
            tracked_symbols=None,
            decision_id=None,
            resolved_signal_source="system",
            strategy_set=None,
            exit_policy_state=dataclasses.asdict(exit_policy),
        )
    except Exception as exc:
        logger.warning("Broker drift reconciliation failed (non-fatal): %s", exc)

    prices = {
        symbol: float((position or {}).get("current_price", 0.0))
        for symbol, position in (reconciliation.snapshot or {})
        .get("intraday_position_state", {})
        .items()
    }
    portfolio.update_prices(prices)

    close_signals: list[TradeSignal] = []
    for pos in positions:
        symbol = pos.get("symbol", "")
        if not symbol:
            continue
        close_signals.append(
            TradeSignal(
                symbol=symbol,
                action=Action.CLOSE,
                conviction=Conviction.LOW,
                target_weight=0.0,
                stop_loss=0.0,
                reasoning="eod_flatten",
            )
        )

    asset_class_map = {
        asset.symbol: asset.asset_class for asset in config.universe.assets
    }
    executed = execute_signals(
        portfolio,
        close_signals,
        prices,
        portfolio.nav,
        asset_class_map=asset_class_map,
        reserve_cash=0.0,
    )
    today = now_et.date()
    if executed:
        log_trades(
            conn,
            executed,
            today,
            decision_id=None,
            pod_id=pod,
            decision_source="system",
            sleeve=None,
            source_decision_id=None,
        )
    save_portfolio_snapshot(conn, portfolio, today, pod_id=pod)
    conn.close()

    console.print(
        f"[green]EOD flatten complete.[/green] Orders submitted: {len(positions)}"
    )


def _display_decision(decision: Any) -> None:
    """Pretty-print a TradingDecision."""

    regime_colors = {"risk_on": "green", "risk_off": "red", "transition": "yellow"}
    color = regime_colors.get(decision.market_regime.value, "white")

    console.print(
        f"\n  Regime: [{color}]{decision.market_regime.value}[/{color}] "
        f"(confidence: {decision.regime_confidence:.0%})"
    )
    console.print(f"  Reasoning: {decision.regime_reasoning}")

    if decision.signals:
        table = Table(title="Trade Signals", show_lines=False)
        table.add_column("Symbol", style="bold")
        table.add_column("Action")
        table.add_column("Conviction")
        table.add_column("Target Wt")
        table.add_column("Stop Loss")
        table.add_column("Reasoning", max_width=50)

        action_colors = {
            "buy": "green",
            "sell": "red",
            "close": "red",
            "hold": "yellow",
        }
        for sig in decision.signals:
            a_color = action_colors.get(sig.action.value, "white")
            table.add_row(
                sig.symbol,
                f"[{a_color}]{sig.action.value.upper()}[/{a_color}]",
                sig.conviction.value,
                f"{sig.target_weight:.1%}",
                f"${sig.stop_loss:.2f}",
                sig.reasoning[:50],
            )
        console.print(table)

    if decision.portfolio_commentary:
        console.print(f"\n  Commentary: {decision.portfolio_commentary}")

    if decision.total_tokens > 0:
        console.print(
            f"  Tokens: {decision.total_tokens} | Cost: ${decision.cost_usd:.4f}"
        )


# ---------------------------------------------------------------------------
# status (pod-aware, with --all flag)
# ---------------------------------------------------------------------------


def _show_all_pods_dashboard(conn) -> None:
    """Show comparative dashboard across all pods."""
    try:
        rows = conn.execute("""
            SELECT
                ps.pod_id,
                ps.nav,
                ps.cash,
                ps.total_pnl,
                ps.gross_exposure,
                (SELECT COUNT(*) FROM positions p
                 WHERE p.snapshot_id = ps.snapshot_id) as positions
            FROM portfolio_snapshots ps
            INNER JOIN (
                SELECT pod_id, MAX(snapshot_id) as max_id
                FROM portfolio_snapshots
                GROUP BY pod_id
            ) latest ON ps.pod_id = latest.pod_id AND ps.snapshot_id = latest.max_id
            ORDER BY ps.pod_id
        """).fetchall()
    except (duckdb.CatalogException, duckdb.BinderException):
        console.print("[dim]No snapshot data available.[/dim]")
        return

    if not rows:
        console.print("[dim]No pod snapshots found.[/dim]")
        return

    table = Table(title="All Pods Dashboard")
    table.add_column("Pod ID", style="bold")
    table.add_column("NAV", justify="right")
    table.add_column("Cash %", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("Positions", justify="right")
    table.add_column("Gross Exposure", justify="right")

    for pod_id, raw_nav, raw_cash, raw_pnl, raw_exposure, positions in rows:
        nav_f = float(raw_nav) if raw_nav else 0.0
        cash_f = float(raw_cash) if raw_cash else 0.0
        pnl_f = float(raw_pnl) if raw_pnl else 0.0
        exp_f = float(raw_exposure) if raw_exposure else 0.0
        cash_pct = (cash_f / nav_f * 100) if nav_f > 0 else 0.0
        pnl_color = "green" if pnl_f >= 0 else "red"
        table.add_row(
            pod_id,
            f"${nav_f:,.2f}",
            f"{cash_pct:.1f}%",
            f"[{pnl_color}]${pnl_f:,.2f}[/{pnl_color}]",
            str(positions or 0),
            f"${exp_f:,.2f}",
        )

    console.print(table)


@app.command()
def status(
    pod: str = typer.Option("default", "--pod", "-p", help="Pod to operate on"),
    all: bool = typer.Option(  # noqa: A002
        False, "--all", "-a", help="Show comparative dashboard across all pods"
    ),
):
    """Show current portfolio status and metrics."""
    _setup_logging("WARNING")

    from llm_quant.db.schema import get_connection
    from llm_quant.trading.performance import compute_performance
    from llm_quant.trading.portfolio import Portfolio

    config = _get_config_for_pod(pod)
    db_path = _get_db_path(config)
    conn = get_connection(db_path)

    if all:
        _show_all_pods_dashboard(conn)
        conn.close()
        return

    portfolio = Portfolio.from_db(conn, config.general.initial_capital, pod_id=pod)

    # Update with latest prices
    latest = conn.execute("""
        SELECT symbol, close as price FROM market_data_daily
        WHERE (symbol, date) IN (
            SELECT symbol, MAX(date) FROM market_data_daily GROUP BY symbol
        )
        """).pl()
    if not latest.is_empty():
        prices = dict(
            zip(latest["symbol"].to_list(), latest["price"].to_list(), strict=True)
        )
        portfolio.update_prices(prices)

    # Portfolio summary
    cash_pct = portfolio.cash / portfolio.nav * 100
    pnl_pct = portfolio.total_pnl / portfolio.initial_capital * 100
    title = f"Portfolio Status (pod: {pod})" if pod != "default" else "Portfolio Status"
    console.print(
        Panel(
            f"[bold]NAV:[/bold] ${portfolio.nav:,.2f}  |  "
            f"[bold]Cash:[/bold] ${portfolio.cash:,.2f} ({cash_pct:.1f}%)  |  "
            f"[bold]P&L:[/bold] ${portfolio.total_pnl:,.2f} ({pnl_pct:+.2f}%)",
            title=title,
        )
    )

    # Positions table — filter to active (shares > 0) from latest snapshot
    active_positions = {s: p for s, p in portfolio.positions.items() if p.shares > 0}
    if active_positions:
        table = Table(title="Positions")
        table.add_column("Symbol", style="bold")
        table.add_column("Shares", justify="right")
        table.add_column("Avg Cost", justify="right")
        table.add_column("Current", justify="right")
        table.add_column("Mkt Value", justify="right")
        table.add_column("P&L", justify="right")
        table.add_column("P&L %", justify="right")
        table.add_column("Weight", justify="right")
        table.add_column("Stop", justify="right")

        for sym, pos in sorted(active_positions.items()):
            pnl_color = "green" if pos.unrealized_pnl >= 0 else "red"
            weight = pos.market_value / portfolio.nav * 100
            share_fmt = f"{pos.shares:.6f}" if abs(pos.shares - round(pos.shares)) > 1e-8 else f"{pos.shares:.0f}"
            table.add_row(
                sym,
                share_fmt,
                f"${pos.avg_cost:.2f}",
                f"${pos.current_price:.2f}",
                f"${pos.market_value:,.2f}",
                f"[{pnl_color}]${pos.unrealized_pnl:,.2f}[/{pnl_color}]",
                f"[{pnl_color}]{pos.pnl_pct:+.1f}%[/{pnl_color}]",
                f"{weight:.1f}%",
                f"${pos.stop_loss:.2f}" if pos.stop_loss > 0 else "-",
            )
        console.print(table)
    else:
        console.print("[dim]No open positions.[/dim]")

    # Performance metrics
    metrics = compute_performance(conn, config.general.initial_capital)
    if metrics.get("total_trades", 0) > 0:
        console.print(
            Panel(
                f"[bold]Total Return:[/bold] {metrics['total_return']:.2%}  |  "
                f"[bold]Sharpe:[/bold] {metrics['sharpe_ratio']:.2f}  |  "
                f"[bold]Max DD:[/bold] {metrics['max_drawdown']:.2%}  |  "
                f"[bold]Win Rate:[/bold] {metrics['win_rate']:.0%}  |  "
                f"[bold]Trades:[/bold] {metrics['total_trades']}",
                title="Performance",
            )
        )

    conn.close()


# --- trades - pod-aware ----------------------------------------------------


@app.command()
def trades(
    limit: int = typer.Option(
        20,
        "--limit",
        "-l",
        help="Number of recent trades to show",
    ),
    pod: str = typer.Option("default", "--pod", "-p", help="Pod to operate on"),
):
    """Show recent trades with LLM reasoning."""
    _setup_logging("WARNING")
    config = _get_config_for_pod(pod)
    db_path = _get_db_path(config)

    from llm_quant.db.schema import get_connection
    from llm_quant.trading.ledger import get_recent_trades

    conn = get_connection(db_path)
    recent = get_recent_trades(conn, limit, pod_id=pod)
    conn.close()

    if not recent:
        console.print("[dim]No trades recorded yet.[/dim]")
        return

    title = (
        f"Recent Trades (last {limit}, pod: {pod})"
        if pod != "default"
        else f"Recent Trades (last {limit})"
    )
    table = Table(title=title)
    table.add_column("ID", style="dim")
    table.add_column("Date")
    table.add_column("Symbol", style="bold")
    table.add_column("Action")
    table.add_column("Shares", justify="right")
    table.add_column("Price", justify="right")
    table.add_column("Notional", justify="right")
    table.add_column("Conviction")
    table.add_column("Reasoning", max_width=60)

    action_colors = {"buy": "green", "sell": "red", "close": "red", "hold": "yellow"}
    for t in recent:
        a_color = action_colors.get(t.get("action", "").lower(), "white")
        shares = float(t.get("shares", 0) or 0.0)
        share_fmt = f"{shares:.6f}" if abs(shares - round(shares)) > 1e-8 else f"{shares:.0f}"
        table.add_row(
            str(t.get("trade_id", "")),
            str(t.get("date", "")),
            t.get("symbol", ""),
            f"[{a_color}]{t.get('action', '').upper()}[/{a_color}]",
            share_fmt,
            f"${t.get('price', 0):.2f}",
            f"${t.get('notional', 0):,.2f}",
            t.get("conviction", "-"),
            (t.get("reasoning", "") or "-")[:60],
        )

    console.print(table)


# --- verify - pod-aware ----------------------------------------------------


@app.command()
def verify(
    pod: str = typer.Option("default", "--pod", "-p", help="Pod to operate on"),
):
    """Verify the tamper-evident hash chain on the trade ledger."""
    _setup_logging("WARNING")
    config = _get_config_for_pod(pod)
    db_path = _get_db_path(config)

    from llm_quant.db.integrity import verify_chain
    from llm_quant.db.schema import get_connection

    conn = get_connection(db_path)
    ok, _last_id, message = verify_chain(conn)
    conn.close()

    if ok:
        console.print(f"[green]PASS[/green] {message}")
    else:
        console.print(f"[red]FAIL[/red] {message}")
        raise typer.Exit(1)


# --- report ----------------------------------------------------------------


@app.command()
def report(
    report_type: str = typer.Argument(
        "daily", help="Report type: daily, weekly, or monthly"
    ),
    date: str = typer.Option(None, "--date", "-d", help="Report date (YYYY-MM-DD)"),
):
    """Generate a performance report."""
    import subprocess
    import sys

    cmd = [sys.executable, "scripts/generate_report.py", report_type]
    if date:
        cmd.extend(["--date", date])

    env = {**__import__("os").environ, "PYTHONPATH": "src"}
    result = subprocess.run(cmd, env=env, check=False)  # noqa: S603
    if result.returncode != 0:
        console.print(
            f"[red]FAIL[/red] Report generation exited with code {result.returncode}"
        )
        raise typer.Exit(result.returncode)
    console.print(f"[green]OK[/green] {report_type.capitalize()} report generated")


# ---------------------------------------------------------------------------
# pods sub-commands
# ---------------------------------------------------------------------------


_POD_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")


@pods_app.command("list")
def pods_list():
    """List all registered pods."""
    _setup_logging("WARNING")
    config = _get_config()
    db_path = _get_db_path(config)

    from llm_quant.db.schema import get_connection

    conn = get_connection(db_path)

    try:
        rows = conn.execute(
            "SELECT pod_id, display_name, strategy_type, initial_capital, "
            "status, created_at FROM pods ORDER BY pod_id"
        ).fetchall()
    except (duckdb.CatalogException, duckdb.BinderException):
        console.print(
            "[yellow]Pods table not found.[/yellow] "
            "Run [bold]pq init[/bold] to create it."
        )
        conn.close()
        return

    conn.close()

    if not rows:
        console.print(
            "[dim]No pods registered. Use [bold]pq pods create[/bold] to add one.[/dim]"
        )
        return

    table = Table(title="Trading Pods")
    table.add_column("Pod ID", style="bold")
    table.add_column("Display Name")
    table.add_column("Strategy")
    table.add_column("Initial Capital", justify="right")
    table.add_column("Status")
    table.add_column("Created")

    status_colors = {"active": "green", "paused": "yellow", "retired": "red"}
    for (
        pod_id,
        display_name,
        strategy_type,
        initial_capital,
        pod_status,
        created_at,
    ) in rows:
        s_color = status_colors.get(pod_status, "white")
        table.add_row(
            pod_id,
            display_name or pod_id,
            strategy_type or "-",
            f"${float(initial_capital):,.2f}" if initial_capital else "-",
            f"[{s_color}]{pod_status}[/{s_color}]",
            str(created_at)[:19] if created_at else "-",
        )

    console.print(table)


@pods_app.command("create")
def pods_create(
    pod_id: str = typer.Argument(..., help="Unique pod identifier (lowercase slug)"),
    name: str = typer.Option(None, "--name", "-n", help="Display name"),
    strategy: str = typer.Option("custom", "--strategy", "-s", help="Strategy type"),
    capital: float | None = typer.Option(
        None, "--capital", "-c", help="Initial capital override"
    ),
    capital_source: str = typer.Option(
        "alpaca_equity",
        "--capital-source",
        help="capital source: alpaca_equity | alpaca_cash | alpaca_buying_power | config",
    ),
):
    """Register a new trading pod."""
    _setup_logging("WARNING")

    # Validate pod_id format
    if not _POD_ID_PATTERN.match(pod_id):
        console.print(
            f"[red]FAIL[/red] Invalid pod_id '{pod_id}'. "
            "Must be a lowercase slug (letters, digits, hyphens, underscores), "
            "starting with a letter, max 63 chars."
        )
        raise typer.Exit(1)

    config = _get_config()
    db_path = _get_db_path(config)

    from llm_quant.db.schema import get_connection

    conn = get_connection(db_path)

    resolved_capital = _resolve_pod_capital(
        capital,
        capital_source,
        config,
    )

    try:
        conn.execute(
            "INSERT INTO pods "
            "(pod_id, display_name, strategy_type, "
            "initial_capital, status, created_at) "
            "VALUES (?, ?, ?, ?, 'active', NOW())",
            [pod_id, name or pod_id, strategy, resolved_capital],
        )
        console.print(
            f"[green]OK[/green] Pod [bold]{pod_id}[/bold] created "
            f"(strategy={strategy}, capital=${resolved_capital:,.2f})"
        )
    except duckdb.ConstraintException:
        console.print(f"[red]FAIL[/red] Pod '{pod_id}' already exists.")
        raise typer.Exit(1) from None
    except duckdb.Error as e:
        console.print(f"[red]FAIL[/red] Could not create pod: {e}")
        raise typer.Exit(1) from e
    finally:
        conn.close()


@pods_app.command("delete")
def pods_delete(
    pod_id: str = typer.Argument(..., help="Pod to remove"),
    force: bool = typer.Option(
        False, "--force", help="Hard delete (default: deactivate)"
    ),
):
    """Deactivate or delete a pod."""
    _setup_logging("WARNING")
    config = _get_config()
    db_path = _get_db_path(config)

    from llm_quant.db.schema import get_connection

    conn = get_connection(db_path)

    try:
        if force:
            conn.execute("DELETE FROM pods WHERE pod_id = ?", [pod_id])
            # DuckDB returns row count via changes
            console.print(
                f"[green]OK[/green] Pod [bold]{pod_id}[/bold] permanently deleted."
            )
        else:
            conn.execute(
                "UPDATE pods SET status = 'retired', "
                "retired_at = NOW() WHERE pod_id = ?",
                [pod_id],
            )
            console.print(
                f"[green]OK[/green] Pod [bold]{pod_id}[/bold] "
                "deactivated (status=retired)."
            )
    except duckdb.Error as e:
        console.print(f"[red]FAIL[/red] Could not delete/deactivate pod: {e}")
        raise typer.Exit(1) from e
    finally:
        conn.close()


@pods_app.command("sync-capital")
def pods_sync_capital(
    pod: str = typer.Option("default", "--pod", "-p", help="Pod to update"),
    all_pods: bool = typer.Option(False, "--all", "-a", help="Update all active pods"),
    source: str = typer.Option(
        "alpaca_equity",
        "--source",
        help="capital source: alpaca_equity | alpaca_cash | alpaca_buying_power | config",
    ),
):
    """Sync pod initial_capital from Alpaca account values."""
    _setup_logging("WARNING")
    config = _get_config()
    db_path = _get_db_path(config)

    from llm_quant.db.schema import get_connection

    conn = get_connection(db_path)
    try:
        if all_pods:
            rows = conn.execute(
                "SELECT pod_id FROM pods WHERE status = 'active' ORDER BY pod_id"
            ).fetchall()
            pods = [r[0] for r in rows]
        else:
            pods = [pod]

        if not pods:
            console.print("[yellow]No active pods found.[/yellow]")
            return

        if len(pods) > 1:
            console.print(
                "[yellow]WARN[/yellow] Updating multiple pods with the same Alpaca "
                "account value. Ensure this is intended to avoid over-allocation."
            )

        new_capital = _resolve_pod_capital(None, source, config)
        for pod_id in pods:
            conn.execute(
                "UPDATE pods SET initial_capital = ? WHERE pod_id = ?",
                [new_capital, pod_id],
            )
        conn.commit()
        console.print(
            f"[green]OK[/green] Synced capital ${new_capital:,.2f} "
            f"for {len(pods)} pod(s)."
        )
    finally:
        conn.close()


@pods_app.command("status")
def pods_status():
    """Show comparative dashboard across all pods."""
    _setup_logging("WARNING")
    config = _get_config()
    db_path = _get_db_path(config)

    from llm_quant.db.schema import get_connection

    conn = get_connection(db_path)
    _show_all_pods_dashboard(conn)


@crypto_app.command("status")
def crypto_status(
    pod: str = typer.Option("crypto", "--pod", "-p", help="Pod ID"),
    stale_minutes: int = typer.Option(
        15, "--stale-minutes", help="Warn if latest bar is older than this"
    ),
):
    """Show crypto pod status (last run, last trade, bar freshness, next run)."""
    _setup_logging("WARNING")
    config = _get_config_for_pod(pod)
    db_path = _get_db_path(config)

    import duckdb

    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except duckdb.Error as exc:
        console.print(f"[red]FAIL[/red] Could not open DB: {exc}")
        raise typer.Exit(1) from exc

    from llm_quant.trading.run_lock import slot_for_time

    try:
        last_run_row = conn.execute(
            """
            SELECT timestamp
            FROM intraday_context_snapshots
            WHERE pod_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            [pod],
        ).fetchone()
        last_run = last_run_row[0] if last_run_row else None

        last_trade_row = conn.execute(
            """
            SELECT trade_id, date, symbol, action, shares, price, exit_reason
            FROM trades
            WHERE pod_id = ?
            ORDER BY trade_id DESC
            LIMIT 1
            """,
            [pod],
        ).fetchone()

        from llm_quant.data.universe import get_tradeable_symbols

        symbols = get_tradeable_symbols(
            config, asset_class_filter=config.execution.asset_class_filter
        )
        latest_bar = None
        if symbols:
            placeholders = ", ".join(["?"] * len(symbols))
            latest_row = conn.execute(
                f"""
                SELECT MAX(timestamp)
                FROM market_data_intraday
                WHERE symbol IN ({placeholders})
                """,
                symbols,
            ).fetchone()
            latest_bar = latest_row[0] if latest_row else None

    finally:
        conn.close()

    now = datetime.now(tz=UTC)
    slot = slot_for_time(now, config.execution.intraday_timeframe_minutes)
    try:
        slot_dt = datetime.fromisoformat(slot)
    except ValueError:
        slot_dt = now
    next_run = slot_dt + timedelta(minutes=config.execution.intraday_timeframe_minutes)

    console.print(f"[bold]Crypto Pod:[/bold] {pod}")
    console.print(
        f"  [bold]Intraday Enabled:[/bold] {config.execution.intraday_enabled}"
        f" | [bold]RTH Guard:[/bold] {config.execution.intraday_rth_guard}"
    )

    def _as_utc(ts: Any) -> Any:
        if ts is None:
            return None
        if getattr(ts, "tzinfo", None) is None:
            return ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC)

    if last_run:
        last_run_utc = _as_utc(last_run)
        age = now - last_run_utc if last_run_utc else None
        console.print(f"  [bold]Last Run:[/bold] {last_run} (age {age})")
    else:
        console.print("  [bold]Last Run:[/bold] -")

    if latest_bar:
        latest_bar_utc = _as_utc(latest_bar)
        bar_age = now - latest_bar_utc if latest_bar_utc else None
        console.print(f"  [bold]Latest Bar:[/bold] {latest_bar} (age {bar_age})")
        if bar_age and bar_age > timedelta(minutes=stale_minutes):
            console.print(
                f"  [yellow]WARN[/yellow] Latest bar is stale (> {stale_minutes} min)."
            )
    else:
        console.print("  [bold]Latest Bar:[/bold] -")

    console.print(f"  [bold]Next Slot:[/bold] {next_run.isoformat()}")

    if last_trade_row:
        trade_id, date, symbol, action, shares, price, exit_reason = last_trade_row
        console.print(
            "  [bold]Last Trade:[/bold] "
            f"#{trade_id} {date} {symbol} {action} "
            f"{shares} @ {price} "
            f"{'(exit=' + exit_reason + ')' if exit_reason else ''}"
        )
    else:
        console.print("  [bold]Last Trade:[/bold] -")
    conn.close()


if __name__ == "__main__":
    app()
