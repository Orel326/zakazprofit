# Historical replay protocol — preregistration v1

This protocol is frozen before inspecting any new replay returns. Existing forward experiment is independent and unchanged. Daily UTC spot candles only; no leverage, shorting or live orders.

## Data and partitions

Development: 2021-11-01 through 2023-10-31. Selection: 2023-11-01 through 2025-10-31, eight consecutive three-calendar-month folds. Final holdout: 2025-11-01 through 2026-10-31, locked until implementation, candidate registry, selection rule and selected candidate commit are frozen. Earlier candles may warm indicators but never contribute to later fold P&L. Each fold starts in cash. No forced terminal exit counted as a natural trade; mark open positions to market and report separately.

The ten-symbol retrospective cohort is exploratory only. First observed candle is not a verified listing date. A separate historical universe must use archived listing/delisting evidence or explicitly label an archive-derived proxy; never infer membership from future availability. Missing candles are not forward-filled for signals or execution. Report coverage, unresolved gaps and unavailable exit prices; invalidate affected fold metrics rather than silently liquidating at stale prices. No claims of survivorship-free results until independently verified.

## Fixed candidate budget

Four new hypotheses, one specification each; no parameter grid:
- Regime: BTC close above its 200-day SMA and asset close above its 50-day SMA; exit when either condition fails.
- Shock reversal: asset daily return <= -8%, BTC above its 200-day SMA; enter after signal, exit after five held daily sessions or when close recovers to pre-shock close, whichever first.
- Relative strength: positive trailing 90-day return; rank by that return, hold top eligible assets, rebalance weekly (Monday UTC close).
- Volatility/volume: close above prior 20-day high, volume above twice prior 20-day median, ATR(14)/close <= 0.06; exit on close below prior 10-day low.

Use only completed candles, including for cross-sectional ranks. Ties lexicographic. No signal until full contiguous lookback exists. Each hypothesis uses dynamic five slots; no additional variants without a new documented research round and independent untouched evaluation data. Compare existing EMA/ADX implementation and fixed 2/5/10 and dynamic 3/5 baseline configurations, cash and equal-weight buy-and-hold (same eligible universe and costs). Record exact baseline definitions and any daily-vs-intraday incompatibility; do not silently substitute.

## Execution and selection

Signal at close, earliest fill next UTC daily open; no same-bar close fills. Cash ledger, long-only, no negative cash, no leverage. Apply existing research risk and correlation limits without relaxing them; record exact values and any incompatibility before running. Apply commission and adverse slippage on both sides; repeat at double costs. Record all attempted candidates, failed tests and invalid folds. Report net return, drawdown, natural closed trades, PF, turnover, exposure, costs, benchmark excess return and 90-day rolling return distribution. PF undefined when no losses; report separately, never treat as automatic pass.

Development is for implementation validation, not parameter search. Selection uses only the eight fixed folds: require at least 30 natural closed trades in aggregate, aggregate PF >= 1.5, worst fold drawdown <= 10%, positive aggregate net return under doubled costs and positive median fold excess return over cash and matched buy-and-hold. Rank passing candidates by median fold net return under doubled costs, then lower worst drawdown, then candidate ID. If none pass, record no candidate; do not open holdout. Target 10–15% net per 90 days is aspirational, not a tuning objective. Final holdout is one evaluation, never a feedback loop.

Required tests before interpreting results: prefix invariance under future-data mutation, next-open execution and gap handling, cash conservation with costs, no trading on missing candles, risk and correlation limits, deterministic tie handling, fold boundary isolation, and holdout access denial. CI must finish successfully and artifacts must be inspected before any result is declared verified.
