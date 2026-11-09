"""CLI entry point for downloading and validating public spot candles."""

import argparse
from datetime import datetime, timezone

from .config import Settings
from .exchange import TIMEFRAME_MS, create_exchange, fetch_closed_ohlcv
from .quality import validate_ohlcv
from .storage import save_candles


def main() -> int:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(
        description=f"Download public {settings.exchange_id.upper()} Spot OHLCV candles"
    )
    parser.add_argument("--symbols", nargs="+", default=list(settings.symbols))
    parser.add_argument("--timeframe", choices=TIMEFRAME_MS, default=settings.default_timeframe)
    parser.add_argument("--days", type=int, default=settings.default_days)
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be >= 1")

    exchange = create_exchange(settings.exchange_id)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    since_ms = now_ms - args.days * 86_400_000
    exit_code = 0
    try:
        exchange.load_markets()
        for symbol in args.symbols:
            print(f"[{symbol}] downloading {args.timeframe} candles from {settings.exchange_id}...")
            rows = fetch_closed_ohlcv(exchange, symbol, args.timeframe, since_ms, now_ms)
            report = validate_ohlcv(rows, TIMEFRAME_MS[args.timeframe])
            for warning in report.warnings:
                print(f"WARNING: {warning}")
            if not report.is_valid:
                exit_code = 1
                for error in report.errors:
                    print(f"ERROR: {error}")
                continue
            saved = save_candles(
                settings.database_path, settings.exchange_id, symbol,
                args.timeframe, rows, now_ms,
            )
            print(f"[{symbol}] validated={len(rows)} saved={saved} db={settings.database_path}")
            if not rows:
                exit_code = 1
    finally:
        exchange.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
