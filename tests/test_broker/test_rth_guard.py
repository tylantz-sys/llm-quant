from llm_quant.broker.rth import should_run_intraday, should_skip_intraday


def test_rth_guard():
    assert should_run_intraday(True) is True
    assert should_run_intraday(False) is False


def test_rth_guard_skip_logic():
    assert should_skip_intraday(False, True) is True
    assert should_skip_intraday(True, True) is False
    assert should_skip_intraday(False, False) is False
