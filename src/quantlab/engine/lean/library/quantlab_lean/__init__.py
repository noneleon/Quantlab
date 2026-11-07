"""QuantLab helper library for LEAN Python algorithms (ENG-009).

Runs **inside the LEAN container**; it must not import ``quantlab`` (which is not installed there). QuantLab
copies this package next to the run, writes ``models.json`` beside this file and adds the folder to LEAN's
``python-additional-paths``. A strategy opts in with two lines at the top of ``initialize``::

    import quantlab_lean
    quantlab_lean.configure(self)      # before add_equity/add_* calls

``configure`` applies the execution models chosen in QuantLab (``[backtest.models]`` / ``-m key=value``):
brokerage model + account type, then a security initializer (LEAN's documented
``BrokerageModelSecurityInitializer`` pattern, with last-known-price seeding) that overrides fee,
slippage, fill and settlement models where QuantLab asks for it ("brokerage" keeps what LEAN's
brokerage model assigns). It logs ``QUANTLAB_MODELS_APPLIED sha256=<hash>`` so QuantLab can confirm the
models were really applied; a strategy that never calls ``configure`` runs with LEAN's defaults and
QuantLab reports that.

LEAN API names used here were checked against LEAN source (QuantConnect/Lean cc98c42, 2026-11-06):
``ConstantFeeModel(fee)``, ``ConstantSlippageModel(percent)``, ``VolumeShareSlippageModel(volume_limit,
price_impact)``, ``ImmediateFillModel()``, ``DelayedSettlementModel(days, time_of_day)``,
``ImmediateSettlementModel()``, ``BrokerageModelSecurityInitializer(model, seeder)``, ``FuncSecuritySeeder``,
``SetBrokerageModel(BrokerageName, AccountType)``. The snake_case spellings are LEAN's Python conventions.
"""

import json
import os
from datetime import timedelta

from AlgorithmImports import (  # type: ignore[import-not-found]
    AccountType,
    BrokerageModelSecurityInitializer,
    BrokerageName,
    ConstantFeeModel,
    ConstantSlippageModel,
    DelayedSettlementModel,
    FuncSecuritySeeder,
    ImmediateFillModel,
    ImmediateSettlementModel,
    VolumeShareSlippageModel,
)

MARKER = "QUANTLAB_MODELS_APPLIED"
MODELS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models.json")

BROKERAGES = {"default": "DEFAULT", "interactive_brokers": "INTERACTIVE_BROKERS_BROKERAGE"}
ACCOUNT_TYPES = {"margin": "MARGIN", "cash": "CASH"}


def load_models(path=None):
    """The models QuantLab recorded for this run: {"sha256": ..., "models": {...}}."""
    with open(path or MODELS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    if "models" not in data or "sha256" not in data:
        raise ValueError("quantlab_lean: models.json is malformed")
    return data


def fee_model(m):
    if m["fee"] == "zero":
        return ConstantFeeModel(0)
    if m["fee"] == "constant":
        return ConstantFeeModel(m["fee_per_order"])
    return None


def slippage_model(m):
    if m["slippage"] == "none":
        return ConstantSlippageModel(0)
    if m["slippage"] == "constant":
        return ConstantSlippageModel(m["slippage_percent"])
    if m["slippage"] == "volume_share":
        return VolumeShareSlippageModel(m["volume_limit"], m["price_impact"])
    return None


def fill_model(m):
    return ImmediateFillModel() if m["fill"] == "immediate" else None


def settlement_model(m):
    if m["settlement"] == "immediate":
        return ImmediateSettlementModel()
    if m["settlement"] == "delayed":
        return DelayedSettlementModel(m["settlement_days"], timedelta(hours=m["settlement_hour"]))
    return None


class QuantLabSecurityInitializer(BrokerageModelSecurityInitializer):
    """Brokerage defaults first, then QuantLab's overrides (a new model instance per security)."""

    def __init__(self, brokerage_model, security_seeder, models):
        super().__init__(brokerage_model, security_seeder)
        self._models = models

    def initialize(self, security):
        super().initialize(security)
        m = self._models
        for setter, factory in (
            (security.set_fee_model, fee_model),
            (security.set_slippage_model, slippage_model),
            (security.set_fill_model, fill_model),
            (security.set_settlement_model, settlement_model),
        ):
            model = factory(m)
            if model is not None:
                setter(model)


def describe(m):
    return ", ".join(f"{k}={m[k]}" for k in sorted(m))


def configure(algorithm, path=None):
    """Apply QuantLab's execution models to ``algorithm``; call before adding securities."""
    data = load_models(path)
    m = data["models"]
    algorithm.set_brokerage_model(
        getattr(BrokerageName, BROKERAGES[m["brokerage"]]),
        getattr(AccountType, ACCOUNT_TYPES[m["account_type"]]),
    )
    seeder = FuncSecuritySeeder(algorithm.get_last_known_prices)
    algorithm.set_security_initializer(QuantLabSecurityInitializer(algorithm.brokerage_model, seeder, m))
    algorithm.log(f"{MARKER} sha256={data['sha256']} {describe(m)}")
    return m
