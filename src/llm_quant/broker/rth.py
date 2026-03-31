"""Regular-trading-hours guard helpers."""


def should_run_intraday(is_open: bool) -> bool:
    """Return True if intraday run should proceed."""
    return bool(is_open)
