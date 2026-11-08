"""RISK-006: a cash account buys only with settled cash (sale proceeds settle T+1). Synthetic quotes only."""

from datetime import UTC, date, datetime, timedelta

import pytest

from quantlab.oms.service import OrderRequest
from quantlab.oms.states import OrderState as S
from quantlab.risk.limits import load_limits, write_template
from quantlab.risk.settlement import settlement_date, unsettled_proceeds

from .test_oms_risk import LIMITS, Side, stack  # noqa: F401  (fixture)


@pytest.mark.parametrize(
    ("executed_at", "calendar", "expected"),
    [
        (datetime(2026, 11, 9, 15, tzinfo=UTC), "XNYS", date(2026, 11, 10)),  # Monday -> Tuesday
        (datetime(2026, 11, 13, 15, tzinfo=UTC), "XNYS", date(2026, 11, 16)),  # Friday -> Monday
        (datetime(2026, 11, 25, 15, tzinfo=UTC), "XNYS", date(2026, 11, 27)),  # day before Thanksgiving
        (datetime(2026, 11, 25, 15, tzinfo=UTC), "none", date(2026, 11, 26)),  # no exchange calendar
        (datetime(2026, 11, 10, 3, tzinfo=UTC), "XNYS", date(2026, 11, 10)),  # 22:00 Mon in New York
    ],
)
def test_settlement_dates(executed_at, calendar, expected):
    assert settlement_date(executed_at, 1, calendar) == expected


def test_unsettled_proceeds_until_the_settle_date():
    sale = (datetime(2026, 11, 13, 15, tzinfo=UTC), 1000.0)  # Friday
    saturday = datetime(2026, 11, 14, 15, tzinfo=UTC)
    monday = datetime(2026, 11, 16, 14, tzinfo=UTC)  # 09:00 New York: settled that morning
    assert unsettled_proceeds([sale], saturday, 1, "XNYS") == 1000.0
    assert unsettled_proceeds([sale], monday, 1, "XNYS") == 0.0
    assert unsettled_proceeds([sale], saturday, 0, "XNYS") == 0.0  # settlement_days = 0: settled at once


def test_the_template_and_older_files_get_t_plus_1(tmp_path):
    assert load_limits(write_template(tmp_path / "t.toml")).limits.settlement_days == 1
    old = tmp_path / "old.toml"
    old.write_text(LIMITS)  # a file written before RISK-006: the safe default applies
    assert load_limits(old).limits.settlement_days == 1


def test_sale_proceeds_buy_again_only_after_they_settle(stack):  # noqa: F811
    """Sell all, buy again the same day with the proceeds: refused (good-faith violation); next day ok."""
    roomy = (LIMITS.replace("max_order_quantity = 100", "max_order_quantity = 1000")
             .replace("max_order_notional = 5000.0", "max_order_notional = 100000.0")
             .replace("max_position_notional = 8000.0", "max_position_notional = 100000.0")
             .replace("max_gross_exposure_fraction = 0.5", "max_gross_exposure_fraction = 1.0"))  # fmt: skip
    stack["limits"].write_text(roomy)
    oms, clock = stack["oms"], stack["clock"]

    def order(key, qty, side=Side.BUY):
        return oms.submit(OrderRequest(key, "SPY", side, qty))

    assert order("all-in", 390).state is S.FILLED  # ~19 504 of 20 000 cash
    assert order("all-out", 390, Side.SELL).state is S.FILLED  # proceeds settle tomorrow
    again = order("same-day", 100)
    assert again.state is S.RISK_REJECTED
    (reason,) = [r for r in again.decision.reasons if r.startswith("buying power")]
    assert "unsettled sales 19" in reason and "T+1" in reason
    clock.advance(timedelta(days=1))  # Tuesday: settled
    assert order("next-day", 100).state is S.FILLED


def test_settlement_days_zero_restores_the_old_behaviour(stack):  # noqa: F811
    stack["limits"].write_text(LIMITS + "settlement_days = 0\n")
    oms = stack["oms"]
    assert oms.submit(OrderRequest("b", "SPY", Side.BUY, 50)).state is S.FILLED
    assert oms.submit(OrderRequest("s", "SPY", Side.SELL, 50)).state is S.FILLED
    assert oms.submit(OrderRequest("b2", "SPY", Side.BUY, 50)).state is S.FILLED
