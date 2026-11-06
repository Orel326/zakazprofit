"""SQLite persistence for normalized OHLCV candles."""

import sqlite3
from pathlib import Path
from typing import Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    fetched_at_ms INTEGER NOT NULL,
    PRIMARY KEY (exchange, symbol, timeframe, timestamp_ms)
);
CREATE INDEX IF NOT EXISTS idx_candles_symbol_time
ON candles (symbol, timeframe, timestamp_ms);
"""


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(SCHEMA)
    return connection


def save_candles(
    database_path: Path,
    exchange: str,
    symbol: str,
    timeframe: str,
    rows: Sequence[Sequence[float | int]],
    fetched_at_ms: int,
) -> int:
    sql = """INSERT INTO candles
        (exchange, symbol, timeframe, timestamp_ms, open, high, low, close, volume, fetched_at_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(exchange, symbol, timeframe, timestamp_ms) DO UPDATE SET
        open=excluded.open, high=excluded.high, low=excluded.low,
        close=excluded.close, volume=excluded.volume, fetched_at_ms=excluded.fetched_at_ms"""
    values = [
        (exchange, symbol, timeframe, int(r[0]), *map(float, r[1:6]), fetched_at_ms)
        for r in rows
    ]
    with connect(database_path) as connection:
        connection.executemany(sql, values)
    return len(values)


def count_candles(database_path: Path, symbol: str, timeframe: str) -> int:
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        ).fetchone()
    return int(row[0])
