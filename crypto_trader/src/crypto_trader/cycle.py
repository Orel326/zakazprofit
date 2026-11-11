"""One-candle-at-a-time autonomous paper trading cycle.

This module uses public OHLCV data only. It never places real exchange orders.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .exchange import TIMEFRAME_MS, fetch_closed_ohlcv
from .paper import PaperBroker
from .quality import validate_ohlcv
from .storage import save_candles
from .team import analyze_symbol


STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_cycle_state (
    symbol TEXT PRIMARY KEY,
    last_candle_ms INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_recent(database_path, exchange, symbol, timeframe, limit):
    with sqlite3.connect(database_path) as db:
        rows = db.execute(
            """SELECT timestamp_ms, open, high, low, close, volume
               FROM candles
               WHERE exchange=? AND symbol=? AND timeframe=?
               ORDER BY timestamp_ms DESC LIMIT ?""",
            (exchange, symbol, timeframe, limit),
        ).fetchall()
    return list(reversed(rows))


def _last_processed(database_path, symbol):
    with sqlite3.connect(database_path) as db:
        db.execute(STATE_SCHEMA)
        row = db.execute(
            "SELECT last_candle_ms FROM paper_cycle_state WHERE symbol=?", (symbol,)
        ).fetchone()
    return int(row[0]) if row else None


def _save_processed(database_path, symbol, timestamp_ms):
    with sqlite3.connect(database_path) as db:
        db.execute(STATE_SCHEMA)
        db.execute(
            """INSERT INTO paper_cycle_state(symbol,last_candle_ms,updated_at)
               VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET
               last_candle_ms=excluded.last_candle_ms, updated_at=excluded.updated_at""",
            (symbol, int(timestamp_ms), _utc_now()),
        )


def run_cycle(exchange, settings: Settings, broker: PaperBroker,
              timeframe: str = "1h", candles: int = 200,
              now_ms: int | None = None) -> dict[str, Any]:
    """Fetch, validate and process each newly closed candle at most once."""
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if candles < 60:
        raise ValueError("candles must be >= 60")
    current_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    candle_ms = TIMEFRAME_MS[timeframe]
    since_ms = max(0, current_ms - (candles + 10) * candle_ms)
    results = []
    marks: dict[str, float] = {}

    exchange.load_markets()
    for symbol in settings.symbols:
        rows = fetch_closed_ohlcv(exchange, symbol, timeframe, since_ms, current_ms)
        if not rows:
            results.append({"symbol": symbol, "status": "no_closed_candles"})
            continue
        quality = validate_ohlcv(rows, candle_ms)
        if not quality.is_valid:
            results.append({"symbol": symbol, "status": "data_rejected",
                            "errors": list(quality.errors)})
            continue

        save_candles(settings.database_path, settings.exchange_id, symbol,
                     timeframe, rows, current_ms)
        latest = rows[-1]
        timestamp_ms = int(latest[0])
        marks[symbol] = float(latest[4])
        last_processed = _last_processed(settings.database_path, symbol)
        if last_processed is not None and timestamp_ms <= last_processed:
            results.append({"symbol": symbol, "status": "no_new_candle",
                            "last_candle_ms": timestamp_ms})
            continue

        existing = next((p for p in broker.open_positions()
                         if p["symbol"] == symbol), None)
        event = None
        if existing:
            opened_ms = int(datetime.fromisoformat(existing["opened_at"].replace("Z", "+00:00")).timestamp() * 1000)
            if timestamp_ms > opened_ms:
                event = broker.process_candle(
                    symbol, datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat(),
                    float(latest[2]), float(latest[3])
                )
            results.append({"symbol": symbol, "status": "position_managed",
                            "exit": event, "position_open": any(
                                p["symbol"] == symbol for p in broker.open_positions())})
        else:
            recent = _load_recent(settings.database_path, settings.exchange_id,
                                  symbol, timeframe, candles)
            if len(recent) < 60:
                results.append({"symbol": symbol, "status": "insufficient_history",
                                "candles": len(recent)})
            else:
                decision = analyze_symbol(symbol, recent)
                opened = None
                rejection = None
                if decision.decision == "WATCH" and decision.proposal:
                    proposal = decision.proposal
                    try:
                        opened = broker.open_position(
                            symbol, float(proposal["reference_price"]),
                            float(proposal["stop_loss"]), float(proposal["take_profit"]),
                            datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat()
                        )
                    except ValueError as exc:
                        rejection = str(exc)
                results.append({
                    "symbol": symbol, "status": "analyzed",
                    "decision": decision.decision, "reason": decision.reason,
                    "proposal": decision.proposal, "opened": opened,
                    "execution_rejected": rejection,
                    "reports": decision.reports,
                })
        _save_processed(settings.database_path, symbol, timestamp_ms)

    # A transient feed failure must not crash the cycle when a position is open.
    # Use the last stored close as a stale mark; never invent a price.
    equity_marks = dict(marks)
    stale_marks = []
    for position in broker.open_positions():
        symbol = position["symbol"]
        if symbol in equity_marks:
            continue
        with sqlite3.connect(settings.database_path) as db:
            row = db.execute(
                """SELECT close FROM candles
                   WHERE exchange=? AND symbol=? AND timeframe=?
                   ORDER BY timestamp_ms DESC LIMIT 1""",
                (settings.exchange_id, symbol, timeframe),
            ).fetchone()
        if row is not None and float(row[0]) > 0:
            equity_marks[symbol] = float(row[0])
            stale_marks.append(symbol)

    equity = None
    equity_error = None
    if equity_marks or broker.open_positions():
        try:
            equity = broker.record_equity(equity_marks)
        except ValueError as exc:
            equity_error = str(exc)

    return {"timestamp": _utc_now(), "timeframe": timeframe,
            "results": results, "account": broker.account(), "equity": equity,
            "stale_equity_marks": stale_marks, "equity_error": equity_error}


def run_forever(exchange, settings: Settings, broker: PaperBroker,
                timeframe: str = "1h", candles: int = 200,
                interval_seconds: int = 60) -> None:
    if interval_seconds < 10:
        raise ValueError("interval_seconds must be >= 10")
    print("Paper trader running. Press Ctrl+C to stop. No real orders are possible.")
    while True:
        try:
            result = run_cycle(exchange, settings, broker, timeframe, candles)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(json.dumps({"status": "cycle_error", "error": str(exc)},
                             ensure_ascii=False))
        time.sleep(interval_seconds)
