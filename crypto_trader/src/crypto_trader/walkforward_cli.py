"""Run rolling-origin out-of-sample validation using public exchange candles."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from .config import Settings
from .exchange import TIMEFRAME_MS, create_exchange, fetch_closed_ohlcv
from .paper import PaperConfig
from .walkforward import run_walk_forward


def main() -> int:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(
        description="Walk-forward validation of the fixed strategy; no real orders."
    )
    parser.add_argument("--timeframe", default=settings.default_timeframe,
                        choices=tuple(TIMEFRAME_MS))
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--candles", type=int, default=0,
                        help="optional maximum candle count")
    parser.add_argument("--train-candles", type=int, default=500)
    parser.add_argument("--test-candles", type=int, default=168)
    parser.add_argument("--step-candles", type=int, default=0,
                        help="fold advance; defaults to --test-candles")
    args = parser.parse_args()
    if args.days < 1 or args.candles < 0:
        parser.error("--days must be >= 1 and --candles must be >= 0")
    if args.train_candles < 60 or args.test_candles < 1 or args.step_candles < 0:
        parser.error("train-candles must be >= 60, test-candles >= 1, step-candles >= 0")
    step = args.step_candles or args.test_candles
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
                output[symbol] = run_walk_forward(
                    symbol, candles, TIMEFRAME_MS[args.timeframe],
                    train_candles=args.train_candles,
                    test_candles=args.test_candles,
                    step_candles=step,
                    config=PaperConfig(),
                )
            except ValueError as exc:
                output[symbol] = {
                    "status": "rejected", "reason": str(exc),
                    "candles_received": len(candles),
                }
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    finally:
        exchange.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
