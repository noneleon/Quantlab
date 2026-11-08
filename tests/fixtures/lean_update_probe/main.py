# Test strategy for order updates through QuantLab (OMS-006): places a limit buy far below the market,
# then updates it (quantity 2, marketable limit) through LEAN's own UpdateOrder path; QuantLab's risk gate
# checks the update and the paper broker fills the new terms. Synthetic quotes in tests.
# ruff: noqa: F403, F405
from AlgorithmImports import *

NAMES = {
    OrderStatus.SUBMITTED: "Submitted",
    OrderStatus.UPDATE_SUBMITTED: "UpdateSubmitted",
    OrderStatus.FILLED: "Filled",
    OrderStatus.CANCELED: "Canceled",
    OrderStatus.INVALID: "Invalid",
}


class UpdateProbe(QCAlgorithm):
    def initialize(self):
        self.set_cash(100000)
        self._spy = self.add_equity("SPY", Resolution.SECOND).symbol
        self._ticket = None
        self._updated = False

    def on_data(self, slice):
        if self._ticket is None and slice.contains_key(self._spy):
            self._ticket = self.limit_order(self._spy, 1, 40.0)

    def on_order_event(self, e):
        name = NAMES.get(e.status, str(e.status))
        self.log(f"UPDATE event {e.order_id} {name} qty {e.fill_quantity:g} @ {e.fill_price}")
        if e.status == OrderStatus.SUBMITTED and not self._updated:
            self._updated = True
            fields = UpdateOrderFields()
            fields.quantity = 2
            fields.limit_price = 51.0
            response = self._ticket.update(fields)
            self.log(f"UPDATE response {response.is_success}")
        if e.status in (OrderStatus.FILLED, OrderStatus.INVALID, OrderStatus.CANCELED):
            self.quit("update probe complete")
