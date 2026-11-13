from crypto_trader.team import analyze_symbol


def make_rows(kind="up", count=120):
    rows = []
    price = 100.0
    for i in range(count):
        if kind == "up":
            price *= 1.0015 if i % 4 != 3 else 0.997
        elif kind == "down":
            price *= 0.9985 if i % 4 != 3 else 1.003
        else:
            price *= 1.001 if i % 2 else 0.999
        rows.append([i * 3_600_000, price * 0.999, price * 1.002,
                     price * 0.998, price, 1000.0])
    return rows


def test_team_emits_structured_reports_and_news_abstains():
    result = analyze_symbol("BTC/USDT", make_rows("up"))
    names = {report["agent"] for report in result.reports}
    assert {"market_scanner", "technical_analyst", "market_regime",
            "news_analyst", "counterargument", "risk_manager",
            "chief_trader"} <= names
    news = next(r for r in result.reports if r["agent"] == "news_analyst")
    assert news["status"] == "unavailable"


def test_team_does_not_trade_without_technical_setup():
    result = analyze_symbol("BTC/USDT", make_rows("down"))
    assert result.decision == "NO_TRADE"
    assert result.proposal is None


def test_counterargument_produces_three_objections_for_candidate():
    result = analyze_symbol("BTC/USDT", make_rows("up"))
    assert result.proposal is not None
    counter = next(r for r in result.reports if r["agent"] == "counterargument")
    assert len(counter["objections"]) >= 3


def test_risk_manager_veto_cannot_be_overridden():
    result = analyze_symbol("BTC/USDT", make_rows("up"))
    risk = next(r for r in result.reports if r["agent"] == "risk_manager")
    if risk["status"] == "veto":
        assert result.decision == "NO_TRADE"
