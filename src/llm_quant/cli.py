"""CLI entry point for llm-quant paper trading system."""

import dataclasses
import logging
import re
from datetime import UTC, datetime, time as dt_time, timedelta
from pathlib import Path
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
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("EOD time must be HH:MM")
    hour, minute = (int(p) for p in parts)
    return dt_time(hour=hour, minute=minute)


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
    from llm_quant.data.universe import get_tradeable_symbols
    from llm_quant.db.schema import get_connection

    symbols = get_tradeable_symbols(config)
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
) -> None:
    """Execute a full trading cycle for a single pod."""
    from llm_quant.brain.context import build_market_context
    from llm_quant.brain.engine import SignalEngine
    from llm_quant.brain.models import Action
    from llm_quant.brain.overlay import OverlayEngine
    from llm_quant.broker.alpaca import AlpacaClient, AlpacaError
    from llm_quant.broker.intraday_orders import (
        load_order_states,
        place_oco_exits_for_buys,
        reconcile_orders,
        update_trailing_stops,
        upsert_order_states,
    )
    from llm_quant.broker.rth import should_run_intraday
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
    from llm_quant.trading.intraday import (
        apply_reentry_cooldown,
        apply_scale_in,
        load_position_states,
        log_intraday_context,
        update_peak_prices,
        update_state_from_trades,
        upsert_position_states,
    )
    from llm_quant.trading.run_lock import acquire_run_lock, slot_for_time
    from llm_quant.strategies.runtime import (
        aggregate_strategy_signals,
        generate_strategy_signals,
        load_promoted_specs,
        required_symbols,
    )
    from llm_quant.trading.ledger import log_trades, save_portfolio_snapshot
    from llm_quant.trading.portfolio import Portfolio

    config = _get_config_for_pod(pod_id)
    db_path = _get_db_path(config)
    run_lock = None
    if config.execution.intraday_enabled:
        slot = slot_for_time(
            datetime.now(tz=UTC),
            config.execution.intraday_timeframe_minutes,
        )
        run_lock = acquire_run_lock(pod_id, slot)
        if run_lock is None:
            console.print(
                f"[yellow]Intraday slot {slot} already executed — skipping.[/yellow]"
            )
            return

    conn = get_connection(db_path)
    today = datetime.now(tz=UTC).date()

    if config.execution.intraday_enabled:
        try:
            rth_client = AlpacaClient.from_env()
            if not should_run_intraday(rth_client.is_market_open()):
                console.print(
                    "[yellow]RTH closed — skipped intraday run.[/yellow]"
                )
                conn.close()
                if run_lock:
                    run_lock.release()
                return
        except AlpacaError as exc:
            console.print(f"[red]FAIL[/red] Alpaca clock check failed: {exc}")
            conn.close()
            if run_lock:
                run_lock.release()
            raise typer.Exit(1) from exc

    # Step 1: Fetch latest data
    symbols = get_tradeable_symbols(config)
    console.print(
        f"\n[bold]Step 1/5:[/bold] Fetching market data for {len(symbols)} symbols..."
    )
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

    if config.execution.intraday_enabled:
        console.print("  [bold]Intraday:[/bold] Fetching 5-min bars from Alpaca...")
        try:
            intraday_df = fetch_intraday_ohlcv(
                symbols,
                timeframe_minutes=config.execution.intraday_timeframe_minutes,
                lookback_days=config.execution.intraday_lookback_days,
                timeout=config.data.fetch_timeout,
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

    # Step 2: Load portfolio
    console.print("[bold]Step 2/5:[/bold] Loading portfolio...")
    portfolio = Portfolio.from_db(conn, config.general.initial_capital, pod_id=pod_id)

    # Get latest prices for portfolio
    if config.execution.intraday_enabled:
        latest = conn.execute(
            """
            SELECT symbol, close as price FROM market_data_intraday
            WHERE (symbol, timestamp) IN (
                SELECT symbol, MAX(timestamp) FROM market_data_intraday GROUP BY symbol
            )
            """
        ).pl()
    else:
        latest = conn.execute(
            """
            SELECT symbol, close as price FROM market_data_daily
            WHERE (symbol, date) IN (
                SELECT symbol, MAX(date) FROM market_data_daily GROUP BY symbol
            )
            """
        ).pl()
    prices = dict(
        zip(
            latest["symbol"].to_list(),
            latest["price"].to_list(),
            strict=True,
        )
    )
    portfolio.update_prices(prices)

    console.print(
        f"  NAV: ${portfolio.nav:,.2f} | Cash: ${portfolio.cash:,.2f}"
        f" | Positions: {len(portfolio.positions)}"
    )

    # Step 3: Build context and get Claude's signals
    console.print("[bold]Step 3/5:[/bold] Generating signals...")
    portfolio_state = portfolio.to_snapshot_dict()
    context = build_market_context(conn, portfolio_state, config)

    if config.execution.claude_overlay_only:
        specs = load_promoted_specs()
        strategy_symbols = required_symbols(specs)
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
        aggregated = aggregate_strategy_signals(
            strategy_signals, max_position_weight=config.risk.max_position_weight
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

        overlay_engine = OverlayEngine(config)
        decision = overlay_engine.get_overlay_signals(context, candidate_signals)
        allowed = {sig.symbol for sig in aggregated}
        decision.signals = [s for s in decision.signals if s.symbol in allowed]
        decision_logger = SignalEngine(config)
    else:
        console.print("[bold]Step 3/5:[/bold] Consulting Claude...")
        engine = SignalEngine(config)
        decision = engine.get_signals(context)
        decision_logger = engine

    decision.pod_id = pod_id
    if not getattr(decision, "decision_type", None):
        decision.decision_type = "llm"

    # Display decision
    _display_decision(decision)

    if dry_run:
        console.print("\n[yellow]DRY RUN[/yellow] -- no trades executed.")
        conn.close()
        if run_lock:
            run_lock.release()
        return

    # Step 4: Risk check and execute
    console.print("[bold]Step 4/5:[/bold] Risk check and execution...")
    risk_mgr = RiskManager(config)

    signals = decision.signals
    now_ts = None
    if config.execution.intraday_enabled:
        now_row = conn.execute(
            "SELECT MAX(timestamp) FROM market_data_intraday"
        ).fetchone()
        now_ts = now_row[0] if now_row and now_row[0] else datetime.now(tz=UTC)

        states = load_position_states(conn, pod_id)
        update_peak_prices(portfolio, prices, states)

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

        signals = other_signals + entry_signals

        # Log intraday context snapshot for audit
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

    alpaca_client = None
    if broker.lower() == "alpaca":
        try:
            alpaca_client = AlpacaClient.from_env()
        except AlpacaError as exc:
            console.print(f"[red]FAIL[/red] Alpaca client init failed: {exc}")
            conn.close()
            raise typer.Exit(1) from exc

    if approved:
        executed = execute_signals(portfolio, approved, prices, portfolio.nav)
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

        if alpaca_client and executed:
            from llm_quant.broker.executor import submit_alpaca_orders

            stop_losses = {sig.symbol: sig.stop_loss for sig in approved}
            try:
                submit_alpaca_orders(
                    alpaca_client,
                    executed,
                    stop_losses,
                    config.risk,
                    use_brackets=not config.execution.intraday_enabled,
                )
            except AlpacaError as exc:
                console.print(f"[red]FAIL[/red] Alpaca execution failed: {exc}")
                conn.close()
                raise typer.Exit(1) from exc

        # Log decision
        decision_id = decision_logger.log_decision(conn, decision)

        # Log trades
        trade_ids = log_trades(conn, executed, today, decision_id, pod_id=pod_id)
        console.print(f"  [green]OK[/green] Logged trade IDs: {trade_ids}")
    else:
        console.print("  No trades to execute.")

    if config.execution.intraday_enabled and alpaca_client:
        order_states = load_order_states(conn, pod_id)
        try:
            positions = {
                p.get("symbol", ""): float(p.get("qty", 0.0))
                for p in alpaca_client.list_positions()
            }
        except AlpacaError as exc:
            console.print(f"[yellow]WARN[/yellow] Alpaca positions failed: {exc}")
            positions = {}

        reconcile_orders(
            alpaca_client,
            order_states,
            positions,
            trailing_pct=config.execution.trailing_stop_pct,
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
                partial_tp_pct=config.execution.profit_take_partial_pct,
                partial_tp_size=config.execution.profit_take_partial_size,
                remainder_tp_mult=config.execution.profit_take_remainder_tp_mult,
                default_stop_loss_pct=config.risk.default_stop_loss_pct,
            )

        upsert_order_states(conn, pod_id, order_states)

    # Step 5: Save snapshot
    console.print("[bold]Step 5/5:[/bold] Saving portfolio snapshot...")
    snap_id = save_portfolio_snapshot(conn, portfolio, today, pod_id=pod_id)
    console.print(f"  [green]OK[/green] Snapshot #{snap_id} saved")

    conn.close()
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
            _run_single_pod(pid, dry_run=dry_run, broker=broker)
        return

    _run_single_pod(pod, dry_run=dry_run, broker=broker)


@app.command()
def eod_flat(
    pod: str = typer.Option("default", "--pod", "-p", help="Pod to operate on"),
):
    """Flatten all positions at the configured end-of-day time."""
    _setup_logging()

    from llm_quant.broker.alpaca import AlpacaClient, AlpacaError
    from llm_quant.brain.models import Action, Conviction, TradeSignal
    from llm_quant.db.schema import get_connection
    from llm_quant.trading.executor import execute_signals
    from llm_quant.trading.ledger import log_trades, save_portfolio_snapshot
    from llm_quant.trading.portfolio import Portfolio

    config = _get_config_for_pod(pod)
    limits = config.risk

    if not getattr(limits, "eod_flatten_enabled", False):
        console.print("[yellow]EOD flatten disabled in config.[/yellow]")
        return

    try:
        target_time = _parse_eod_time(getattr(limits, "eod_flatten_time", "15:55"))
    except ValueError as exc:
        console.print(f"[red]FAIL[/red] Invalid eod_flatten_time: {exc}")
        raise typer.Exit(1) from exc

    try:
        client = AlpacaClient.from_env()
        now_et = client.clock_timestamp_et()
        if not client.is_market_open():
            console.print("[yellow]Market closed — skipping EOD flatten.[/yellow]")
            return
    except AlpacaError as exc:
        console.print(f"[red]FAIL[/red] Alpaca clock check failed: {exc}")
        raise typer.Exit(1) from exc

    if now_et.time() < target_time:
        console.print(
            f"[yellow]EOD flatten scheduled for {target_time} ET; "
            f"current time {now_et.time().strftime('%H:%M')} ET.[/yellow]"
        )
        return

    try:
        client.cancel_all_orders()
        positions = client.list_positions()
    except AlpacaError as exc:
        console.print(f"[red]FAIL[/red] Alpaca order/position fetch failed: {exc}")
        raise typer.Exit(1) from exc

    if not positions:
        console.print("[green]No open positions to flatten.[/green]")
        return

    for pos in positions:
        qty = float(pos.get("qty", 0.0))
        if qty == 0:
            continue
        side = "sell" if qty > 0 else "buy"
        try:
            client.submit_market_order(
                symbol=pos.get("symbol", ""),
                qty=abs(qty),
                side=side,
            )
        except AlpacaError as exc:
            console.print(f"[red]FAIL[/red] Order submit failed: {exc}")
            raise typer.Exit(1) from exc

    # Log EOD flatten in DuckDB (best-effort; relies on DB matching Alpaca)
    db_path = _get_db_path(config)
    conn = get_connection(db_path)
    portfolio = Portfolio.from_db(conn, config.general.initial_capital, pod_id=pod)

    prices = {
        pos.get("symbol", ""): float(pos.get("current_price", 0.0))
        for pos in positions
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

    executed = execute_signals(portfolio, close_signals, prices, portfolio.nav)
    today = now_et.date()
    if executed:
        log_trades(conn, executed, today, decision_id=None, pod_id=pod)
    save_portfolio_snapshot(conn, portfolio, today, pod_id=pod)
    conn.close()

    console.print(
        f"[green]EOD flatten complete.[/green] Orders submitted: {len(positions)}"
    )


def _display_decision(decision):
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
            table.add_row(
                sym,
                f"{pos.shares:.0f}",
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
        table.add_row(
            str(t.get("trade_id", "")),
            str(t.get("date", "")),
            t.get("symbol", ""),
            f"[{a_color}]{t.get('action', '').upper()}[/{a_color}]",
            f"{t.get('shares', 0):.0f}",
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
    capital: float = typer.Option(100_000.0, "--capital", "-c", help="Initial capital"),
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

    try:
        conn.execute(
            "INSERT INTO pods "
            "(pod_id, display_name, strategy_type, "
            "initial_capital, status, created_at) "
            "VALUES (?, ?, ?, ?, 'active', NOW())",
            [pod_id, name or pod_id, strategy, capital],
        )
        console.print(
            f"[green]OK[/green] Pod [bold]{pod_id}[/bold] created "
            f"(strategy={strategy}, capital=${capital:,.2f})"
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


@pods_app.command("status")
def pods_status():
    """Show comparative dashboard across all pods."""
    _setup_logging("WARNING")
    config = _get_config()
    db_path = _get_db_path(config)

    from llm_quant.db.schema import get_connection

    conn = get_connection(db_path)
    _show_all_pods_dashboard(conn)
    conn.close()


if __name__ == "__main__":
    app()
