from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_quant.backtest.artifacts import hash_content

spec_path = Path("data/strategies/xli-regime-starter-v1/research-spec.yaml")
spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
hashable = {k: v for k, v in spec.items() if k != "frozen_hash"}
content = yaml.dump(hashable, default_flow_style=False, sort_keys=False)
print(content)
print(hash_content(content))
