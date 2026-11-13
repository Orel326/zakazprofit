"""OHLCV validation. A candle is [timestamp_ms, open, high, low, close, volume]."""

from dataclasses import dataclass, field
import math
from typing import Sequence

Ohlcv = Sequence[float | int]


@dataclass
class QualityReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def validate_ohlcv(rows: Sequence[Ohlcv], timeframe_ms: int) -> QualityReport:
    report = QualityReport()
    if timeframe_ms <= 0:
        report.errors.append("timeframe_ms must be positive")
        return report
    if not rows:
        report.errors.append("dataset is empty")
        return report

    previous_ts: int | None = None
    seen: set[int] = set()
    for index, row in enumerate(rows):
        if len(row) != 6:
            report.errors.append(f"row {index}: expected 6 OHLCV fields, got {len(row)}")
            continue
        try:
            ts = int(row[0])
            open_, high, low, close, volume = (float(value) for value in row[1:])
        except (TypeError, ValueError, OverflowError):
            report.errors.append(f"row {index}: contains a non-numeric value")
            continue
        if not all(math.isfinite(value) for value in (open_, high, low, close, volume)):
            report.errors.append(f"row {index}: contains NaN or infinity")
            continue
        if ts < 0:
            report.errors.append(f"row {index}: timestamp is negative")
        if ts in seen:
            report.errors.append(f"row {index}: duplicate timestamp {ts}")
        seen.add(ts)
        if previous_ts is not None:
            if ts <= previous_ts:
                report.errors.append(f"row {index}: timestamps are not strictly increasing")
            elif ts - previous_ts > timeframe_ms:
                missing = (ts - previous_ts) // timeframe_ms - 1
                report.warnings.append(f"gap before row {index}: approximately {missing} candle(s) missing")
        previous_ts = ts
        if min(open_, high, low, close) <= 0:
            report.errors.append(f"row {index}: prices must be positive")
        if high < max(open_, close, low):
            report.errors.append(f"row {index}: high is below another OHLC price")
        if low > min(open_, close, high):
            report.errors.append(f"row {index}: low is above another OHLC price")
        if volume < 0:
            report.errors.append(f"row {index}: volume is negative")
    return report
