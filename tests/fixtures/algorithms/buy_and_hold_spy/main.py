# Minimal LEAN Python algorithm used by the Docker-gated integration test.
# Uses SPY minute data from LEAN's bundled sample dataset (2013-10-07 .. 2013-10-11).
from AlgorithmImports import *  # noqa: F403


class BuyAndHoldSpy(QCAlgorithm):  # noqa: F405
    def initialize(self):
        self.set_start_date(2013, 10, 7)
        self.set_end_date(2013, 10, 11)
        self.set_cash(100000)
        self.spy = self.add_equity("SPY", Resolution.MINUTE).symbol  # noqa: F405

    def on_data(self, data):
        if not self.portfolio.invested:
            self.set_holdings(self.spy, 1.0)
