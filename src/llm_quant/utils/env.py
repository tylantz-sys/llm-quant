"""Environment helpers (e.g., load .env for CLI runs)."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv_if_present(path: Path | None = None) -> None:
    """Load .env into os.environ if present (non-destructive)."""
    env_override = os.environ.get("LLM_QUANT_ENV_FILE")
    if path is None and env_override:
        path = Path(env_override)
    if path is None:
        path = Path.cwd() / ".env"

    if not path.exists():
        return

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
