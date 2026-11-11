"""Sequential, restart-safe autonomous paper trading cycle.

Uses public OHLCV only and never places real exchange orders.
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


def _load_recent(database_path, exchange, symbol, timeframe, limit, end_ms=None):
    query = """SELECT timestamp_ms, open, high, low, close, volume
               FROM candles
               WHERE exchange=? AND symbol=? AND timeframe=?"""
    params = [exchange, symbol, timeframe]
    if end_ms is not None:
        query += " AND timestamp_ms<=?"
        params.append(int(end_ms))
    query += " ORDER BY timestamp_ms DESC LIMIT ?"
    params.append(int(limit))
    with sqlite3.connect(database_path) as db:
        rows = db.execute(query, params).fetchall()
    return list(reversed(rows))


def _state_key(exchange, timeframe, symbol):
    return f"{exchange}|{timeframe}|{symbol}"


def _last_processed(database_path, symbol, exchange="okx", timeframe="1h"):
    key = _state_key(exchange, timeframe, symbol)
    with sqlite3.connect(database_path) as db:
        db.execute(STATE_SCHEMA)
        row = db.execute(
            "SELECT last_candle_ms FROM paper_cycle_state WHERE symbol=?", (key,)
        ).fetchone()
    return int(row[0]) if row else None


def _save_processed(database_path, symbol, timestamp_ms,
                    exchange="okx", timeframe="1h"):
    key = _state_key(exchange, timeframe, symbol)
    with sqlite3.connect(database_path) as db:
        db.execute(STATE_SCHEMA)
        db.execute(
            """INSERT INTO paper_cycle_state(symbol,last_candle_ms,updated_at)
               VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET
               last_candle_ms=excluded.last_candle_ms, updated_at=excluded.updated_at""",
            (key, int(timestamp_ms), _utc_now()),
        )


def _to_ms(timestamp: str) -> int:
    return int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)


def run_cycle(exchange, settings: Settings, broker: PaperBroker,
              timeframe: str = "1h", candles: int = 200,
              now_ms: int | None = None) -> dict[str, Any]:
    """Fetch, validate and process every newly closed candle in chronological order.

    On first startup with no open position, older candles are used only as
    indicator history; only the latest closed candle is eligible for a trade.
    """
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if candles < 60:
        raise ValueError("candles must be >= 60")
    current_ms = (now_ms if now_ms is not None
                  else int(datetime.now(timezone.utc).timestamp() * 1000))
    candle_ms = TIMEFRAME_MS[timeframe]
    default_since_ms = max(0, current_ms - (candles + 10) * candle_ms)
    results = []
    marks: dict[str, float] = {}

    exchange.load_markets()
    for symbol in settings.symbols:
        existing = next((p for p in broker.open_positions()
                         if p["symbol"] == symbol), None)
        last_processed = _last_processed(settings.database_path, symbol, settings.exchange_id, timeframe)
        since_ms = default_since_ms
        if last_processed is not None:
            # Do not silently skip candles after a long outage; catch up from
            # the exact persisted checkpoint, even beyond the indicator window.
            since_ms = min(since_ms, last_processed + 1)
        elif existing is not None:
            # Recover the full position lifetime if the cycle-state row is absent.
            since_ms = min(since_ms, max(0, _to_ms(existing["opened_at"]) - candle_ms))

        rows = fetch_closed_ohlcv(exchange, symbol, timeframe, since_ms, current_ms)
        if not rows:
            results.append({"symbol": symbol, "status": "no_closed_candles"})
            continue
        quality = validate_ohlcv(rows, candle_ms)
        if not quality.is_valid:
            results.append({"symbol": symbol, "status": "data_rejected",
                            "errors": list(quality.errors)})
            continue
        # Never advance the checkpoint across missing market data: a stop or
        # target could have been touched in an unavailable candle.
        internal_gaps = [warning for warning in quality.warnings
                         if warning.startswith("gap before row")]
        expected_first_ms = None
        if last_processed is not None:
            expected_first_ms = last_processed + candle_ms
        elif existing is not None:
            expected_first_ms = _to_ms(existing["opened_at"])
        first_gap = (expected_first_ms is not None
                     and int(rows[0][0]) > expected_first_ms)
        if internal_gaps or first_gap:
            results.append({
                "symbol": symbol, "status": "data_gap_detected",
                "warnings": internal_gaps,
                "expected_first_candle_ms": expected_first_ms,
                "actual_first_candle_ms": int(rows[0][0]),
            })
            continue

        save_candles(settings.database_path, settings.exchange_id, symbol,
                     timeframe, rows, current_ms)
        latest = rows[-1]
        marks[symbol] = float(latest[4])
        latest_ms = int(latest[0])

        if last_processed is None:
            if existing is None:
                # Never backtest historical candles as if they were live signals.
                pending = rows[-1:]
            else:
                opened_ms = _to_ms(existing["opened_at"])
                pending = [row for row in rows if int(row[0]) + candle_ms > opened_ms]
        else:
            pending = [row for row in rows if int(row[0]) > last_processed]

        if not pending:
            if last_processed is None:
                _save_processed(settings.database_path, symbol, latest_ms, settings.exchange_id, timeframe)
                results.append({"symbol": symbol, "status": "state_initialized",
                                "last_candle_ms": latest_ms})
            else:
                results.append({"symbol": symbol, "status": "no_new_candle",
                                "last_candle_ms": latest_ms})
            continue

        candle_events = []
        for candle in pending:
            ts = int(candle[0])
            # Execution is observed at candle close, not at its opening timestamp.
            timestamp = datetime.fromtimestamp(
                (ts + candle_ms) / 1000, timezone.utc
            ).isoformat()
            position = next((p for p in broker.open_positions()
                             if p["symbol"] == symbol), None)
            if position is not None:
                if _to_ms(position["opened_at"]) > ts:
                    # Entry is filled at this candle close. Its earlier OHLC
                    # range must never be used to trigger an exit.
                    candle_events.append({
                        "timestamp_ms": ts, "action": "entry_candle_replay_skipped",
                    })
                else:
                    event = broker.process_candle(
                        symbol, timestamp, float(candle[2]), float(candle[3]),
                        open_price=float(candle[1]),
                    )
                    candle_events.append({
                        "timestamp_ms": ts, "action": "manage_position", "exit": event,
                        "position_open": any(p["symbol"] == symbol
                                             for p in broker.open_positions()),
                    })
            elif broker.has_trade_activity(symbol, timestamp):
                # A prior execution may have committed immediately before a
                # crash. Do not analyze/re-enter on replay of that same candle.
                candle_events.append({
                    "timestamp_ms": ts, "action": "execution_already_recorded",
                })
            else:
                recent = _load_recent(
                    settings.database_path, settings.exchange_id, symbol,
                    timeframe, candles, end_ms=ts,
                )
                if len(recent) < 60:
                    candle_events.append({
                        "timestamp_ms": ts, "action": "insufficient_history",
                        "candles": len(recent),
                    })
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
                                timestamp,
                            )
                        except ValueError as exc:
                            rejection = str(exc)
                    candle_events.append({
                        "timestamp_ms": ts, "action": "analyze",
                        "decision": decision.decision, "reason": decision.reason,
                        "proposal": decision.proposal, "opened": opened,
                        "execution_rejected": rejection, "reports": decision.reports,
                    })
            # Advance state only after this candle has been fully handled.
            _save_processed(settings.database_path, symbol, ts, settings.exchange_id, timeframe)

        results.append({
            "symbol": symbol,
            "status": "processed" if pending else "no_new_candle",
            "candles_processed": len(pending),
            "events": candle_events,
        })

    # Use the last stored close for any open position with a temporarily missing feed.
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
