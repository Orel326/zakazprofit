"""Structured deterministic agent team. LLM adapters can be added without changing contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .analysis import MarketSnapshot, analyze_candles


@dataclass(frozen=True)
class AgentReport:
    agent: str
    status: str
    summary: str
    evidence: tuple[str, ...]
    objections: tuple[str, ...] = ()


@dataclass(frozen=True)
class TradeProposal:
    symbol: str
    side: str
    reference_price: float
    stop_loss: float
    take_profit: float
    rationale: tuple[str, ...]


@dataclass(frozen=True)
class TeamDecision:
    decision: str
    reason: str
    snapshot: dict[str, Any]
    reports: tuple[dict[str, Any], ...]
    proposal: dict[str, Any] | None
    risk_approved: bool


def _reports(snapshot: MarketSnapshot) -> tuple[list[AgentReport], TradeProposal | None]:
    reports: list[AgentReport] = []
    # Agent 1: market scanner
    reports.append(AgentReport(
        "market_scanner", "complete",
        f"Scanned {snapshot.symbol} using {snapshot.candles} closed candles.",
        (f"Last close: {snapshot.close:.4f}", f"Trend: {snapshot.trend}"),
    ))
    # Agent 2: technical analyst
    bullish = snapshot.trend == "bullish" and snapshot.momentum == "positive"
    bearish = snapshot.trend == "bearish" and snapshot.momentum == "negative"
    technical_status = "long_candidate" if bullish else "no_setup"
    reports.append(AgentReport(
        "technical_analyst", technical_status,
        "Trend and momentum align." if bullish else "No sufficiently aligned long setup.",
        (f"EMA20: {snapshot.ema_fast:.4f}", f"EMA50: {snapshot.ema_slow:.4f}",
         f"RSI14: {snapshot.rsi:.2f}", f"ATR: {snapshot.atr_pct:.2f}%"),
    ))
    # Agent 3: regime analyst
    regime_ok = snapshot.trend != "sideways" and snapshot.volatility != "high"
    reports.append(AgentReport(
        "market_regime", "favorable" if regime_ok else "unfavorable",
        "Directional regime with acceptable measured volatility." if regime_ok else
        "Regime is sideways or volatility is too high.",
        (f"Trend: {snapshot.trend}", f"Volatility: {snapshot.volatility} ({snapshot.atr_pct:.2f}% ATR)"),
    ))
    # Agent 4: news analyst. No feed is connected at this stage; abstain explicitly.
    reports.append(AgentReport(
        "news_analyst", "unavailable",
        "No verified news source is connected; no news-based conclusion is made.",
        ("News risk is unknown, not assumed to be zero.",),
    ))

    proposal = None
    if bullish and regime_ok:
        stop = snapshot.close - 1.5 * (snapshot.close * snapshot.atr_pct / 100)
        target = snapshot.close + 3.0 * (snapshot.close * snapshot.atr_pct / 100)
        proposal = TradeProposal(
            snapshot.symbol, "LONG", snapshot.close, stop, target,
            ("EMA20 above EMA50", "RSI indicates positive but not overbought momentum",
             "Market regime is directional and volatility is not classified as high"),
        )
        objections = (
            "News catalyst and event risk are unverified because no news feed is connected.",
            f"RSI is {snapshot.rsi:.2f}; momentum can weaken before the stop is reached.",
            f"ATR is {snapshot.atr_pct:.2f}% of price; the proposed stop can still be crossed by a volatility shock.",
        )
        reports.append(AgentReport(
            "counterargument", "objections_raised",
            "Three evidence-based objections recorded; proposal is not automatically rejected.",
            (f"Candidate entry: {snapshot.close:.4f}",), objections,
        ))
    else:
        reports.append(AgentReport(
            "counterargument", "no_trade_to_challenge",
            "No trade proposal exists, so no fabricated objections were generated.",
            ("Technical and regime conditions did not jointly pass.",),
        ))
    return reports, proposal


def analyze_symbol(symbol: str, rows: Sequence[Sequence[float]]) -> TeamDecision:
    snapshot = analyze_candles(symbol, rows)
    reports, proposal = _reports(snapshot)
    # Hard risk gate. This is a deterministic veto and is not delegated to the chief trader.
    risk_reasons: list[str] = []
    if proposal is None:
        risk_reasons.append("No valid proposal to assess.")
    if snapshot.volatility == "high":
        risk_reasons.append("High volatility veto.")
    if proposal and not (0 < proposal.stop_loss < proposal.reference_price < proposal.take_profit):
        risk_reasons.append("Invalid stop/target geometry.")
    risk_approved = proposal is not None and not risk_reasons
    reports.append(AgentReport(
        "risk_manager", "approved" if risk_approved else "veto",
        "Hard risk checks passed." if risk_approved else "Trade blocked by hard risk gate.",
        tuple(risk_reasons or ["Proposal geometry valid; account-level limits are checked by PaperBroker at execution."]),
    ))
    # Chief trader is the only final decision-maker, but cannot override risk veto.
    if not proposal:
        decision, reason = "NO_TRADE", "No qualifying technical proposal."
    elif not risk_approved:
        decision, reason = "NO_TRADE", "Risk manager vetoed the proposal."
    elif len(next((r.objections for r in reports if r.agent == "counterargument"), ())) < 3:
        decision, reason = "NO_TRADE", "Counterargument agent did not provide three objections."
    else:
        decision, reason = "WATCH", "Proposal passed initial gates; awaiting account-level sizing and execution stage."
    return TeamDecision(
        decision, reason, asdict(snapshot),
        tuple(asdict(report) for report in reports),
        asdict(proposal) if proposal else None, risk_approved,
    )
