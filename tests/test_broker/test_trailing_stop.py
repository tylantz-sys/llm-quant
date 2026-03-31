from llm_quant.broker.intraday_orders import compute_trailing_stop


def test_trailing_stop_updates_on_new_high():
    hwm, stop, should = compute_trailing_stop(100.0, 101.0, 0.015)
    assert should is True
    assert hwm == 101.0
    assert stop == 101.0 * (1.0 - 0.015)


def test_trailing_stop_no_update_without_new_high():
    hwm, stop, should = compute_trailing_stop(105.0, 104.0, 0.015)
    assert should is False
    assert hwm == 105.0
    assert stop == 105.0 * (1.0 - 0.015)
