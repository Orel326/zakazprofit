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
        return [row for row in self.rows.get(symbol, [])
                if row[0] >= (since or 0)][:limit]

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
    assert all(item["status"] == "processed" for item in first["results"])
    assert first["account"]["initial_balance"] == 100.0

    history_after_first = len(broker.trade_history())
    second = run_cycle(exchange, settings, broker, "1h", 100, now_ms)
    assert all(item["status"] == "no_new_candle" for item in second["results"])
    assert len(broker.trade_history()) == history_after_first


def test_cycle_catches_up_every_missed_candle_and_closes_stop(tmp_path):
    now_ms = 2_000_000_000_000
    start_ms = now_ms - 120 * HOUR
    btc = rising_rows(start_ms, 120)
    settings = Settings(database_path=tmp_path / "market.sqlite3",
                        symbols=("BTC/USDT",))
    broker = PaperBroker(settings.database_path)
    # Seed a persistent open position and cycle state before the outage.
    opened_at = datetime.fromtimestamp((btc[-101][0]) / 1000,
                                       timezone.utc).isoformat()
    broker.open_position("BTC/USDT", 100, 95, 110, opened_at)
    from crypto_trader.cycle import _save_processed
    checkpoint = btc[-100][0]
    _save_processed(settings.database_path, "BTC/USDT", checkpoint)
    # The second missed candle crosses the stop, then price recovers.
    btc[-2][3] = 90.0
    btc[-2][1] = 99.0
    btc[-2][2] = 101.0
    btc[-2][4] = 100.0
    exchange = FakeExchange({"BTC/USDT": btc})

    # A 60-candle indicator window must not truncate a 99-candle outage backlog.
    result = run_cycle(exchange, settings, broker, "1h", 60, now_ms)
    item = result["results"][0]
    assert item["candles_processed"] >= 90
    assert broker.open_positions() == []
    close = broker.trade_history()[-1]
    assert close["side"] == "CLOSE"
    assert close["reason"] == "stop_loss"


def test_empty_feed_is_reported_without_crashing(tmp_path):
    now_ms = 2_000_000_000_000
    settings = Settings(database_path=tmp_path / "market.sqlite3",
                        symbols=("BTC/USDT",))
    result = run_cycle(FakeExchange({"BTC/USDT": []}), settings,
                       PaperBroker(settings.database_path), "1h", 100, now_ms)
    assert result["results"][0]["status"] == "no_closed_candles"


def test_position_and_cycle_state_survive_broker_restart(tmp_path):
    now_ms = 2_000_000_000_000
    start_ms = now_ms - 120 * HOUR
    rows = {"BTC/USDT": rising_rows(start_ms, 120)}
    settings = Settings(database_path=tmp_path / "market.sqlite3",
                        symbols=("BTC/USDT",))
    first_broker = PaperBroker(settings.database_path)
    first = run_cycle(FakeExchange(rows), settings, first_broker, "1h", 100, now_ms)
    trades_before = first_broker.trade_history()
    second_broker = PaperBroker(settings.database_path)
    second = run_cycle(FakeExchange(rows), settings, second_broker, "1h", 100, now_ms)
    assert second["results"][0]["status"] == "no_new_candle"
    assert second_broker.trade_history() == trades_before
    assert second_broker.account()["initial_balance"] == 100.0
