from llm_quant.broker.rth import should_run_intraday


def test_rth_guard():
    assert should_run_intraday(True) is True
    assert should_run_intraday(False) is False
