# QuantLab strategy template: pairs trading on the z-score of a log-price spread (STR-005).
#
# Hedge ratio = OLS slope of log(a) on log(b) over `lookback` sessions; enter when |z| > `entry`, exit
# when |z| < `exit`. Market-neutral by notional (`gross` split between legs).
# Needs both symbols in one daily dataset.
#   quantlab backtest run pairs --dataset <daily-equities> -p a=KO -p b=PEP
# Cointegration is NOT tested here: verify the pair in research first.
# Template for research; illustrative only, not a recommendation.
# Not yet verified on a real LEAN run (KI-0050).
from collections import deque
from math import log

from AlgorithmImports import *  # noqa: F403

try:  # QuantLab execution models (fees, slippage, fills, settlement); absent outside QuantLab
    import quantlab_lean
except ImportError:  # pragma: no cover
    quantlab_lean = None


def hedge_ratio_and_z(xs, ys):
    """OLS slope of ys on xs and the z-score of the latest residual (pure Python, no LEAN dependency)."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return None, None
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / var
    spread = [y - beta * x for x, y in zip(xs, ys, strict=True)]
    ms = sum(spread) / n
    sd = (sum((s - ms) ** 2 for s in spread) / (n - 1)) ** 0.5
    if sd == 0:
        return beta, None
    return beta, (spread[-1] - ms) / sd


class PairsZScore(QCAlgorithm):  # noqa: F405
    def initialize(self):
        if quantlab_lean is not None:
            quantlab_lean.configure(self)  # before adding securities
        start = [int(x) for x in (self.get_parameter("start") or "2015-01-02").split("-")]
        end = [int(x) for x in (self.get_parameter("end") or "2024-12-31").split("-")]
        self.set_start_date(*start)
        self.set_end_date(*end)
        self.set_cash(100_000)

        self.lookback = int(self.get_parameter("lookback") or 60)
        self.entry = float(self.get_parameter("entry") or 2.0)
        self.exit = float(self.get_parameter("exit") or 0.5)
        self.gross = float(self.get_parameter("gross") or 1.0)
        if not 0 <= self.exit < self.entry:
            raise ValueError("need 0 <= exit < entry")

        raw = DataNormalizationMode.RAW  # noqa: F405
        a = self.get_parameter("a") or "KO"
        b = self.get_parameter("b") or "PEP"
        self.a = self.add_equity(a, Resolution.DAILY, data_normalization_mode=raw).symbol  # noqa: F405
        self.b = self.add_equity(b, Resolution.DAILY, data_normalization_mode=raw).symbol  # noqa: F405
        self.set_benchmark(self.a)
        self.log_a = deque(maxlen=self.lookback)
        self.log_b = deque(maxlen=self.lookback)
        self.side = 0  # +1 long spread (long a, short b), -1 short spread

    def on_data(self, data):
        if not (data.contains_key(self.a) and data.contains_key(self.b)):
            return
        bar_a, bar_b = data[self.a], data[self.b]
        if bar_a is None or bar_b is None or bar_a.close <= 0 or bar_b.close <= 0:
            return
        self.log_a.append(log(bar_a.close))
        self.log_b.append(log(bar_b.close))
        if len(self.log_a) < self.lookback:
            return
        beta, z = hedge_ratio_and_z(list(self.log_b), list(self.log_a))
        if beta is None or z is None:
            return
        if self.side == 0 and abs(z) > self.entry:
            self.side = -1 if z > 0 else 1
            weight = self.gross / (1 + abs(beta))
            self.set_holdings(self.a, self.side * weight)
            self.set_holdings(self.b, -self.side * weight * abs(beta))
        elif self.side != 0 and abs(z) < self.exit:
            self.liquidate()
            self.side = 0
