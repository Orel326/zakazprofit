"""Run isolated fixed-parameter strategy comparison; public market data only."""
import argparse
import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone

from .config import Settings
from .exchange import TIMEFRAME_MS, create_exchange, fetch_closed_ohlcv
from .paper import PaperConfig
from .research_backtest import evaluate, STRATEGIES

def main():
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Research-only Donchian/EMA strategy comparison")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--timeframe", choices=tuple(TIMEFRAME_MS), default="1h")
    parser.add_argument("--folds", type=int, default=4)
    args = parser.parse_args()
    if args.days < 1 or args.folds < 2:
        parser.error("days >= 1 and folds >= 2 required")
    now = int(datetime.now(timezone.utc).timestamp()*1000)
    since = now-args.days*86400000
    exchange = create_exchange(settings.exchange_id)
    report = {
        "_metadata": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "git_commit": os.getenv("GITHUB_SHA"),
            "exchange_id": settings.exchange_id,
            "timeframe": args.timeframe, "requested_days": args.days,
            "requested_since_ms": since, "requested_until_ms": now,
            "risk_and_costs": asdict(PaperConfig()),
            "strategies": list(STRATEGIES),
            "method": "Fixed parameters, no optimization. Disjoint test folds with historical warmup; not optimized walk-forward.",
            "caution": "Paper simulation; signals on closed candles, next-open entry, same-candle stop/target with adverse stop priority; no real orders. Historical periods are exploratory, not untouched out-of-sample.",
        }
    }
    try:
        exchange.load_markets()
        for symbol in settings.symbols:
            candles = fetch_closed_ohlcv(exchange, symbol, args.timeframe, since, now)
            canonical = json.dumps([[int(r[0]), *[float(x) for x in r[1:6]]] for r in candles],
                                   separators=(",",":"), allow_nan=False).encode()
            result = {"dataset_sha256": hashlib.sha256(canonical).hexdigest(),
                      "candles": len(candles), "strategies": {}}
            for name in STRATEGIES:
                try:
                    result["strategies"][name] = evaluate(symbol, candles, TIMEFRAME_MS[args.timeframe],
                                                          name, folds=args.folds)
                except ValueError as exc:
                    result["strategies"][name] = {"status":"rejected","reason":str(exc)}
            report[symbol] = result
    finally:
        exchange.close()
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
