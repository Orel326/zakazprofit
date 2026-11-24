"""Causal daily signal primitives for frozen replay protocol v1.
No execution, sizing or performance claims are made by this module.
Rows are (UTC milliseconds, open, high, low, close, volume).
"""
from statistics import median
from datetime import datetime, timezone

DAY = 86400000
CANDIDATES = ("regime", "shock", "relative_strength", "volume_breakout")

def window(rows, day, n):
    """Require every calendar day, including the signal day."""
    result = [rows.get(day - DAY * i) for i in range(n-1, -1, -1)]
    return result if all(r is not None for r in result) else None

def signals(asset, btc, day):
    """Return entry/exit flags and rank score; None means unavailable."""
    a = window(asset, day, 50)
    b = window(btc, day, 200)
    bull = None if b is None else b[-1][4] > sum(r[4] for r in b)/200
    regime = None if a is None or bull is None else bull and a[-1][4] > sum(r[4] for r in a)/50
    s = window(asset, day, 2)
    shock = None if s is None or bull is None else bull and s[-1][4]/s[0][4]-1 <= -0.08
    rs = window(asset, day, 91)
    score = None if rs is None else rs[-1][4]/rs[0][4]-1
    v = window(asset, day, 21)
    breakout = None
    if v is not None:
        current = v[-1]
        tr = [max(y[2]-y[3], abs(y[2]-x[4]), abs(y[3]-x[4]))
              for x,y in zip(v[-15:-1],v[-14:])]
        breakout = (current[4] > max(r[2] for r in v[:-1])
                    and current[5] > 2*median(r[5] for r in v[:-1])
                    and sum(tr)/14/current[4] <= .06)
    e = window(asset, day, 11)
    exit_breakout = None if e is None else e[-1][4] < min(r[3] for r in e[:-1])
    return dict(regime=regime, shock=shock, relative_strength=score,
                volume_breakout=breakout, exit_breakout=exit_breakout)

def relative_targets(scores, day, slots=5):
    if datetime.fromtimestamp(day/1000, timezone.utc).weekday() != 0:
        return None
    return tuple(sorted((s for s,v in scores.items() if v is not None and v > 0),
                        key=lambda s: (-scores[s], s))[:slots])
