"""Value objects for automated design-space search (the constraint solver's vocabulary).

A design has knobs (`ParamRange` — named scalar dimensions the CAD code reads) and must
satisfy hard requirements (`Target` — mass under X, safety factor over Y) while optimising
one figure of merit (`Objective` — minimise mass among the feasible candidates). A
`SolveRequest` bundles the code, the searchable ranges, those targets, the objective, and a
budget; a `SolveResult` reports the best feasible `Candidate` plus every candidate tried.

Kept presentation-free and free of any CAD/toolkit dependency: the solver reads metrics out
of a `ValidationReport` the caller supplies, and these objects only speak in metric keys
(``mass.mass_g``, ``fea.safety_factor``, ``slicer.cost_usd``, ...) and plain scalars.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

# The comparison operators a `Target` may assert against a metric.
TargetOp = Literal["<=", ">=", "=="]
# Which way an `Objective` improves a metric among feasible candidates.
Direction = Literal["min", "max"]

# Absolute tolerance for the "==" operator, so floating-point metrics that land a hair off
# an exact target still count as satisfied. Deliberately tight — targets are engineering
# limits, not fuzzy goals.
_EQ_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class ParamRange:
    """One searchable dimension: the CAD code reads ``params[name]`` in ``[lo, hi]``.

    `step`, when set, discretises the axis (a grid); when None the axis is continuous and
    the solver samples it. `lo`/`hi` are inclusive bounds. A degenerate range (lo == hi)
    pins the param to a constant, which is a legitimate way to hold one knob fixed.
    """

    name: str
    lo: float
    hi: float
    step: float | None = None

    def __post_init__(self) -> None:
        if self.hi < self.lo:
            raise ValueError(f"ParamRange '{self.name}': hi ({self.hi}) < lo ({self.lo})")
        if self.step is not None and self.step <= 0:
            raise ValueError(f"ParamRange '{self.name}': step must be > 0, got {self.step}")

    def grid_values(self, samples: int) -> tuple[float, ...]:
        """Discretise the axis into ordered candidate values.

        With a `step`, walk ``lo, lo+step, ...`` up to (and including, within rounding) `hi`.
        Without a step, return `samples` evenly spaced points (>= 1); a degenerate range
        collapses to the single value `lo`.
        """
        if self.lo == self.hi:
            return (self.lo,)
        if self.step is not None:
            values: list[float] = []
            value = self.lo
            # Guard against float drift overshooting `hi` by a hair.
            while value <= self.hi + self.step * 1e-9:
                values.append(min(value, self.hi))
                value += self.step
            return tuple(values)
        count = max(samples, 1)
        if count == 1:
            return (self.lo,)
        span = self.hi - self.lo
        return tuple(self.lo + span * i / (count - 1) for i in range(count))


@dataclass(frozen=True, slots=True)
class Target:
    """A hard requirement on a validation metric, e.g. ``mass.mass_g <= 40``.

    `metric` is a ``ValidationReport.metrics`` key. A candidate is feasible only if every
    target holds; a target whose metric is absent from a report is treated as *not*
    satisfied (the solver cannot prove the requirement, so it must not claim feasibility).
    """

    metric: str
    op: TargetOp
    value: float

    def is_satisfied(self, metrics: Mapping[str, float]) -> bool:
        actual = metrics.get(self.metric)
        if actual is None:
            return False
        if self.op == "<=":
            return actual <= self.value
        if self.op == ">=":
            return actual >= self.value
        return abs(actual - self.value) <= _EQ_TOLERANCE

    def describe_violation(self, metrics: Mapping[str, float]) -> str:
        """One-line reason this target is unmet, for candidate diagnostics."""
        actual = metrics.get(self.metric)
        if actual is None:
            return f"{self.metric} missing (required {self.op} {self.value})"
        return f"{self.metric}={actual:.4g} violates {self.op} {self.value:.4g}"


@dataclass(frozen=True, slots=True)
class Objective:
    """The figure of merit optimised among feasible candidates (e.g. minimise mass).

    `metric` is a ``ValidationReport.metrics`` key; `direction` picks the better sign. A
    candidate missing the objective metric cannot be ranked and is never selected as best.
    """

    metric: str
    direction: Direction

    def is_better(self, candidate: float, incumbent: float) -> bool:
        """True if `candidate` beats `incumbent` under this objective's direction."""
        if self.direction == "min":
            return candidate < incumbent
        return candidate > incumbent


@dataclass(frozen=True, slots=True)
class SolveRequest:
    """A full search specification handed to `ConstraintSolver`.

    `max_evaluations` bounds the number of build+validate calls (the expensive part) so the
    search terminates predictably. `seed` derives every sample so a request is reproducible:
    the same request always evaluates the same candidates in the same order.
    """

    code: str
    ranges: tuple[ParamRange, ...]
    targets: tuple[Target, ...]
    objective: Objective
    max_evaluations: int = 32
    seed: int = 0
    # Fraction of the budget reserved for local refinement around the best feasible point;
    # the remainder funds the initial coarse sample. 0 disables refinement.
    refine_fraction: float = 0.35

    def __post_init__(self) -> None:
        if not self.ranges:
            raise ValueError("SolveRequest requires at least one ParamRange to search")
        if self.max_evaluations < 1:
            raise ValueError("max_evaluations must be >= 1")
        if not 0.0 <= self.refine_fraction < 1.0:
            raise ValueError("refine_fraction must be in [0.0, 1.0)")


@dataclass(frozen=True, slots=True)
class Candidate:
    """One evaluated point in the search: its params, resulting metrics, and verdict.

    `feasible` is True only when every `Target` held. `violations` lists the human-readable
    reasons an infeasible (or unevaluable) candidate failed — including "build failed" or a
    missing required metric — so the caller can explain why the search rejected it.
    """

    params: Mapping[str, float]
    metrics: Mapping[str, float]
    feasible: bool
    violations: tuple[str, ...] = field(default_factory=tuple)

    def objective_value(self, objective: Objective) -> float | None:
        """This candidate's score under `objective`, or None if the metric is absent."""
        return self.metrics.get(objective.metric)


@dataclass(frozen=True, slots=True)
class SolveResult:
    """The outcome of a search: the winning candidate (if any) and the full trail.

    `best` is None when no evaluated candidate was feasible. `evaluated` preserves every
    candidate in evaluation order so the search is auditable and reproducible.
    """

    best: Candidate | None
    evaluated: tuple[Candidate, ...]

    @property
    def feasible_count(self) -> int:
        return sum(1 for c in self.evaluated if c.feasible)
