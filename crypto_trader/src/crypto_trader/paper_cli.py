"""Run the autonomous virtual trading cycle."""
from __future__ import annotations

import argparse

from .config import Settings
from .exchange import create_exchange
from .paper import PaperBroker
from .cycle import run_cycle, run_forever


def main() -> int:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(
        description="Autonomous spot paper trading. Never sends real orders."
    )
    parser.add_argument("--timeframe", default=settings.default_timeframe,
                        choices=("1m", "5m", "15m", "1h", "4h", "1d"))
    parser.add_argument("--candles", type=int, default=200)
    parser.add_argument("--once", action="store_true",
                        help="run one cycle and exit; default is continuous mode")
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args()
    if args.candles < 60:
        parser.error("--candles must be >= 60")
    if args.interval_seconds < 10:
        parser.error("--interval-seconds must be >= 10")

    broker = PaperBroker(settings.database_path)
    exchange = create_exchange(settings.exchange_id)
    try:
        if args.once:
            import json
            result = run_cycle(exchange, settings, broker, args.timeframe, args.candles)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            run_forever(exchange, settings, broker, args.timeframe,
                        args.candles, args.interval_seconds)
    except KeyboardInterrupt:
        print("\nPaper trader stopped.")
    finally:
        exchange.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
