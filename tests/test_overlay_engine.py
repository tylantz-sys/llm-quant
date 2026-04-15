from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from llm_quant.brain.overlay import OverlayEngine, OverlayUnavailableError


class _FakeConfig:
    def __init__(self) -> None:
        self.execution = SimpleNamespace(asset_class_filter=[])
        self.llm = SimpleNamespace(
            model="claude-3-5-sonnet-latest",
            max_tokens=256,
            temperature=0.0,
        )


def test_overlay_engine_requires_auth_before_client_init(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    config = _FakeConfig()
    engine = OverlayEngine(config=config, config_dir=Path("config"))

    init_called = False

    class _BoomAnthropic:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            nonlocal init_called
            init_called = True
            raise AssertionError("Anthropic client should not initialize without auth")

    monkeypatch.setattr("llm_quant.brain.overlay.anthropic.Anthropic", _BoomAnthropic)

    with pytest.raises(OverlayUnavailableError, match="overlay_auth_missing"):
        engine._call_api("test prompt")

    assert init_called is False
