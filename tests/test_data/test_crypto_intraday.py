from llm_quant.data.alpaca_intraday import normalize_crypto_symbols


def test_normalize_crypto_symbols_default():
    symbols = ["BTC-USD", "ETH-USD"]
    normalized, reverse = normalize_crypto_symbols(symbols)
    assert normalized == ["BTC/USD", "ETH/USD"]
    assert reverse["BTC/USD"] == "BTC-USD"
    assert reverse["ETH/USD"] == "ETH-USD"


def test_normalize_crypto_symbols_override():
    symbols = ["BTC-USD"]
    normalized, reverse = normalize_crypto_symbols(
        symbols, symbol_map={"BTC-USD": "BTC/USD"}
    )
    assert normalized == ["BTC/USD"]
    assert reverse["BTC/USD"] == "BTC-USD"
