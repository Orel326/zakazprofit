"""Run the structured analysis team against the latest stored closed candles."""
from __future__ import annotations

import argparse
import json
import sqlite3

from .config import Settings
from .team import analyze_symbol


def load_recent(database_path, exchange, symbol, timeframe, limit):
    with sqlite3.connect(database_path) as db:
        rows = db.execute(
            """SELECT timestamp_ms, open, high, low, close, volume
               FROM candles
               WHERE exchange=? AND symbol=? AND timeframe=?
               ORDER BY timestamp_ms DESC LIMIT ?""",
            (exchange, symbol, timeframe, limit),
        ).fetchall()
    return list(reversed(rows))


def main() -> int:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Run structured paper-trading analysis agents")
    parser.add_argument("--symbols", nargs="+", default=list(settings.symbols))
    parser.add_argument("--timeframe", default=settings.default_timeframe)
    parser.add_argument("--candles", type=int, default=200)
    args = parser.parse_args()
    if args.candles < 60:
        parser.error("--candles must be >= 60")
    for symbol in args.symbols:
        rows = load_recent(settings.database_path, settings.exchange_id, symbol, args.timeframe, args.candles)
        if len(rows) < 60:
            print(json.dumps({"symbol": symbol, "decision": "NO_TRADE",
                              "reason": f"Only {len(rows)} stored candles; need at least 60."},
                             ensure_ascii=False, indent=2))
            continue
        result = analyze_symbol(symbol, rows)
        print(json.dumps({
            "symbol": symbol, "decision": result.decision, "reason": result.reason,
            "snapshot": result.snapshot, "proposal": result.proposal,
            "risk_approved": result.risk_approved, "reports": result.reports,
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
