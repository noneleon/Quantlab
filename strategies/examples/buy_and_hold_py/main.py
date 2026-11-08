# Same logic as LEAN's BasicTemplateAlgorithm, so a run on LEAN's bundled sample data must reproduce LEAN's
# published statistics exactly (golden check for Python on the native launcher,
# tests/integration/test_lean_native.py). Sample data: results are labelled `sample`, not a performance claim.
from AlgorithmImports import *  # noqa: F403


class BuyAndHold(QCAlgorithm):  # noqa: F405
    def initialize(self):
        self.set_start_date(2013, 10, 7)
        self.set_end_date(2013, 10, 11)
        self.set_cash(100000)
        self._spy = self.add_equity("SPY", Resolution.MINUTE).symbol  # noqa: F405

    def on_data(self, data):
        if not self.portfolio.invested:
            self.set_holdings(self._spy, 1)
