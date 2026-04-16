from pathlib import Path

import yaml

spec_path = Path("data/strategies/xli-regime-starter-v4/research-spec.yaml")
spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
spec["frozen"] = True
spec["frozen_at"] = "2026-04-15"
spec.pop("frozen_hash", None)
hashable = {k: v for k, v in spec.items() if k != "frozen_hash"}
content = yaml.dump(hashable, default_flow_style=False, sort_keys=False)
import hashlib

spec["frozen_hash"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
spec_path.write_text(yaml.dump(spec, default_flow_style=False, sort_keys=False), encoding="utf-8")
print(spec["frozen_hash"])
