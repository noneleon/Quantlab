import pytest

from quantlab.optimize.objective import Constraint, ExpressionError, Objective
from quantlab.optimize.space import Space, SpaceError


def test_objective_expressions():
    m = {"sharpe": 1.5, "max_drawdown": 0.2, "cagr": 0.1, "trades": 30}
    assert Objective.parse("sharpe").evaluate(m) == 1.5
    assert Objective.parse("cagr - 0.5*max_drawdown").evaluate(m) == pytest.approx(0.0)
    assert Objective.parse("-max_drawdown").evaluate(m) == -0.2
    assert Objective.parse("min(sharpe, 1) + abs(-cagr)").evaluate(m) == pytest.approx(1.1)
    assert Objective.parse("sortino").evaluate(m) is None  # missing metric -> no value
    assert Objective.parse("sharpe / (cagr - 0.1)").evaluate(m) is None  # division by zero -> no value


@pytest.mark.parametrize(
    "bad",
    [
        "__import__('os')",
        "sharpe.real",
        "open",
        "lambda: 1",
        "sharpe if 1 else 2",
        "[sharpe]",
        "",
        "x" * 301,
        "sharpe ** 2",
    ],
)
def test_objective_rejects_anything_but_arithmetic(bad):
    with pytest.raises(ExpressionError):
        Objective.parse(bad)


def test_constraints():
    m = {"max_drawdown": 0.3, "trades": 12}
    assert Constraint.parse("max_drawdown <= 0.25").holds(m) is False
    assert Constraint.parse("trades >= 10").holds(m) is True
    assert Constraint.parse("win_rate > 0.5").holds(m) is None
    with pytest.raises(ExpressionError):
        Constraint.parse("trades ~ 3")


def test_space_grid_random_and_ranges():
    space = Space.parse(["fast=5:20:5", "slow=50,100"])
    assert space.to_dict() == {"fast": ["5", "10", "15", "20"], "slow": ["50", "100"]}
    assert space.size == 8 and len(space.grid()) == 8 and space.grid()[1] == {"fast": "5", "slow": "100"}
    a, b = space.sample(3, seed=7), space.sample(3, seed=7)
    assert a == b and len(a) == 3 and all(p in space.grid() for p in a)
    assert len(space.sample(100, seed=1)) == 8
    assert Space.parse(["x=0.1:0.3:0.1"]).to_dict() == {"x": ["0.1", "0.2", "0.3"]}
    for bad in (["fast"], ["fast=1:2"], ["fast=5:1:1"], ["a=1", "a=2"], [], ["fast=1:100000:1"]):
        with pytest.raises(SpaceError):
            Space.parse(bad)
