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

def test_dynamic_filter_changes_actual_executions(monkeypatch):
    from crypto_trader.research_portfolio import STRATEGIES
    from crypto_trader.research_strategies import Signal
    data = dataset(count=240)
    # ETH trends downward while BTC trends upward; no future observations.
    for i, row in enumerate(data["ETH/USDT"]):
        price = 100-i*.1
        row[1],row[2],row[3],row[4] = price,price+1,price-1,price
    def always_signal(rows):
        price = float(rows[-1][4])
        return Signal("ema_adx",price,price*.5,price*1.5)
    monkeypatch.setitem(STRATEGIES,"ema_adx",always_signal)
    fixed = portfolio_backtest(data,HOUR)
    dynamic = portfolio_backtest(data,HOUR,selection_size=1)
    assert fixed["trades_by_symbol"]["ETH/USDT"] > 0
    assert dynamic["trades_by_symbol"]["ETH/USDT"] == 0
    assert dynamic["trades_by_symbol"]["BTC/USDT"] > 0
    assert dynamic["closed_trades"] < fixed["closed_trades"]

def test_next_open_entry_and_same_candle_stop_are_conservative(monkeypatch):
    from crypto_trader.research_portfolio import STRATEGIES
    from crypto_trader.research_strategies import Signal
    data=dataset(("BTC/USDT",),count=225)
    # Signal only on candle 220. The next candle gaps down but stays
    # between stop and target; intrabar low then hits stop.
    data["BTC/USDT"][220][1:5]=[100.,101.,99.,100.]
    data["BTC/USDT"][221][1:5]=[95.,96.,89.,93.]
    called=[]
    def once(rows):
        if len(rows)==221:
            return Signal("ema_adx",100.,90.,120.)
        return None
    monkeypatch.setitem(STRATEGIES,"ema_adx",once)
    from crypto_trader import research_portfolio as rp
    original_open=rp.PaperBroker.open_position
    def spy_open(self,symbol,market_price,stop_loss,take_profit,occurred_at=None):
        called.append((symbol,market_price,occurred_at))
        return original_open(self,symbol,market_price,stop_loss,take_profit,occurred_at)
    monkeypatch.setattr(rp.PaperBroker,"open_position",spy_open)
    report=portfolio_backtest(data,HOUR)
    assert len(called)==1
    assert called[0][1]==95.  # next open, NOT signal close 100
    assert called[0][2].startswith("1970-01-10T05:00:00")  # index 221 candle open
    assert report["closed_trades"]==1
    assert report["return_pct"]<0

def test_last_candle_signal_cannot_fill_without_next_open(monkeypatch):
    from crypto_trader.research_portfolio import STRATEGIES
    from crypto_trader.research_strategies import Signal
    data=dataset(("BTC/USDT",),count=225)
    def only_last(rows):
        if len(rows)==225:
            price=float(rows[-1][4])
            return Signal("ema_adx",price,price*.9,price*1.1)
        return None
    monkeypatch.setitem(STRATEGIES,"ema_adx",only_last)
    report=portfolio_backtest(data,HOUR)
    assert report["closed_trades"]==0
    assert report["return_pct"]==0

def test_portfolio_caps_simultaneous_positions_and_combined_risk(monkeypatch):
    from crypto_trader.research_portfolio import STRATEGIES
    from crypto_trader.research_strategies import Signal
    from crypto_trader import research_portfolio as rp
    data=dataset(UNIVERSE,count=225)
    def signal_every_close(rows):
        price=float(rows[-1][4])
        return Signal("ema_adx",price,price*.95,price*1.1)
    monkeypatch.setitem(STRATEGIES,"ema_adx",signal_every_close)
    observed=[]
    original=rp.PaperBroker.open_position
    def audited(self,symbol,market_price,stop_loss,take_profit,occurred_at=None):
        result=original(self,symbol,market_price,stop_loss,take_profit,occurred_at)
        positions=self.open_positions()
        observed.append((len(positions),self.account()["equity_at_entry_marks"],sum(
            p["quantity"]*(p["entry_price"]*(1+self.config.fee_rate)
            -p["stop_loss"]*(1-self.config.slippage_rate)*(1-self.config.fee_rate))
            for p in positions)))
        return result
    monkeypatch.setattr(rp.PaperBroker,"open_position",audited)
    portfolio_backtest(data,HOUR)
    assert observed
    assert all(count<=RESEARCH_CONFIG.max_open_positions for count,_,_ in observed)
    assert all(risk<=equity*RESEARCH_CONFIG.max_total_open_risk+1e-7
               for _,equity,risk in observed)

def test_dynamic_evaluation_window_ignores_future_prices(monkeypatch):
    from crypto_trader.research_portfolio import STRATEGIES
    from crypto_trader.research_strategies import Signal
    data=dataset(UNIVERSE[:5],count=260)
    def deterministic_signal(rows):
        price=float(rows[-1][4])
        return Signal("ema_adx",price,price*.95,price*1.1)
    monkeypatch.setitem(STRATEGIES,"ema_adx",deterministic_signal)
    first=portfolio_backtest(data,HOUR,selection_size=3,
                             start_index=220,end_index=240)
    for rows in data.values():
        for i in range(240,260):
            rows[i][4]=rows[i][1]
    second=portfolio_backtest(data,HOUR,selection_size=3,
                              start_index=220,end_index=240)
    assert first==second

