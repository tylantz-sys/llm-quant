"""Regular-trading-hours guard helpers."""


def should_run_intraday(is_open: bool) -> bool:
    """Return True if intraday run should proceed."""
    return bool(is_open)


def should_skip_intraday(is_open: bool, guard_enabled: bool) -> bool:
    """Return True if intraday run should be skipped based on RTH guard."""
    return bool(guard_enabled) and not bool(is_open)
