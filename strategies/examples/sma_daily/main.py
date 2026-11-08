# QuantLab example strategy: daily SMA crossover on a single symbol from a QuantLab dataset.
#
# Run with:  quantlab backtest run sma-daily --dataset <your-daily-equity-dataset> -p symbol=SPY
# QuantLab exports datasets as raw prices with neutral factor files, so the algorithm requests RAW
# normalization.
# Illustrative only; not a recommendation.
from AlgorithmImports import *  # noqa: F403


class SmaDaily(QCAlgorithm):  # noqa: F405
    def initialize(self):
        # LEAN start/end dates are exchange-local calendar dates.
        start = [int(x) for x in (self.get_parameter("start") or "2020-01-02").split("-")]
        end = [int(x) for x in (self.get_parameter("end") or "2024-12-31").split("-")]
        self.set_start_date(*start)
        self.set_end_date(*end)
        self.set_cash(100_000)

        fast = int(self.get_parameter("fast") or 20)
        slow = int(self.get_parameter("slow") or 50)
        if fast >= slow:
            raise ValueError(f"fast ({fast}) must be smaller than slow ({slow})")

        ticker = self.get_parameter("symbol") or "SPY"
        raw = DataNormalizationMode.RAW  # noqa: F405
        equity = self.add_equity(ticker, Resolution.DAILY, data_normalization_mode=raw)  # noqa: F405
        self.symbol = equity.symbol
        self.set_benchmark(self.symbol)  # default benchmark (SPY) may not be in the dataset
        self.fast = self.sma(self.symbol, fast, Resolution.DAILY)  # noqa: F405
        self.slow = self.sma(self.symbol, slow, Resolution.DAILY)  # noqa: F405
        self.set_warm_up(slow, Resolution.DAILY)  # noqa: F405

    def on_data(self, data):
        if self.is_warming_up or not self.slow.is_ready:
            return
        invested = self.portfolio[self.symbol].invested
        if self.fast.current.value > self.slow.current.value and not invested:
            self.set_holdings(self.symbol, 1.0)
        elif self.fast.current.value < self.slow.current.value and invested:
            self.liquidate(self.symbol)
