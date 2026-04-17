"""Alpaca broker health-check for paper trading.

Verifies:
  - Credentials are set and the Alpaca paper API is reachable
  - Account is ACTIVE and not blocked
  - Crypto trading is enabled (crypto_status = ACTIVE)
  - Market clock is accessible
  - Open positions and recent orders are retrieved

Usage:
    cd /home/ty/Documents/llm-quant/llm-quant && PYTHONPATH=src python scripts/check_alpaca.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_quant.broker.alpaca import AlpacaClient, AlpacaError
from llm_quant.broker.executor import _map_crypto_symbol
from llm_quant.utils.env import load_dotenv_if_present

load_dotenv_if_present()

CRYPTO_SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "ADA-USD", "DOGE-USD"]


def main() -> None:
    result: dict = {}
    issues: list[str] = []

    # --- Connectivity ---
    try:
        client = AlpacaClient.from_env()
        result["connection"] = "ok"
    except AlpacaError as exc:
        result["connection"] = f"FAILED: {exc}"
        issues.append(str(exc))
        print(json.dumps({"status": "FAIL", "issues": issues, "detail": result}, indent=2))
        sys.exit(1)

    # --- Account ---
    account = client.get_account()
    acct_status = account.get("status")
    trading_blocked = account.get("trading_blocked", False)
    crypto_status = account.get("crypto_status", "UNKNOWN")

    result["account"] = {
        "status": acct_status,
        "trading_blocked": trading_blocked,
        "crypto_status": crypto_status,
        "shorting_enabled": account.get("shorting_enabled"),
        "equity": account.get("equity"),
        "cash": account.get("cash"),
        "buying_power": account.get("buying_power"),
    }

    if acct_status != "ACTIVE":
        issues.append(f"Account status is {acct_status!r} (expected ACTIVE)")
    if trading_blocked:
        issues.append("Trading is blocked on this account")
    if crypto_status != "ACTIVE":
        issues.append(f"Crypto trading status is {crypto_status!r} (expected ACTIVE)")

    # --- Clock ---
    clock = client.get_clock()
    result["clock"] = {
        "is_open": clock.get("is_open"),
        "next_open": clock.get("next_open", "")[:19],
        "next_close": clock.get("next_close", "")[:19],
    }

    # --- Symbol mapping validation ---
    symbol_map: dict[str, str] = {}
    for sym in CRYPTO_SYMBOLS:
        symbol_map[sym] = _map_crypto_symbol(sym, {})
    result["crypto_symbol_map"] = symbol_map

    expected = {s: s.replace("-", "/") for s in CRYPTO_SYMBOLS}
    for sym, mapped in symbol_map.items():
        if mapped != expected[sym]:
            issues.append(f"Unexpected crypto mapping: {sym} -> {mapped} (expected {expected[sym]})")

    # --- Positions ---
    positions = client.list_positions()
    result["open_positions"] = [
        {
            "symbol": p.get("symbol"),
            "qty": p.get("qty"),
            "side": p.get("side"),
            "unrealized_pl": p.get("unrealized_pl"),
            "avg_entry_price": p.get("avg_entry_price"),
        }
        for p in (positions or [])
    ]

    # --- Recent orders ---
    orders = client.list_orders(status="all") or []
    result["recent_orders"] = [
        {
            "symbol": o.get("symbol"),
            "side": o.get("side"),
            "qty": o.get("qty"),
            "status": o.get("status"),
            "submitted_at": (o.get("submitted_at") or "")[:19],
        }
        for o in orders[:10]
    ]

    # --- Overall verdict ---
    status = "OK" if not issues else "WARN"
    output = {
        "status": status,
        "issues": issues,
        "detail": result,
    }
    print(json.dumps(output, indent=2))
    if issues:
        sys.exit(1)


if __name__ == "__main__":
    main()
