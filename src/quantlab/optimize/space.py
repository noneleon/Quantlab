"""Parameter spaces (OPT-001): explicit grids and seeded random sampling.

Spec syntax (CLI ``-s``): ``name=a,b,c`` (list) or ``name=start:stop:step`` (inclusive numeric range). Values
stay
strings for LEAN (``get_parameter`` semantics); integer-looking ranges produce integers.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass

MAX_TRIALS = 10_000


class SpaceError(ValueError):
    pass


def _fmt(x: float, integer: bool) -> str:
    return str(round(x)) if integer else repr(round(x, 10))


@dataclass(frozen=True)
class Dimension:
    name: str
    values: tuple[str, ...]

    @classmethod
    def parse(cls, spec: str) -> Dimension:
        name, sep, rhs = spec.partition("=")
        name, rhs = name.strip(), rhs.strip()
        if not sep or not name or not rhs:
            raise SpaceError(f"parameter space must be name=values, got {spec!r}")
        if ":" in rhs:
            parts = rhs.split(":")
            if len(parts) != 3:
                raise SpaceError(f"range must be start:stop:step, got {rhs!r}")
            try:
                start, stop, step = (float(p) for p in parts)
            except ValueError as exc:
                raise SpaceError(f"range bounds must be numbers: {rhs!r}") from exc
            if step <= 0 or stop < start:
                raise SpaceError(f"invalid range {rhs!r} (need step > 0, stop >= start)")
            integer = all(p.strip().lstrip("-").isdigit() for p in parts)
            count = int((stop - start) / step + 1e-9) + 1
            if count > MAX_TRIALS:
                raise SpaceError(f"range {rhs!r} has {count} values (max {MAX_TRIALS})")
            values = tuple(dict.fromkeys(_fmt(start + i * step, integer) for i in range(count)))
        else:
            values = tuple(dict.fromkeys(v.strip() for v in rhs.split(",") if v.strip()))
        if not values:
            raise SpaceError(f"no values for {name!r}")
        return cls(name, values)


@dataclass(frozen=True)
class Space:
    dimensions: tuple[Dimension, ...]

    @classmethod
    def parse(cls, specs: list[str]) -> Space:
        dims = tuple(Dimension.parse(s) for s in specs)
        names = [d.name for d in dims]
        if not dims:
            raise SpaceError("at least one parameter dimension is required")
        if len(set(names)) != len(names):
            raise SpaceError(f"duplicate parameter in space: {names}")
        return cls(dims)

    @property
    def size(self) -> int:
        n = 1
        for d in self.dimensions:
            n *= len(d.values)
        return n

    def grid(self) -> list[dict[str, str]]:
        if self.size > MAX_TRIALS:
            raise SpaceError(f"grid has {self.size} combinations (max {MAX_TRIALS}); use random sampling")
        names = [d.name for d in self.dimensions]
        return [
            dict(zip(names, combo, strict=True))
            for combo in itertools.product(*(d.values for d in self.dimensions))
        ]

    def sample(self, n: int, seed: int) -> list[dict[str, str]]:
        """``n`` distinct points drawn uniformly from the grid (all of them if the grid is smaller)."""
        if n < 1:
            raise SpaceError("random search needs n >= 1")
        rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not cryptography
        if self.size <= n:
            return self.grid()
        chosen: set[int] = set()
        while len(chosen) < n:
            chosen.add(rng.randrange(self.size))
        out = []
        for index in sorted(chosen):
            point = {}
            for d in reversed(self.dimensions):
                index, pos = divmod(index, len(d.values))
                point[d.name] = d.values[pos]
            out.append({d.name: point[d.name] for d in self.dimensions})
        return out

    def to_dict(self) -> dict[str, list[str]]:
        return {d.name: list(d.values) for d in self.dimensions}
