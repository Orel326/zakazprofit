"""Research-only chronological simulator using the existing virtual spot broker.

No exchange orders. Signals only see candles through the current close.
Entries fill at the NEXT candle open (plus slippage); intrabar stops are checked immediately.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .paper import PaperBroker, PaperConfig
from .quality import validate_ohlcv
from .research_strategies import donchian_atr, ema_adx

STRATEGIES = {"donchian_atr": donchian_atr, "ema_adx": ema_adx}
WARMUP = {"donchian_atr": 60, "ema_adx": 220}

def simulate(symbol, rows, timeframe_ms, strategy, config=None, *, start_index=0, end_index=None):
    if strategy not in STRATEGIES:
        raise ValueError("unknown strategy")
    end = len(rows) if end_index is None else end_index
    if not 0 <= start_index < end <= len(rows) or end < WARMUP[strategy]:
        raise ValueError("insufficient history or invalid window")
    quality = validate_ohlcv(rows[:end], timeframe_ms)
    if not quality.is_valid or quality.warnings:
        raise ValueError("invalid or gapped OHLCV: " + "; ".join(quality.errors + quality.warnings))
    cfg = config or PaperConfig()
    with tempfile.TemporaryDirectory(prefix="crypto-research-") as directory:
        broker = PaperBroker(Path(directory) / "paper.sqlite3", cfg)
        curve = []
        pending = None  # Signal from previous CLOSED candle only.
        for i in range(start_index, end):
            candle = rows[i]
            stamp = datetime.fromtimestamp((int(candle[0]) + timeframe_ms)/1000, timezone.utc).isoformat()
            open_stamp = datetime.fromtimestamp(int(candle[0])/1000, timezone.utc).isoformat()
            if pending is not None:
                open_price = float(candle[1])
                if pending.stop_loss < open_price < pending.take_profit:
                    try:
                        broker.open_position(symbol, open_price, pending.stop_loss,
                                             pending.take_profit, open_stamp)
                    except ValueError:
                        pass
                pending = None
            # Stop/target can be touched on the very candle of entry.
            if broker.open_positions():
                broker.process_candle(symbol, stamp, float(candle[2]), float(candle[3]),
                                      open_price=float(candle[1]))
            # Do not enter at the close used to calculate the signal.
            if not broker.open_positions() and i + 1 >= WARMUP[strategy] and i + 1 < end:
                pending = STRATEGIES[strategy](rows[:i+1])
            marks = {symbol:float(candle[4])} if broker.open_positions() else {}
            equity = broker.record_equity(marks, stamp)["equity"]
            curve.append(equity)
        last = rows[end-1]
        stamp = datetime.fromtimestamp((int(last[0])+timeframe_ms)/1000, timezone.utc).isoformat()
        if broker.open_positions():
            broker.close_position(symbol, float(last[4]), "end_of_backtest", stamp)
            curve[-1] = broker.account()["cash"]
        trades = [t for t in broker.trade_history() if t["side"] == "CLOSE"]
        pnls = [float(t["pnl"]) for t in trades]
        wins = sum(x for x in pnls if x > 0)
        losses = -sum(x for x in pnls if x < 0)
        peak, dd = cfg.initial_balance, 0.
        for eq in curve:
            peak = max(peak, eq)
            dd = max(dd, (peak-eq)/peak)
        first_price, last_price = float(rows[start_index][4]), float(last[4])
        entry = first_price * (1 + cfg.slippage_rate)
        qty = cfg.initial_balance / (entry*(1+cfg.fee_rate))
        benchmark = qty * last_price*(1-cfg.slippage_rate)*(1-cfg.fee_rate)
        return {
            "strategy": strategy, "symbol": symbol,
            "execution_model": "signal at close; next-open entry; same-candle stop priority",
            "return_pct": (curve[-1]/cfg.initial_balance-1)*100,
            "net_pnl": curve[-1]-cfg.initial_balance,
            "ending_equity": curve[-1],
            "max_drawdown_pct": dd*100,
            "closed_trades": len(pnls),
            "win_rate_pct": 100*sum(x>0 for x in pnls)/len(pnls) if pnls else 0.,
            "profit_factor": wins/losses if losses else None,
            "expectancy_per_trade": sum(pnls)/len(pnls) if pnls else 0.,
            "buy_and_hold_return_pct": (benchmark/cfg.initial_balance-1)*100,
            "start_index": start_index, "end_index": end,
            "from_timestamp_ms": int(rows[start_index][0]),
            "to_timestamp_ms": int(last[0]),
            "trades": trades,
        }

def evaluate(symbol, rows, timeframe_ms, strategy, config=None, folds=4):
    if folds < 2 or len(rows)-WARMUP[strategy] < folds*2:
        raise ValueError("insufficient history for folds")
    baseline = simulate(symbol, rows, timeframe_ms, strategy, config)
    start = WARMUP[strategy]
    n = len(rows)-start
    segments = []
    for j in range(folds):
        left = start+n*j//folds
        right = start+n*(j+1)//folds
        result = simulate(symbol, rows, timeframe_ms, strategy, config,
                          start_index=left, end_index=right)
        segments.append({key: result[key] for key in (
            "return_pct", "max_drawdown_pct", "closed_trades", "profit_factor",
            "buy_and_hold_return_pct", "from_timestamp_ms", "to_timestamp_ms")})
    return {
        "metrics": {k: v for k,v in baseline.items() if k != "trades"},
        "folds": segments,
        "profitable_folds": sum(f["return_pct"] > 0 for f in segments),
        "method": "fixed parameters, chronological disjoint evaluation folds, fresh account per fold; not parameter-optimized walk-forward",
    }
