"""Crypto basket equal-weight sizing.

When ``crypto_basket_equal_weight`` is enabled in the risk limits, all BUY
signals for crypto assets are clamped to ``crypto_basket_target_weight``
before execution.  This prevents the LLM from producing ad-hoc concentrations
(e.g. 7 % XRP / 1.5 % SOL in the same session) and instead forces a flat
allocation across basket constituents regardless of expressed conviction.

Sell / close signals are intentionally left unchanged — the LLM and exit
engine must remain free to reduce positions to any size.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from llm_quant.brain.models import Action

if TYPE_CHECKING:
    from llm_quant.brain.models import TradeSignal
    from llm_quant.config import RiskLimits

logger = logging.getLogger(__name__)


def normalize_crypto_basket_weights(
    signals: list[TradeSignal],
    limits: RiskLimits,
    asset_class_map: dict[str, str],
) -> list[TradeSignal]:
    """Clamp BUY crypto signal ``target_weight`` to the basket equal-weight target.

    Parameters
    ----------
    signals:
        Signals already approved by the risk manager.
    limits:
        Active risk limits for the current pod/track.
    asset_class_map:
        Symbol → asset class mapping from the universe config.

    Returns
    -------
    list[TradeSignal]
        Signals with crypto BUY ``target_weight`` clamped where necessary.
        The list is the same object as *signals*; affected signals are mutated
        in-place (``TradeSignal`` is a plain mutable dataclass).
    """
    if not getattr(limits, "crypto_basket_equal_weight", False):
        return signals

    target = float(getattr(limits, "crypto_basket_target_weight", 0.03))

    for sig in signals:
        if sig.action != Action.BUY:
            continue
        asset_class = asset_class_map.get(sig.symbol, "equity").lower()
        if asset_class != "crypto":
            continue
        if sig.target_weight > target:
            logger.info(
                "crypto basket sizing: clamping %s target_weight %.4f → %.4f",
                sig.symbol,
                sig.target_weight,
                target,
            )
            sig.target_weight = target

    return signals
