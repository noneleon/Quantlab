# QuantLab strategy template: RSI mean reversion on one symbol (STR-005).
#
# Long when RSI(`period`) falls below `entry`, exit when it rises above `exit` or after `max_days` sessions.
#   quantlab backtest run rsi --dataset <daily-equity> -p symbol=SPY \
#       -m slippage=constant -m slippage_percent=0.0005
# Template for research; illustrative only, not a recommendation.
# Not yet verified on a real LEAN run (KI-0050).
from AlgorithmImports import *  # noqa: F403

try:  # QuantLab execution models (fees, slippage, fills, settlement); absent outside QuantLab
    import quantlab_lean
except ImportError:  # pragma: no cover
    quantlab_lean = None


class RsiMeanReversion(QCAlgorithm):  # noqa: F405
    def initialize(self):
        if quantlab_lean is not None:
            quantlab_lean.configure(self)  # before adding securities
        start = [int(x) for x in (self.get_parameter("start") or "2015-01-02").split("-")]
        end = [int(x) for x in (self.get_parameter("end") or "2024-12-31").split("-")]
        self.set_start_date(*start)
        self.set_end_date(*end)
        self.set_cash(100_000)

        period = int(self.get_parameter("period") or 2)
        self.entry = float(self.get_parameter("entry") or 10)
        self.exit = float(self.get_parameter("exit") or 70)
        self.max_days = int(self.get_parameter("max_days") or 10)
        if not 0 < self.entry < self.exit < 100:
            raise ValueError("need 0 < entry < exit < 100")

        raw = DataNormalizationMode.RAW  # noqa: F405
        ticker = self.get_parameter("symbol") or "SPY"
        self.symbol = self.add_equity(ticker, Resolution.DAILY, data_normalization_mode=raw).symbol  # noqa: F405
        self.set_benchmark(self.symbol)
        self.indicator = self.rsi(self.symbol, period, MovingAverageType.WILDERS, Resolution.DAILY)  # noqa: F405
        self.set_warm_up(period * 10, Resolution.DAILY)  # noqa: F405
        self.held_days = 0

    def on_data(self, data):
        if self.is_warming_up or not self.indicator.is_ready or not data.contains_key(self.symbol):
            return
        value = self.indicator.current.value
        if not self.portfolio[self.symbol].invested:
            if value < self.entry:
                self.set_holdings(self.symbol, 1.0)
                self.held_days = 0
            return
        self.held_days += 1
        if value > self.exit or self.held_days >= self.max_days:
            self.liquidate(self.symbol)
