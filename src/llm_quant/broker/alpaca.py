"""Minimal Alpaca REST client used for live paper execution."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


class AlpacaError(RuntimeError):
    """Raised when Alpaca API calls fail."""


@dataclass
class AlpacaClient:
    base_url: str
    api_key: str
    api_secret: str
    timeout: int = 10

    @classmethod
    def from_env(cls) -> "AlpacaClient":
        api_key = os.environ.get("ALPACA_API_KEY")
        api_secret = os.environ.get("ALPACA_SECRET_KEY")
        base_url = os.environ.get("ALPACA_PAPER_URL", "https://paper-api.alpaca.markets")
        if not api_key or not api_secret:
            raise AlpacaError("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in env")
        return cls(base_url=base_url, api_key=api_key, api_secret=api_secret)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url.rstrip('/')}{path}"
        try:
            resp = requests.request(
                method,
                url,
                headers=self._headers,
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise AlpacaError(f"Alpaca request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise AlpacaError(f"Alpaca {method} {path} failed: {resp.text}")
        if resp.text:
            return resp.json()
        return None

    def get_clock(self) -> dict[str, Any]:
        return self._request("GET", "/v2/clock")

    def clock_timestamp_et(self) -> datetime:
        clock = self.get_clock()
        ts = clock.get("timestamp")
        if not ts:
            return datetime.now(tz=_ET)
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(_ET)

    def is_market_open(self) -> bool:
        clock = self.get_clock()
        return bool(clock.get("is_open"))

    def list_positions(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v2/positions")

    def cancel_all_orders(self) -> None:
        self._request("DELETE", "/v2/orders")

    def list_orders(
        self, status: str = "open", nested: bool = False
    ) -> list[dict[str, Any]]:
        query = f"/v2/orders?status={status}"
        if nested:
            query = f"{query}&nested=true"
        return self._request("GET", query)

    def get_order(self, order_id: str, nested: bool = False) -> dict[str, Any]:
        query = f"/v2/orders/{order_id}"
        if nested:
            query = f"{query}?nested=true"
        return self._request("GET", query)

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"/v2/orders/{order_id}")

    def replace_order(self, order_id: str, **params: Any) -> dict[str, Any]:
        return self._request("PATCH", f"/v2/orders/{order_id}", json=params)

    def submit_market_order(self, symbol: str, qty: float, side: str) -> dict[str, Any]:
        payload = {
            "symbol": symbol,
            "qty": str(int(qty)),
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
        return self._request("POST", "/v2/orders", json=payload)

    def submit_limit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        limit_price: float,
    ) -> dict[str, Any]:
        payload = {
            "symbol": symbol,
            "qty": str(int(qty)),
            "side": side,
            "type": "limit",
            "time_in_force": "day",
            "limit_price": f"{limit_price:.2f}",
        }
        return self._request("POST", "/v2/orders", json=payload)

    def submit_stop_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        stop_price: float,
    ) -> dict[str, Any]:
        payload = {
            "symbol": symbol,
            "qty": str(int(qty)),
            "side": side,
            "type": "stop",
            "time_in_force": "day",
            "stop_price": f"{stop_price:.2f}",
        }
        return self._request("POST", "/v2/orders", json=payload)

    def submit_oco_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        take_profit: float,
        stop_loss: float,
    ) -> dict[str, Any]:
        payload = {
            "symbol": symbol,
            "qty": str(int(qty)),
            "side": side,
            "type": "limit",
            "time_in_force": "day",
            "limit_price": f"{take_profit:.2f}",
            "stop_price": f"{stop_loss:.2f}",
            "order_class": "oco",
        }
        return self._request("POST", "/v2/orders", json=payload)

    def submit_bracket_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        take_profit: float,
        stop_loss: float,
    ) -> dict[str, Any]:
        payload = {
            "symbol": symbol,
            "qty": str(int(qty)),
            "side": side,
            "type": "market",
            "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": {"limit_price": f"{take_profit:.2f}"},
            "stop_loss": {"stop_price": f"{stop_loss:.2f}"},
        }
        return self._request("POST", "/v2/orders", json=payload)


__all__ = ["AlpacaClient", "AlpacaError"]
