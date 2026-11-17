"""Run 2/5/10 symbol paper portfolio research on public OHLCV."""
import argparse
import hashlib
import json
import os
from dataclasses import asdict, replace
from datetime import datetime, timezone

from .config import Settings
from .exchange import TIMEFRAME_MS, create_exchange, fetch_closed_ohlcv
from .research_portfolio import UNIVERSE, RESEARCH_CONFIG, compare_portfolios, portfolio_backtest
from .research_backtest import WARMUP
from .research_forward import evaluate_forward

def main():
    parser = argparse.ArgumentParser(description="Research-only portfolio backtest; no orders")
    parser.add_argument("--days",type=int,default=365)
    parser.add_argument("--timeframe",choices=("1h","4h"),default="1h")
    parser.add_argument("--folds",type=int,default=4)
    args = parser.parse_args()
    if args.days<1 or args.folds<2:
        parser.error("days >= 1 and folds >= 2 required")
    settings = Settings.from_env()
    now = int(datetime.now(timezone.utc).timestamp()*1000)
    since = now-args.days*86400000
    exchange = create_exchange(settings.exchange_id)
    data, hashes, rejected = {}, {}, {}
    try:
        exchange.load_markets()
        for symbol in UNIVERSE:
            if symbol not in exchange.markets or not exchange.markets[symbol].get("spot"):
                rejected[symbol] = "not a supported spot market"
                continue
            try:
                rows = fetch_closed_ohlcv(exchange,symbol,args.timeframe,since,now,
                                          allowed_symbols=frozenset(UNIVERSE))
                data[symbol] = rows
                canonical = json.dumps([[int(r[0]),*[float(x) for x in r[1:6]]] for r in rows],
                                       separators=(",",":"),allow_nan=False).encode()
                hashes[symbol] = hashlib.sha256(canonical).hexdigest()
            except Exception as exc:
                rejected[symbol] = f"{type(exc).__name__}: {exc}"
    finally:
        exchange.close()
    # Reject missing/unequal histories explicitly rather than silently forward-filling.
    try:
        comparisons = compare_portfolios(data,TIMEFRAME_MS[args.timeframe],folds=args.folds)
    except ValueError as exc:
        comparisons = {"status":"rejected","reason":str(exc)}
    dynamic = {}
    if all(s in data for s in UNIVERSE):
        for count in (3,5):
            try:
                subset = {s:data[s] for s in UNIVERSE}
                full = portfolio_backtest(subset, TIMEFRAME_MS[args.timeframe],
                                          selection_size=count)
                length = len(next(iter(subset.values())))
                warmup = WARMUP["ema_adx"]
                folds = []
                for j in range(args.folds):
                    left = warmup+(length-warmup)*j//args.folds
                    right = warmup+(length-warmup)*(j+1)//args.folds
                    folds.append(portfolio_backtest(
                        subset,TIMEFRAME_MS[args.timeframe],selection_size=count,
                        start_index=left,end_index=right))
                dynamic[str(count)] = {
                    "full_period":full,"folds":folds,
                    "profitable_folds":sum(f["return_pct"]>0 for f in folds),
                    "method":"fixed parameters, non-overlapping chronological evaluation folds; no optimization"
                }
            except ValueError as exc:
                dynamic[str(count)] = {"status":"rejected","reason":str(exc)}
    else:
        dynamic = {"status":"rejected","reason":"incomplete 10-symbol dataset"}
    # Chronological last-quarter evaluation, not a pristine holdout: earlier
    # iterations inspected this dataset. Keep fixed parameters, no optimization.
    diagnostics = {}
    expensive = replace(RESEARCH_CONFIG,
                        fee_rate=RESEARCH_CONFIG.fee_rate*2,
                        slippage_rate=RESEARCH_CONFIG.slippage_rate*2)
    for label, selected, kwargs in (
        ("fixed_2", UNIVERSE[:2], {}),
        ("fixed_5", UNIVERSE[:5], {}),
        ("fixed_10", UNIVERSE, {}),
        ("dynamic_3", UNIVERSE, {"selection_size":3}),
        ("dynamic_5", UNIVERSE, {"selection_size":5}),
    ):
        if not all(s in data for s in selected):
            diagnostics[label] = {"status":"rejected","reason":"missing requested symbols"}
            continue
        subset = {s:data[s] for s in selected}
        length = len(next(iter(subset.values())))
        boundary = WARMUP["ema_adx"] + (length-WARMUP["ema_adx"])*3//4
        try:
            diagnostics[label] = {
                "chronological_last_quarter":portfolio_backtest(
                    subset,TIMEFRAME_MS[args.timeframe],
                    start_index=boundary,**kwargs),
                "double_cost_last_quarter":portfolio_backtest(
                    subset,TIMEFRAME_MS[args.timeframe],
                    start_index=boundary,config=expensive,**kwargs),
                "double_cost_full_period":portfolio_backtest(
                    subset,TIMEFRAME_MS[args.timeframe],
                    config=expensive,**kwargs),
            }
        except ValueError as exc:
            diagnostics[label] = {"status":"rejected","reason":str(exc)}
    # Predefined alternative already implemented in research_strategies.py.
    # Exploratory comparison on the SAME historical sample, not a holdout.
    # No parameters are tuned using the observed outcomes.
    alternative = {}
    for label, selected, kwargs in (
        ("fixed_2", UNIVERSE[:2], {}),
        ("dynamic_5", UNIVERSE, {"selection_size": 5}),
    ):
        if not all(s in data for s in selected):
            alternative[label] = {"status": "rejected", "reason": "missing requested symbols"}
            continue
        subset = {s: data[s] for s in selected}
        try:
            alternative[label] = portfolio_backtest(
                subset, TIMEFRAME_MS[args.timeframe], strategy="donchian_atr", **kwargs
            )
        except ValueError as exc:
            alternative[label] = {"status": "rejected", "reason": str(exc)}
    print(json.dumps({
        "_metadata":{
            "created_at_utc":datetime.now(timezone.utc).isoformat(),
            "git_commit":os.getenv("GITHUB_SHA"),
            "exchange":settings.exchange_id,"timeframe":args.timeframe,
            "requested_days":args.days,"universe":UNIVERSE,
            "risk":asdict(RESEARCH_CONFIG),"dataset_sha256":hashes,
            "note":"Research-only shared-cash paper portfolio; fixed EMA/ADX parameters; no orders, no parameter tuning.",
        },
        "candles_by_symbol":{s:len(r) for s,r in data.items()},
        "rejected_symbols":rejected,"portfolios":comparisons,
        "dynamic_top_3_5":dynamic,
        "chronological_evaluation_and_cost_stress":diagnostics,
        "exploratory_donchian_atr":alternative,
        "precommitted_forward_validation":evaluate_forward(data,TIMEFRAME_MS[args.timeframe]),
    },ensure_ascii=False,indent=2,allow_nan=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
