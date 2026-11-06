# QuantLab example strategy: simple moving-average crossover on SPY.
#
# Runs on LEAN's bundled *sample* data (SPY minute bars, 2013-10-07 .. 2013-10-11), so results are
# labelled `sample` by QuantLab and are not a performance claim. Parameters come from strategy.toml
# (overridable per run) via LEAN's get_parameter.
from AlgorithmImports import *  # noqa: F403


class SmaCrossover(QCAlgorithm):  # noqa: F405
    def initialize(self):
        self.set_start_date(2013, 10, 7)
        self.set_end_date(2013, 10, 11)
        self.set_cash(100_000)

        fast = int(self.get_parameter("fast") or 10)
        slow = int(self.get_parameter("slow") or 30)
        if fast >= slow:
            raise ValueError(f"fast ({fast}) must be smaller than slow ({slow})")

        self.symbol = self.add_equity("SPY", Resolution.MINUTE).symbol  # noqa: F405
        self.fast = self.sma(self.symbol, fast, Resolution.MINUTE)  # noqa: F405
        self.slow = self.sma(self.symbol, slow, Resolution.MINUTE)  # noqa: F405
        self.set_warm_up(slow, Resolution.MINUTE)  # noqa: F405

    def on_data(self, data):
        if self.is_warming_up or not self.slow.is_ready:
            return
        invested = self.portfolio[self.symbol].invested
        if self.fast.current.value > self.slow.current.value and not invested:
            self.set_holdings(self.symbol, 1.0)
        elif self.fast.current.value < self.slow.current.value and invested:
            self.liquidate(self.symbol)
