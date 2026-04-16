#!/usr/bin/env python3
"""Run the stock-bounded-paper pod only on actual U.S. equity trading days."""

from __future__ import annotations

import os
import subprocess
import sys

import requests


_raw_url = os.environ["ALPACA_PAPER_URL"].rstrip("/")
ALPACA_PAPER_BASE_URL = _raw_url[:-3] if _raw_url.endswith("/v2") else _raw_url
ALPACA_API_KEY = os.environ["ALPACA_API_KEY"]
ALPACA_SECRET_KEY = os.environ["ALPACA_SECRET_KEY"]

CLOCK_URL = f"{ALPACA_PAPER_BASE_URL}/v2/clock"
RUN_CMD = [
    "/home/ty/Documents/llm-quant/llm-quant/.venv/bin/pq",
    "run",
    "--pod",
    "stock-bounded-paper",
    "--broker",
    "paper",
]


def main() -> int:
    response = requests.get(
        CLOCK_URL,
        headers={
            "APCA-API-KEY-ID": ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
        },
        timeout=20,
    )
    response.raise_for_status()
    clock = response.json()

    if not clock.get("is_open", False):
        timestamp = clock.get("timestamp", "unknown")
        next_open = clock.get("next_open", "unknown")
        print(
            "Skipping stock-bounded-paper run: market closed "
            f"(timestamp={timestamp}, next_open={next_open})."
        )
        return 0

    print("Market open according to Alpaca clock; running stock-bounded-paper pod.")
    completed = subprocess.run(RUN_CMD, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
