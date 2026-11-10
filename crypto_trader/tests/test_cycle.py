from datetime import datetime, timezone

from crypto_trader.config import Settings
from crypto_trader.cycle import run_cycle
from crypto_trader.paper import PaperBroker


HOUR = 3_600_000


class FakeExchange:
    def __init__(self, rows):
        self.rows = rows

    def load_markets(self):
        return {}

    def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=None):
        return [row for row in self.rows[symbol] if row[0] >= (since or 0)][:limit]

    def close(self):
        pass


def rising_rows(start_ms, count=120):
    rows = []
    price = 100.0
    for i in range(count):
        price *= 1.0015 if i % 4 != 3 else 0.997
        rows.append([start_ms + i * HOUR, price * 0.999, price * 1.002,
                     price * 0.998, price, 1000.0])
    return rows


def test_cycle_processes_each_closed_candle_only_once(tmp_path):
    count = 120
    now_ms = 2_000_000_000_000
    start_ms = now_ms - count * HOUR
    rows = {symbol: rising_rows(start_ms, count)
            for symbol in ("BTC/USDT", "ETH/USDT")}
    exchange = FakeExchange(rows)
    settings = Settings(database_path=tmp_path / "market.sqlite3",
                        symbols=("BTC/USDT", "ETH/USDT"))
    broker = PaperBroker(settings.database_path)

    first = run_cycle(exchange, settings, broker, "1h", 100, now_ms)
    assert len(first["results"]) == 2
    assert first["account"]["initial_balance"] == 100.0

    history_after_first = len(broker.trade_history())
    second = run_cycle(exchange, settings, broker, "1h", 100, now_ms)
    assert all(item["status"] == "no_new_candle" for item in second["results"])
    assert len(broker.trade_history()) == history_after_first
