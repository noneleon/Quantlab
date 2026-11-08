# Test strategy for `quantlab paper deploy` with Python (KI-0078): same behaviour as DeployProbe.cs — buys
# 1 SPY share on the first price it receives from QuantLab (QuantLabDataQueue — synthetic quotes in tests),
# then stops. No seeded prices.
from AlgorithmImports import *  # noqa: F403


class DeployProbe(QCAlgorithm):  # noqa: F405
    def initialize(self):
        import os
        import socket

        port = os.environ.get("QUANTLAB_TEST_HOST_PORT")  # isolation test (KI-0082): try to reach the host
        if port:
            probe = socket.socket()
            probe.settimeout(2)
            reached = probe.connect_ex(("127.0.0.1", int(port))) == 0
            self.log(f"DEPLOY host network {'REACHABLE' if reached else 'unreachable'}")
        self.set_cash(100000)
        self._spy = self.add_equity("SPY", Resolution.SECOND).symbol  # noqa: F405
        self._sent = False

    def on_data(self, slice):
        if self._sent or not slice.contains_key(self._spy):
            return
        self._sent = True
        self.log(f"DEPLOY first price {self.securities[self._spy].price}")
        self.market_order(self._spy, 1, tag="quantlab deploy probe")

    def on_order_event(self, order_event):
        status = "Filled" if order_event.status == OrderStatus.FILLED else str(order_event.status)  # noqa: F405
        self.log(  # same text as DeployProbe.cs, so one test checks both
            f"DEPLOY order event {order_event.order_id} {status} "
            f"qty {order_event.fill_quantity:g} @ {order_event.fill_price}"
        )
        if order_event.status in (OrderStatus.FILLED, OrderStatus.INVALID):  # noqa: F405
            self.quit("deploy probe complete")
