from llm_quant.config import load_config
from llm_quant.data.universe import get_tradeable_symbols


def test_tradeable_symbols_asset_class_filter():
    config = load_config()
    symbols = get_tradeable_symbols(config, asset_class_filter=["crypto"])
    assert "BTC-USD" in symbols
    assert "ETH-USD" in symbols
    assert "SPY" not in symbols
