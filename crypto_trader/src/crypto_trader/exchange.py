"""Public OHLCV access through ccxt; no authentication or trading methods."""

from datetime import datetime, timezone

import ccxt


TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}
ALLOWED_SYMBOLS = {"BTC/USDT", "ETH/USDT"}
SUPPORTED_EXCHANGES = {"okx", "binance"}


def create_exchange(exchange_id: str = "okx"):
    exchange_id = exchange_id.strip().lower()
    if exchange_id not in SUPPORTED_EXCHANGES:
        raise ValueError(
            f"Unsupported exchange: {exchange_id}. "
            f"Supported exchanges: {', '.join(sorted(SUPPORTED_EXCHANGES))}"
        )
    exchange_class = getattr(ccxt, exchange_id)
    return exchange_class({"enableRateLimit": True, "options": {"defaultType": "spot"}})


def fetch_closed_ohlcv(
    exchange, symbol: str, timeframe: str, since_ms: int, now_ms: int | None = None
):
    if symbol not in ALLOWED_SYMBOLS:
        raise ValueError(f"Unsupported symbol: {symbol}")
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if since_ms < 0:
        raise ValueError("since_ms must be non-negative")

    current_ms = (
        now_ms
        if now_ms is not None
        else int(datetime.now(timezone.utc).timestamp() * 1000)
    )
    candle_ms = TIMEFRAME_MS[timeframe]
    cursor = since_ms
    by_timestamp = {}

    # Paginate so small timeframes can retrieve more than the exchange's
    # per-request limit of 1,000 candles.
    while cursor < current_ms:
        batch = exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, since=cursor, limit=1000
        )
        if not batch:
            break
        for row in batch:
            timestamp = int(row[0])
            if timestamp + candle_ms <= current_ms:
                by_timestamp[timestamp] = row
        last_timestamp = int(batch[-1][0])
        if last_timestamp < cursor or len(batch) < 1000:
            break
        next_cursor = last_timestamp + candle_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor

    return [by_timestamp[key] for key in sorted(by_timestamp)]
