"""Objective and constraint expressions (OPT-006) — parsed with a small allow-list, never ``eval``.

Objective: an arithmetic expression over trial metrics, always **maximised**, e.g. ``sharpe``,
``cagr - 0.5*max_drawdown``, ``-max_drawdown``. Allowed: metric names, numbers, ``+ - * /``, unary minus,
parentheses,
``abs()``, ``min()``, ``max()``. Constraint: ``<metric expression> <op> <number>`` with ``<= < >= > ==``, e.g.
``max_drawdown <= 0.25`` or ``trades >= 20``. A trial whose expression needs a missing metric has no objective
value (it can never be selected as best).
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

METRIC_NAMES = frozenset(
    {
        "total_return", "cagr", "annual_volatility", "sharpe", "sortino", "calmar", "max_drawdown",
        "max_drawdown_duration_days", "best_day", "worst_day", "start_equity", "end_equity",
        "trades", "win_rate", "profit_factor", "expectancy", "net_pnl",
    }
)  # fmt: skip
_BINOPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_CMP: dict[str, Callable[[float, float], bool]] = {
    "<=": operator.le,
    "<": operator.lt,
    ">=": operator.ge,
    ">": operator.gt,
    "==": operator.eq,
}
_FUNCS: dict[str, Callable[..., float]] = {"abs": abs, "min": min, "max": max}


class ExpressionError(ValueError):
    pass


class _Missing(Exception):
    pass


def _compile(node: ast.AST) -> Callable[[Mapping[str, Any]], float]:
    if isinstance(node, ast.Expression):
        return _compile(node.body)
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ):
        value = float(node.value)
        return lambda m: value
    if isinstance(node, ast.Name):
        if node.id not in METRIC_NAMES:
            raise ExpressionError(f"unknown metric {node.id!r}; known: {sorted(METRIC_NAMES)}")
        name = node.id

        def lookup(m: Mapping[str, Any]) -> float:
            v = m.get(name)
            if v is None:
                raise _Missing(name)
            return float(v)

        return lookup
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        inner = _compile(node.operand)
        sign = -1.0 if isinstance(node.op, ast.USub) else 1.0
        return lambda m: sign * inner(m)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left, right, op = _compile(node.left), _compile(node.right), _BINOPS[type(node.op)]
        return lambda m: op(left(m), right(m))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and not node.keywords
        and node.args
    ):
        fn = _FUNCS[node.func.id]
        args = [_compile(a) for a in node.args]
        return lambda m: float(fn(*(a(m) for a in args)))
    raise ExpressionError(f"not allowed in an objective: {ast.dump(node)[:80]}")


@dataclass(frozen=True)
class Objective:
    expression: str
    _fn: Callable[[Mapping[str, Any]], float]

    @classmethod
    def parse(cls, expression: str) -> Objective:
        if not expression.strip() or len(expression) > 300:
            raise ExpressionError("objective must be a non-empty expression of at most 300 characters")
        try:
            tree = ast.parse(expression.strip(), mode="eval")
        except SyntaxError as exc:
            raise ExpressionError(f"invalid objective {expression!r}: {exc.msg}") from exc
        return cls(expression.strip(), _compile(tree))

    def evaluate(self, metrics: Mapping[str, Any]) -> float | None:
        try:
            value = self._fn(metrics)
        except (_Missing, ZeroDivisionError):
            return None
        return value if value == value and abs(value) != float("inf") else None


@dataclass(frozen=True)
class Constraint:
    text: str
    left: Objective
    op: str
    bound: float

    @classmethod
    def parse(cls, text: str) -> Constraint:
        for op in ("<=", ">=", "==", "<", ">"):
            if op in text:
                lhs, _, rhs = text.partition(op)
                try:
                    bound = float(rhs.strip())
                except ValueError as exc:
                    raise ExpressionError(f"constraint bound must be a number: {text!r}") from exc
                return cls(text.strip(), Objective.parse(lhs), op, bound)
        raise ExpressionError(f"constraint needs one of <= < >= > ==: {text!r}")

    def holds(self, metrics: Mapping[str, Any]) -> bool | None:
        value = self.left.evaluate(metrics)
        return None if value is None else _CMP[self.op](value, self.bound)
