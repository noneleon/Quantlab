"""Recording stand-in for LEAN's ``AlgorithmImports`` (tests only; NOT LEAN).

Classes record their constructor arguments so tests can assert which LEAN models ``quantlab_lean``
would create.
Names mirror the LEAN API checked in LEAN source (see quantlab_lean docstring); behaviour is not simulated.
"""

CALLS = []


class _Model:
    def __init__(self, *args):
        self.args = args
        CALLS.append((type(self).__name__, args))


class ConstantFeeModel(_Model): ...


class ConstantSlippageModel(_Model): ...


class VolumeShareSlippageModel(_Model): ...


class ImmediateFillModel(_Model): ...


class ImmediateSettlementModel(_Model): ...


class DelayedSettlementModel(_Model): ...


class FuncSecuritySeeder(_Model): ...


class BrokerageName:
    DEFAULT = "BrokerageName.DEFAULT"
    INTERACTIVE_BROKERS_BROKERAGE = "BrokerageName.INTERACTIVE_BROKERS_BROKERAGE"


class AccountType:
    MARGIN = "AccountType.MARGIN"
    CASH = "AccountType.CASH"


class BrokerageModelSecurityInitializer:
    def __init__(self, brokerage_model, security_seeder):
        self.brokerage_model = brokerage_model
        self.security_seeder = security_seeder

    def initialize(self, security):
        security.events.append(("brokerage_defaults", self.brokerage_model))


class StubSecurity:
    def __init__(self):
        self.events = []

    def set_fee_model(self, m):
        self.events.append(("fee", type(m).__name__, m.args))

    def set_slippage_model(self, m):
        self.events.append(("slippage", type(m).__name__, m.args))

    def set_fill_model(self, m):
        self.events.append(("fill", type(m).__name__, m.args))

    def set_settlement_model(self, m):
        self.events.append(("settlement", type(m).__name__, m.args))


class StubAlgorithm:
    def __init__(self):
        self.brokerage_model = None
        self.initializer = None
        self.logs = []

    def set_brokerage_model(self, name, account):
        self.brokerage_model = (name, account)

    def set_security_initializer(self, initializer):
        self.initializer = initializer

    def get_last_known_prices(self, security):
        return []

    def log(self, message):
        self.logs.append(message)


class QCAlgorithm:  # base class name only, so templates can be imported for their pure helpers
    pass


class DataNormalizationMode:
    RAW = "DataNormalizationMode.RAW"


class Resolution:
    DAILY = "Resolution.DAILY"
    MINUTE = "Resolution.MINUTE"


class MovingAverageType:
    WILDERS = "MovingAverageType.WILDERS"
