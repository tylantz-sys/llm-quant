from pathlib import Path

from src.llm_quant.backtest.artifacts import freeze_spec

print(freeze_spec(Path("data/strategies/xli-regime-starter-v1")))
