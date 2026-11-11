"""Run an isolated historical backtest using public exchange candles."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from .backtest import run_backtest
from .config import Settings
from .exchange import TIMEFRAME_MS, create_exchange, fetch_closed_ohlcv
from .paper import PaperConfig


def main() -> int:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Historical paper backtest; no real orders.")
    parser.add_argument("--timeframe", default=settings.default_timeframe, choices=tuple(TIMEFRAME_MS))
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--candles", type=int, default=0, help="optional maximum candle count")
    args = parser.parse_args()
    if args.days < 1 or args.candles < 0:
        parser.error("--days must be >= 1 and --candles must be >= 0")
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    since = max(0, now - args.days * 86_400_000)
    exchange = create_exchange(settings.exchange_id)
    try:
        exchange.load_markets()
        output = {}
        for symbol in settings.symbols:
            candles = fetch_closed_ohlcv(exchange, symbol, args.timeframe, since, now)
            if args.candles:
                candles = candles[-args.candles:]
            try:
                output[symbol] = run_backtest(symbol, candles, TIMEFRAME_MS[args.timeframe], PaperConfig())
            except ValueError as exc:
                output[symbol] = {"status": "rejected", "reason": str(exc),
                                  "candles_received": len(candles)}
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    finally:
        exchange.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
