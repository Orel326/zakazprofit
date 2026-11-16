"""Run isolated historical and walk-forward paper backtests using public candles."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone

from . import team
from .backtest import run_backtest, run_walk_forward
from .config import Settings
from .exchange import TIMEFRAME_MS, create_exchange, fetch_closed_ohlcv
from .paper import PaperConfig


def _run_rsi_variant(symbol, candles, timeframe_ms, config, folds, method):
    original = team.analyze_candles
    try:
        team.analyze_candles = lambda name, rows: original(
            name, rows, rsi_method=method
        )
        baseline = run_backtest(symbol, candles, timeframe_ms, config)
        walk_forward = run_walk_forward(
            symbol, candles, timeframe_ms, folds=folds, config=config
        )
    finally:
        team.analyze_candles = original
    return {
        "rsi_method": method,
        "metrics": {
            key: baseline[key] for key in (
                "initial_equity", "ending_equity", "net_pnl", "return_pct",
                "closed_trades", "win_rate_pct", "profit_factor",
                "expectancy_per_trade", "max_drawdown_pct",
            )
        },
        "buy_and_hold": baseline["buy_and_hold"],
        "trade_diagnostics": baseline["trade_diagnostics"],
        "entry_diagnostics": baseline["entry_diagnostics"],
        "walk_forward": walk_forward,
        "data": baseline["data"],
    }


def _comparison(simple, wilder):
    sm, wi = simple["metrics"], wilder["metrics"]
    return {
        "return_pct_delta_wilder_minus_simple": wi["return_pct"] - sm["return_pct"],
        "trade_count_delta_wilder_minus_simple": wi["closed_trades"] - sm["closed_trades"],
        "win_rate_delta_percentage_points": wi["win_rate_pct"] - sm["win_rate_pct"],
        "profit_factor_delta": (
            wi["profit_factor"] - sm["profit_factor"]
            if wi["profit_factor"] is not None and sm["profit_factor"] is not None
            else None
        ),
        "profitable_oos_folds": {
            "simple": simple["walk_forward"]["profitable_folds"],
            "wilder": wilder["walk_forward"]["profitable_folds"],
        },
        "oos_mean_return_pct": {
            "simple": simple["walk_forward"]["mean_fold_return_pct"],
            "wilder": wilder["walk_forward"]["mean_fold_return_pct"],
        },
    }


def main() -> int:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(
        description="Historical paper backtest; compares simple and Wilder RSI; no real orders."
    )
    parser.add_argument("--timeframe", default=settings.default_timeframe, choices=tuple(TIMEFRAME_MS))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--candles", type=int, default=0, help="optional maximum candle count")
    parser.add_argument("--walk-forward-folds", type=int, default=4)
    args = parser.parse_args()
    if args.days < 1 or args.candles < 0 or args.walk_forward_folds < 2:
        parser.error("--days must be >= 1, --candles >= 0 and --walk-forward-folds >= 2")
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    since = max(0, now - args.days * 86_400_000)
    exchange = create_exchange(settings.exchange_id)
    try:
        exchange.load_markets()
        output = {}
        dataset_fingerprints = {}
        for symbol in settings.symbols:
            candles = fetch_closed_ohlcv(exchange, symbol, args.timeframe, since, now)
            if args.candles:
                candles = candles[-args.candles:]
            canonical_data = json.dumps(
                [[int(row[0]), *[float(value) for value in row[1:6]]] for row in candles],
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
            dataset_fingerprints[symbol] = hashlib.sha256(canonical_data).hexdigest()
            try:
                config = PaperConfig()
                simple = _run_rsi_variant(
                    symbol, candles, TIMEFRAME_MS[args.timeframe], config,
                    args.walk_forward_folds, "simple",
                )
                wilder = _run_rsi_variant(
                    symbol, candles, TIMEFRAME_MS[args.timeframe], config,
                    args.walk_forward_folds, "wilder",
                )
                simple["data"]["sha256"] = dataset_fingerprints[symbol]
                wilder["data"]["sha256"] = dataset_fingerprints[symbol]
                output[symbol] = {
                    "simple_rsi": simple,
                    "wilder_rsi": wilder,
                    "comparison": _comparison(simple, wilder),
                }
            except ValueError as exc:
                output[symbol] = {
                    "status": "rejected", "reason": str(exc),
                    "candles_received": len(candles),
                }
        output["_metadata"] = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "exchange_id": settings.exchange_id,
            "timeframe": args.timeframe,
            "requested_days": args.days,
            "requested_since_ms": since,
            "requested_until_ms": now,
            "walk_forward_folds": args.walk_forward_folds,
            "strategy_version": "ema20_ema50_rsi_atr_long_spot_v1",
            "git_commit": os.getenv("GITHUB_SHA"),
            "execution_and_risk_config": asdict(PaperConfig()),
            "dataset_sha256": dataset_fingerprints,
            "notes": [
                "Public OHLCV only; no real orders.",
                "Dataset hash is SHA-256 of canonical timestamp/OHLCV rows per symbol.",
                "Walk-forward folds use fresh paper accounts and historical indicator warm-up only.",
            ],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    finally:
        exchange.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
