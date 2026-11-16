"""Isolated research-only signals. OHLCV rows must contain CLOSED candles."""
from dataclasses import dataclass
from math import isfinite

@dataclass(frozen=True)
class Signal:
    strategy: str
    reference_price: float
    stop_loss: float
    take_profit: float

def _series(rows):
    if any(len(r) != 6 or not all(isfinite(float(v)) for v in r)
           or r[3] <= 0 or not r[3] <= r[1] <= r[2]
           or not r[3] <= r[4] <= r[2] or r[5] < 0 for r in rows):
        raise ValueError("invalid OHLCV")
    if any(rows[i][0] <= rows[i-1][0] for i in range(1, len(rows))):
        raise ValueError("timestamps must increase")
    return ([float(r[2]) for r in rows], [float(r[3]) for r in rows],
            [float(r[4]) for r in rows])

def _wilder(values, n):
    result = [None] * len(values)
    if len(values) < n:
        return result
    avg = sum(values[:n]) / n
    result[n-1] = avg
    for i in range(n, len(values)):
        avg = (avg * (n-1) + values[i]) / n
        result[i] = avg
    return result

def _ema(values, n):
    out = [None] * len(values)
    if len(values) < n:
        return out
    avg = sum(values[:n]) / n
    out[n-1] = avg
    for i in range(n, len(values)):
        avg += (values[i] - avg) * 2 / (n+1)
        out[i] = avg
    return out

def _atr(h, l, c, n):
    tr = [h[0] - l[0]]
    for i in range(1, len(c)):
        tr.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
    return _wilder(tr, n)

def _adx(h, l, c, n):
    tr, plus, minus = [], [], []
    for i in range(1, len(c)):
        up, down = h[i]-h[i-1], l[i-1]-l[i]
        plus.append(up if up > down and up > 0 else 0.)
        minus.append(down if down > up and down > 0 else 0.)
        tr.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
    st, sp, sm = _wilder(tr,n), _wilder(plus,n), _wilder(minus,n)
    dx = []
    result = [None] * len(c)
    for i in range(n-1, len(tr)):
        p = 100*sp[i]/st[i] if st[i] else 0.
        m = 100*sm[i]/st[i] if st[i] else 0.
        dx.append(100*abs(p-m)/(p+m) if p+m else 0.)
        if len(dx) >= n:
            result[i+1] = (sum(dx[:n])/n if len(dx)==n
                           else (result[i]*(n-1)+dx[-1])/n)
    return result

def _signal(name, close, atr, stop_atr, rr):
    if atr is None or atr <= 0 or stop_atr*atr >= close:
        return None
    distance = stop_atr*atr
    return Signal(name, close, close-distance, close+rr*distance)

def donchian_atr(rows, *, channel=20, atr_period=14, stop_atr=2., rr=2.):
    """Close breaks preceding channel high; current candle excluded."""
    if channel < 2 or atr_period < 2 or stop_atr <= 0 or rr <= 0:
        raise ValueError("invalid parameters")
    if not rows:
        return None
    h,l,c = _series(rows)
    if len(c) < max(channel+1, atr_period):
        return None
    if c[-1] <= max(h[-channel-1:-1]):
        return None
    return _signal("donchian_atr", c[-1], _atr(h,l,c,atr_period)[-1], stop_atr, rr)

def ema_adx(rows, *, fast=50, slow=200, adx_period=14,
            adx_threshold=25., atr_period=14, stop_atr=2., rr=2.):
    """Bullish EMA regime, strong ADX, fresh close crossing fast EMA."""
    if not 2 <= fast < slow or min(adx_period,atr_period) < 2 or adx_threshold < 0 or stop_atr <= 0 or rr <= 0:
        raise ValueError("invalid parameters")
    if not rows:
        return None
    h,l,c = _series(rows)
    if len(c) < max(slow+1, 2*adx_period+1, atr_period):
        return None
    ef, es = _ema(c,fast), _ema(c,slow)
    strength = _adx(h,l,c,adx_period)[-1]
    if strength is None or strength < adx_threshold:
        return None
    if not (ef[-1] > es[-1] and c[-2] <= ef[-2] and c[-1] > ef[-1]):
        return None
    return _signal("ema_adx", c[-1], _atr(h,l,c,atr_period)[-1], stop_atr, rr)
