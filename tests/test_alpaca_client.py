from __future__ import annotations

from typing import Any

from llm_quant.broker.alpaca import AlpacaClient


def test_from_env_normalizes_trailing_v2(monkeypatch: Any) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.setenv("ALPACA_PAPER_URL", "https://paper-api.alpaca.markets/v2")

    client = AlpacaClient.from_env()

    assert client.base_url == "https://paper-api.alpaca.markets"


def test_from_env_preserves_trading_host_without_suffix(monkeypatch: Any) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.setenv("ALPACA_PAPER_URL", "https://paper-api.alpaca.markets")

    client = AlpacaClient.from_env()

    assert client.base_url == "https://paper-api.alpaca.markets"


def test_request_builds_single_v2_path(monkeypatch: Any) -> None:
    client = AlpacaClient(
        base_url="https://paper-api.alpaca.markets",
        api_key="key",
        api_secret="secret",
    )
    captured: dict[str, Any] = {}

    class _Response:
        status_code = 200
        text = '{"ok": true}'

        @staticmethod
        def json() -> dict[str, bool]:
            return {"ok": True}

    def _fake_request(method: str, url: str, **kwargs: Any) -> _Response:
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _Response()

    monkeypatch.setattr("llm_quant.broker.alpaca.requests.request", _fake_request)

    payload = client.get_clock()

    assert payload == {"ok": True}
    assert captured["method"] == "GET"
    assert captured["url"] == "https://paper-api.alpaca.markets/v2/clock"