def test_dynamic_selection_excludes_correlated_existing_holdings():
    from crypto_trader.research_portfolio import _eligible_universe
    data=dataset(UNIVERSE[:5],count=250)
    # Every symbol has the same return stream. A position already held in
    # BTC must prevent stacking another highly correlated symbol.
    selected=_eligible_universe(data,230,3,0.85,held={"BTC/USDT"})
    assert selected==frozenset()

def test_dynamic_selection_can_choose_uncorrelated_new_symbol_with_holding():
    from crypto_trader.research_portfolio import _eligible_universe
    data=dataset(UNIVERSE[:2],count=250)
    # ETH has a positive but different alternating return pattern.
    for i,row in enumerate(data["ETH/USDT"]):
        price=100+i*.1+(2 if i%2 else -2)
        row[1:5]=[price,price+1,price-1,price]
    selected=_eligible_universe(data,230,2,0.85,held={"BTC/USDT"})
    assert selected==frozenset({"ETH/USDT"})


def test_dynamic_selection_counts_existing_holdings_against_size(monkeypatch):
    from crypto_trader import research_portfolio as rp
    data = dataset(UNIVERSE[:5], count=250)
    # Isolate capacity accounting from the correlation constraint.
    monkeypatch.setattr(rp, "_correlation", lambda *args, **kwargs: 0.0)
    selected = rp._eligible_universe(data, 230, 3, 0.85,
                                     held={"BTC/USDT", "ETH/USDT"})
    assert len(selected) == 1
    assert not selected.intersection({"BTC/USDT", "ETH/USDT"})
    assert len(selected) + 2 == 3


def test_dynamic_selection_at_capacity_does_not_add_assets(monkeypatch):
    from crypto_trader import research_portfolio as rp
    data = dataset(UNIVERSE[:5], count=250)
    monkeypatch.setattr(rp, "_correlation", lambda *args, **kwargs: 0.0)
    assert rp._eligible_universe(data, 230, 3, 0.85,
                                 held=set(UNIVERSE[:3])) == frozenset()
    assert rp._eligible_universe(data, 230, 3, 0.85,
                                 held=set(UNIVERSE[:4])) == frozenset()


def test_dynamic_portfolio_never_exceeds_requested_holdings(monkeypatch):
    from crypto_trader import research_portfolio as rp
    from crypto_trader.research_strategies import Signal
    data = dataset(UNIVERSE[:5], count=240)
    # Independent momentum series permit several eligible candidates.
    for j, rows in enumerate(data.values()):
        for i, row in enumerate(rows):
            price = 100 + i * (0.1 + j * 0.02) + (j + 1) * (i % (j + 2)) * 0.01
            row[1:5] = [price, price + 1, price - 1, price]
    monkeypatch.setattr(rp, "_correlation", lambda *args, **kwargs: 0.0)
    monkeypatch.setitem(rp.STRATEGIES, "ema_adx", lambda rows: Signal(
        "ema_adx", float(rows[-1][4]), float(rows[-1][4]) * .5,
        float(rows[-1][4]) * 1.5))
    counts = []
    original = rp.PaperBroker.open_position
    def observed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        counts.append(len(self.open_positions()))
        return result
    monkeypatch.setattr(rp.PaperBroker, "open_position", observed)
    report = rp.portfolio_backtest(data, HOUR, selection_size=3)
    assert report["closed_trades"] > 0
    assert counts and max(counts) <= 3


def test_dynamic_selection_ignores_future_ohlc_not_just_close():
    from crypto_trader.research_portfolio import _eligible_universe
    data = dataset(UNIVERSE[:5], count=250)
    before = _eligible_universe(data, 230, 3, 0.85)
    for rows in data.values():
        for i in range(231, 250):
            rows[i][1:5] = [500., 600., 400., 550.]
    assert _eligible_universe(data, 230, 3, 0.85) == before


def test_opening_gap_exit_precedes_new_entries(monkeypatch):
    from crypto_trader import research_portfolio as rp
    from crypto_trader.research_strategies import Signal
    data = dataset(UNIVERSE[:2], count=225)
    for row in data["ETH/USDT"]:
        row[1:5] = [200., 201., 199., 200.]
    # BTC enters at index 220 and gaps through its stop at index 221,
    # when ETH has a pending order from the previous close.
    data["BTC/USDT"][221][1:5] = [80., 82., 79., 81.]
    def signal(rows):
        price = float(rows[-1][4])
        if len(rows) == 220 and price < 150:
            return Signal("ema_adx", price, 90., 150.)
        if len(rows) == 221 and price > 150:
            return Signal("ema_adx", price, 180., 240.)
        return None
    monkeypatch.setitem(rp.STRATEGIES, "ema_adx", signal)
    events = []
    original_open = rp.PaperBroker.open_position
    original_close = rp.PaperBroker.close_position
    def tracked_open(self, symbol, *args, **kwargs):
        events.append(("open", symbol))
        return original_open(self, symbol, *args, **kwargs)
    def tracked_close(self, symbol, *args, **kwargs):
        events.append(("close", symbol))
        return original_close(self, symbol, *args, **kwargs)
    monkeypatch.setattr(rp.PaperBroker, "open_position", tracked_open)
    monkeypatch.setattr(rp.PaperBroker, "close_position", tracked_close)
    rp.portfolio_backtest(data, HOUR)
    assert ("open", "BTC/USDT") in events
    assert ("close", "BTC/USDT") in events
    assert ("open", "ETH/USDT") in events
    assert events.index(("close", "BTC/USDT")) < events.index(("open", "ETH/USDT"))
