"""Claude overlay engine: adjust/approve strategy signals."""

from __future__ import annotations

import logging
from pathlib import Path

import anthropic

from llm_quant.brain.models import MarketContext, TradingDecision
from llm_quant.brain.parser import parse_trading_decision
from llm_quant.brain.prompts import load_overlay_system_prompt, render_overlay_prompt
from llm_quant.config import CONFIG_DIR, AppConfig

logger = logging.getLogger(__name__)

_COST_INPUT_PER_M: float = 3.0
_COST_OUTPUT_PER_M: float = 15.0


def _estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    input_cost = prompt_tokens / 1_000_000 * _COST_INPUT_PER_M
    output_cost = completion_tokens / 1_000_000 * _COST_OUTPUT_PER_M
    return round(input_cost + output_cost, 6)


class OverlayEngine:
    """Calls Claude to approve/scale candidate strategy signals."""

    def __init__(
        self,
        config: AppConfig,
        config_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._config_dir = config_dir or CONFIG_DIR
        self._client = anthropic.Anthropic()
        self._system_prompt: str = load_overlay_system_prompt(self._config_dir)

    def get_overlay_signals(
        self,
        context: MarketContext,
        candidate_signals: list[dict],
    ) -> TradingDecision:
        user_prompt = render_overlay_prompt(
            context, candidate_signals, self._config_dir
        )
        response = self._call_api(user_prompt)

        raw_text: str = response.content[0].text
        usage = response.usage

        prompt_tokens = usage.input_tokens
        completion_tokens = usage.output_tokens
        total_tokens = prompt_tokens + completion_tokens
        cost_usd = _estimate_cost(prompt_tokens, completion_tokens)

        decision = parse_trading_decision(raw_text, context.date)
        decision.model = self._config.llm.model
        decision.decision_type = "overlay"
        decision.prompt_tokens = prompt_tokens
        decision.completion_tokens = completion_tokens
        decision.total_tokens = total_tokens
        decision.cost_usd = cost_usd
        decision.raw_response = raw_text
        decision.system_prompt = self._system_prompt
        decision.user_prompt = user_prompt
        return decision

    def _call_api(self, user_prompt: str) -> anthropic.types.Message:
        return self._client.messages.create(
            model=self._config.llm.model,
            max_tokens=self._config.llm.max_tokens,
            temperature=self._config.llm.temperature,
            system=self._system_prompt,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
        )
