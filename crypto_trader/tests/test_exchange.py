from crypto_trader.exchange import fetch_closed_ohlcv


class FakeExchange:
    def __init__(self, rows, page_size=1000):
        self.rows = rows
        self.page_size = page_size
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, since, limit):
        self.calls.append(since)
        return [row for row in self.rows if row[0] >= since][:min(limit, self.page_size)]


def test_fetch_excludes_unclosed_candle():
    hour = 3_600_000
    rows = [[0, 100, 110, 90, 105, 1], [hour, 105, 115, 100, 112, 2]]
    result = fetch_closed_ohlcv(FakeExchange(rows), "BTC/USDT", "1h", 0, now_ms=hour + 10)
    assert [row[0] for row in result] == [0]


def test_fetch_rejects_unknown_symbol():
    try:
        fetch_closed_ohlcv(FakeExchange([]), "DOGE/USDT", "1h", 0, now_ms=10_000_000)
    except ValueError as exc:
        assert "Unsupported symbol" in str(exc)
    else:
        raise AssertionError("Expected unsupported symbol to be rejected")


def test_fetch_continues_after_short_exchange_page():
    hour = 3_600_000
    rows = [[i * hour, 100, 110, 90, 105, 1] for i in range(1200)]
    exchange = FakeExchange(rows, page_size=300)

    result = fetch_closed_ohlcv(
        exchange, "BTC/USDT", "1h", 0, now_ms=1200 * hour
    )

    assert len(result) == 1200
    assert len(exchange.calls) == 4
    assert result[0][0] == 0
    assert result[-1][0] == 1199 * hour
