"""Offline checks for shared-cash multi-asset research."""
from dataclasses import replace

import pytest

from crypto_trader.paper import PaperBroker
from crypto_trader.research_portfolio import (
    RESEARCH_CONFIG, UNIVERSE, compare_portfolios, portfolio_backtest,
)

HOUR = 3600000

def dataset(symbols=UNIVERSE[:2],count=240):
    return {
        symbol:[[i*HOUR,100+i*.1,101+i*.1,99+i*.1,100+i*.1,100]
                for i in range(count)]
        for symbol in symbols
    }

def test_two_asset_portfolio_runs_without_live_orders():
    report=portfolio_backtest(dataset(),HOUR)
    assert report["symbols"]==sorted(UNIVERSE[:2])
    assert report["ending_equity"]>0

def test_ten_asset_portfolio_and_fold_comparison():
    data=dataset(UNIVERSE)
    result=compare_portfolios(data,HOUR,folds=2)
    assert set(result)=={"2","5","10"}
    assert all(len(v["folds"])==2 for v in result.values())
    assert all(v["full_period"]["closed_trades"]>=0 for v in result.values())

def test_missing_market_is_rejected_not_silently_substituted():
    report=compare_portfolios(dataset(),HOUR,folds=2)
    assert report["5"]["status"]=="rejected"
    assert report["10"]["status"]=="rejected"

def test_misaligned_timestamps_are_rejected():
    data=dataset()
    data["ETH/USDT"][5][0]+=1
    with pytest.raises(ValueError,match="invalid/gapped candles|identical candle timestamps"):
        portfolio_backtest(data,HOUR)

def test_research_symbol_allowlist_is_opt_in(tmp_path):
    standard=PaperBroker(tmp_path/"standard.sqlite3")
    with pytest.raises(ValueError,match="unsupported"):
        standard.open_position("SOL/USDT",100,95,110)
    research=PaperBroker(tmp_path/"research.sqlite3",RESEARCH_CONFIG,
                         allowed_symbols=frozenset(UNIVERSE))
    opened=research.open_position("SOL/USDT",100,95,110)
    assert opened["symbol"]=="SOL/USDT"

def test_risk_is_conservative():
    assert RESEARCH_CONFIG.risk_per_trade==0.0025
    assert RESEARCH_CONFIG.max_open_positions==4
    assert RESEARCH_CONFIG.max_total_open_risk==0.01

def test_dynamic_selection_respects_correlated_assets():
    from crypto_trader.research_portfolio import _eligible_universe
    data=dataset(UNIVERSE[:5])
    selected=_eligible_universe(data,230,3,0.85)
    # Identical return streams must not be treated as diversified.
    assert len(selected)==1

def test_dynamic_selection_runs_without_future_prices():
    data=dataset(UNIVERSE[:5])
    result=portfolio_backtest(data,HOUR,selection_size=3)
    assert result["selection_size"]==3
    assert result["closed_trades"]>=0

def test_dynamic_settings_are_validated():
    with pytest.raises(ValueError,match="dynamic selection"):
        portfolio_backtest(dataset(),HOUR,selection_size=0)

def test_dynamic_selection_does_not_use_future_candles():
    from crypto_trader.research_portfolio import _eligible_universe
    data=dataset(UNIVERSE[:5],count=250)
    before=_eligible_universe(data,230,3,0.85)
    # Large future price moves cannot affect decisions at candle 230.
    for symbol in data:
        for i in range(231,250):
            data[symbol][i][4] *= (1.5 if symbol=="BTC/USDT" else .5)
    after=_eligible_universe(data,230,3,0.85)
    assert before==after

def test_portfolio_fold_does_not_depend_on_future_data():
    data=dataset(UNIVERSE[:2],count=260)
    first=portfolio_backtest(data,HOUR,start_index=220,end_index=240)
    # Alter future candles only; earlier fold must be unchanged.
    for rows in data.values():
        for i in range(240,260):
            rows[i][4]=rows[i][1]
    second=portfolio_backtest(data,HOUR,start_index=220,end_index=240)
    assert first["ending_equity"]==second["ending_equity"]
    assert first["closed_trades"]==second["closed_trades"]
