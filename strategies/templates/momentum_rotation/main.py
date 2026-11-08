# QuantLab strategy template: monthly momentum rotation (STR-005).
#
# Each month, hold the `top` symbols with the highest `lookback`-day rate of change, equally weighted;
# symbols with negative momentum are not held (cash instead).
# Needs a daily dataset containing every symbol in `symbols`.
#   quantlab backtest run momentum --dataset <daily-equities> -p symbols=SPY,QQQ,IWM,TLT -m fee=constant ...
# Template for research; illustrative only, not a recommendation.
# Not yet verified on a real LEAN run (KI-0050).
from AlgorithmImports import *  # noqa: F403

try:  # QuantLab execution models (fees, slippage, fills, settlement); absent outside QuantLab
    import quantlab_lean
except ImportError:  # pragma: no cover
    quantlab_lean = None


class MomentumRotation(QCAlgorithm):  # noqa: F405
    def initialize(self):
        if quantlab_lean is not None:
            quantlab_lean.configure(self)  # before adding securities
        start = [int(x) for x in (self.get_parameter("start") or "2015-01-02").split("-")]
        end = [int(x) for x in (self.get_parameter("end") or "2024-12-31").split("-")]
        self.set_start_date(*start)
        self.set_end_date(*end)
        self.set_cash(100_000)

        self.lookback = int(self.get_parameter("lookback") or 126)
        self.top = int(self.get_parameter("top") or 2)
        tickers = [t.strip().upper() for t in (self.get_parameter("symbols") or "SPY,QQQ,IWM,TLT").split(",")]
        if self.top < 1 or self.top > len(tickers):
            raise ValueError(f"top must be between 1 and {len(tickers)}")

        raw = DataNormalizationMode.RAW  # noqa: F405 - QuantLab exports raw prices + factor files
        self.momentum = {}
        for ticker in tickers:
            symbol = self.add_equity(ticker, Resolution.DAILY, data_normalization_mode=raw).symbol  # noqa: F405
            self.momentum[symbol] = self.roc(symbol, self.lookback, Resolution.DAILY)  # noqa: F405
        first = next(iter(self.momentum))
        self.set_benchmark(first)
        self.set_warm_up(self.lookback + 1, Resolution.DAILY)  # noqa: F405
        self.schedule.on(
            self.date_rules.month_start(first), self.time_rules.after_market_open(first, 30), self.rebalance
        )

    def rebalance(self):
        if self.is_warming_up:
            return
        ready = {s: ind.current.value for s, ind in self.momentum.items() if ind.is_ready}
        ranked = sorted((s for s, v in ready.items() if v > 0), key=lambda s: ready[s], reverse=True)
        chosen = set(ranked[: self.top])
        for symbol in self.momentum:
            if symbol not in chosen and self.portfolio[symbol].invested:
                self.liquidate(symbol)
        for symbol in chosen:
            self.set_holdings(symbol, 1.0 / self.top)
