"""Research-only multi-asset portfolio backtest, sharing one cash account.

Every timestamp is processed synchronously: exits first, then ranked entries.
Signals see only closed candles. No real orders, no leverage, no optimization.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .paper import PaperBroker, PaperConfig
from .quality import validate_ohlcv
from .research_backtest import STRATEGIES, WARMUP

UNIVERSE = (
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT",
    "LINK/USDT", "ADA/USDT", "AVAX/USDT", "DOGE/USDT", "LTC/USDT",
)
RESEARCH_CONFIG = PaperConfig(risk_per_trade=0.0025,
                              max_total_open_risk=0.01,
                              max_open_positions=4)

def _rank(signal):
    # Ex-ante signal quality: target distance as a fraction of ATR stop risk.
    # Equal ratios tie, then alphabetical symbol; no future prices involved.
    return (signal.take_profit - signal.reference_price) / signal.reference_price


def _momentum(rows, i, lookback=30):
    if i < lookback:
        return None
    return float(rows[i][4])/float(rows[i-lookback][4])-1.

def _correlation(a, b, i, lookback=30):
    if i < lookback:
        return 1.
    x = [float(a[k][4])/float(a[k-1][4])-1 for k in range(i-lookback+1,i+1)]
    y = [float(b[k][4])/float(b[k-1][4])-1 for k in range(i-lookback+1,i+1)]
    mx, my = sum(x)/len(x),sum(y)/len(y)
    xx = sum((v-mx)**2 for v in x)
    yy = sum((v-my)**2 for v in y)
    if xx*yy <= 1e-20:
        return 1.
    return sum((u-mx)*(v-my) for u,v in zip(x,y))/(xx*yy)**0.5

def _eligible_universe(data, i, selection_size, correlation_limit):
    # Select using only current/past CLOSED candle prices.
    scores = []
    for symbol, rows in data.items():
        momentum = _momentum(rows,i)
        if momentum is not None and momentum > 0:
            scores.append((-momentum,symbol))
    selected = []
    for _,symbol in sorted(scores):
        if all(_correlation(data[symbol],data[other],i) < correlation_limit
               for other in selected):
            selected.append(symbol)
            if len(selected) >= selection_size:
                break
    return frozenset(selected)

def portfolio_backtest(data, timeframe_ms, *, strategy="ema_adx",
                       config=RESEARCH_CONFIG, start_index=0, end_index=None,
                       selection_size=None, correlation_limit=0.85):
    if selection_size is not None and (not isinstance(selection_size,int) or selection_size < 1 or not -1 < correlation_limit < 1):
        raise ValueError("invalid dynamic selection settings")
    if strategy not in STRATEGIES or not data or len(data) > len(UNIVERSE):
        raise ValueError("invalid strategy or portfolio")
    if any(s not in UNIVERSE for s in data):
        raise ValueError("unsupported research symbol")
    for symbol, rows in data.items():
        q = validate_ohlcv(rows, timeframe_ms)
        if not q.is_valid or q.warnings:
            raise ValueError(f"{symbol}: invalid/gapped candles: {q.errors + q.warnings}")
    # Explicitly require a synchronized dataset, avoiding stale price marks.
    times = [tuple(int(row[0]) for row in rows) for rows in data.values()]
    if any(t != times[0] for t in times[1:]):
        raise ValueError("datasets must have identical candle timestamps")
    symbols = tuple(sorted(data))
    length = len(times[0])
    end = length if end_index is None else end_index
    if not 0 <= start_index < end <= length or end < WARMUP[strategy]:
        raise ValueError("invalid evaluation window")
    with tempfile.TemporaryDirectory(prefix="crypto-portfolio-") as directory:
        broker = PaperBroker(Path(directory)/"paper.sqlite3", config,
                             allowed_symbols=frozenset(symbols))
        equity_curve = []
        for i in range(start_index,end):
            stamp = datetime.fromtimestamp(
                (int(data[symbols[0]][i][0])+timeframe_ms)/1000, timezone.utc
            ).isoformat()
            # Existing positions are eligible for exit from the next candle only.
            for p in broker.open_positions():
                symbol = p["symbol"]
                row = data[symbol][i]
                if datetime.fromisoformat(p["opened_at"]) < datetime.fromisoformat(stamp):
                    broker.process_candle(symbol, stamp, float(row[2]), float(row[3]),
                                          open_price=float(row[1]))
            # Rank competing signals before any entry; fixed tie-breaker.
            if i+1 >= WARMUP[strategy]:
                held = {p["symbol"] for p in broker.open_positions()}
                eligible = (frozenset(symbols) if selection_size is None
                            else _eligible_universe(data, i, selection_size, correlation_limit))
                candidates = []
                for symbol in symbols:
                    if symbol not in eligible or symbol in held:
                        continue
                    signal = STRATEGIES[strategy](data[symbol][:i+1])
                    if signal:
                        candidates.append((-_rank(signal), symbol, signal))
                for _,symbol,signal in sorted(candidates):
                    if len(broker.open_positions()) >= config.max_open_positions:
                        break
                    try:
                        broker.open_position(symbol, signal.reference_price,
                                             signal.stop_loss, signal.take_profit, stamp)
                    except ValueError:
                        # Cash, combined risk, and daily guard can block entries.
                        continue
            marks = {p["symbol"]: float(data[p["symbol"]][i][4])
                     for p in broker.open_positions()}
            equity_curve.append(broker.record_equity(marks,stamp)["equity"])
        final_stamp = datetime.fromtimestamp(
            (int(data[symbols[0]][end-1][0])+timeframe_ms)/1000,timezone.utc
        ).isoformat()
        for p in broker.open_positions():
            broker.close_position(p["symbol"],float(data[p["symbol"]][end-1][4]),
                                  "end_of_backtest",final_stamp)
        equity_curve[-1] = broker.account()["cash"]
        trades = [t for t in broker.trade_history() if t["side"]=="CLOSE"]
        pnls = [float(t["pnl"]) for t in trades]
        gains = sum(v for v in pnls if v>0)
        losses = -sum(v for v in pnls if v<0)
        peak, dd = config.initial_balance, 0.
        for value in equity_curve:
            peak = max(peak,value)
            dd = max(dd,(peak-value)/peak)
        return {
            "symbols": list(symbols), "strategy": strategy,
            "selection_size": selection_size, "correlation_limit": correlation_limit,
            "return_pct": 100*(equity_curve[-1]/config.initial_balance-1),
            "ending_equity": equity_curve[-1],
            "max_drawdown_pct": 100*dd,
            "closed_trades": len(trades),
            "win_rate_pct": 100*sum(v>0 for v in pnls)/len(pnls) if pnls else 0.,
            "profit_factor": gains/losses if losses else None,
            "expectancy_per_trade": sum(pnls)/len(pnls) if pnls else 0.,
            "start_timestamp_ms": int(data[symbols[0]][start_index][0]),
            "end_timestamp_ms": int(data[symbols[0]][end-1][0]),
            "trades_by_symbol": {s:sum(t["symbol"]==s for t in trades) for s in symbols},
        }

def compare_portfolios(data, timeframe_ms, *, strategy="ema_adx",
                       sizes=(2,5,10), folds=4, config=RESEARCH_CONFIG):
    if folds < 2:
        raise ValueError("folds must be >= 2")
    out = {}
    for size in sizes:
        selected = UNIVERSE[:size]
        if not all(s in data for s in selected):
            out[str(size)] = {"status":"rejected","reason":"missing requested symbols"}
            continue
        subset = {s:data[s] for s in selected}
        length = len(next(iter(subset.values())))
        start = WARMUP[strategy]
        if length-start < folds*2:
            out[str(size)] = {"status":"rejected","reason":"insufficient candles"}
            continue
        baseline = portfolio_backtest(subset,timeframe_ms,strategy=strategy,config=config)
        segments = []
        for j in range(folds):
            left = start+(length-start)*j//folds
            right = start+(length-start)*(j+1)//folds
            segments.append(portfolio_backtest(subset,timeframe_ms,strategy=strategy,
                                               config=config,start_index=left,end_index=right))
        out[str(size)] = {
            "full_period": baseline,
            "folds": segments,
            "profitable_folds": sum(f["return_pct"]>0 for f in segments),
        }
    return out
