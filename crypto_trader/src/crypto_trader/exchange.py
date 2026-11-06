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


def create_exchange(exchange_id: str = "binance"):
    if exchange_id != "binance":
        raise ValueError("Stage 1 supports Binance Spot public market data only")
    exchange_class = getattr(ccxt, exchange_id)
    return exchange_class({"enableRateLimit": True, "options": {"defaultType": "spot"}})


def fetch_closed_ohlcv(exchange, symbol: str, timeframe: str, since_ms: int, now_ms: int | None = None):
    if symbol not in ALLOWED_SYMBOLS:
        raise ValueError(f"Unsupported symbol: {symbol}")
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    rows = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=1000)
    current_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    candle_ms = TIMEFRAME_MS[timeframe]
    # Exclude the still-forming candle; its OHLCV values can change until close.
    return [row for row in rows if int(row[0]) + candle_ms <= current_ms]
