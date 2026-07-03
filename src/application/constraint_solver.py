"""Automated design-space search: pick params that meet the targets and optimise the goal.

Given a `SolveRequest` (searchable `ParamRange`s, hard `Target`s, and one `Objective`) this
runs a two-phase search over the param space:

1. **Coarse sample** — a deterministic space-filling scan (grid for stepped axes, otherwise
   a seeded scrambled sample) bounded by most of `max_evaluations`. Each point is built,
   validated, and scored against the targets.
2. **Local refinement** — coordinate descent (hill-climb) around the best feasible point
   found so far, spending the remaining budget nudging one axis at a time toward a better
   objective while staying feasible.

The build+validate step is *injected* as ``Callable[[Mapping[str, float]], ValidationReport
| None]`` so the solver never imports the toolkit or session: the MCP layer wires it to
``toolkit.build`` + ``toolkit.validate`` on a session. An evaluator that returns None (build
failed, or a solver binary was absent so a required metric is missing) is handled
gracefully — that candidate is recorded infeasible with a reason and the search continues.

Determinism is a hard requirement: every sample derives from `SolveRequest.seed`, so the
same request always evaluates the same candidates in the same order. No unseeded RNG.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping

import structlog

from domain.models.constraints import (
    Candidate,
    ParamRange,
    SolveRequest,
    SolveResult,
)
from domain.models.validation import ValidationReport

log = structlog.get_logger(__name__)

# Evaluate one param point: build the part with these params, validate it, hand back the
# report. None means the candidate could not be evaluated (build failed, or the report is
# unusable) — the solver treats it as infeasible rather than crashing.
EvaluateFn = Callable[[Mapping[str, float]], ValidationReport | None]

# Coarse-phase samples per continuous axis when no `step` is given. Kept small so the
# Cartesian product stays inside typical `max_evaluations` budgets; the refinement phase
# does the fine-grained work.
_DEFAULT_AXIS_SAMPLES = 5


class ConstraintSolver:
    """Searches a parametric design space for the best target-satisfying candidate."""

    def __init__(self, *, evaluate: EvaluateFn) -> None:
        self._evaluate = evaluate

    def solve(self, request: SolveRequest) -> SolveResult:
        """Run the coarse-then-refine search and return the best feasible candidate.

        Never raises for an unevaluable candidate: a failed build or a missing required
        metric is recorded as an infeasible `Candidate` with a diagnostic and the search
        moves on. Stops once `max_evaluations` builds have been spent.
        """
        rng = random.Random(request.seed)
        budget = _Budget(request.max_evaluations)
        evaluated: list[Candidate] = []
        seen: set[tuple[tuple[str, float], ...]] = set()
        best: Candidate | None = None

        refine_budget = int(request.max_evaluations * request.refine_fraction)
        coarse_budget = request.max_evaluations - refine_budget

        log.info(
            "solve.start",
            ranges=[r.name for r in request.ranges],
            targets=len(request.targets),
            objective=f"{request.objective.direction} {request.objective.metric}",
            max_evaluations=request.max_evaluations,
            coarse_budget=coarse_budget,
            refine_budget=refine_budget,
        )

        # --- Phase 1: coarse space-filling sample ---
        for params in self._coarse_points(request, rng, coarse_budget):
            if budget.exhausted:
                break
            candidate = self._evaluate_point(request, params, seen)
            if candidate is None:
                continue
            budget.spend()
            evaluated.append(candidate)
            best = self._maybe_better(request, best, candidate)

        # --- Phase 2: local refinement (coordinate descent around the best feasible point) ---
        if best is not None and best.feasible:
            best = self._refine(request, best, evaluated, seen, budget)

        log.info(
            "solve.done",
            evaluated=len(evaluated),
            feasible=sum(1 for c in evaluated if c.feasible),
            best_objective=(best.objective_value(request.objective) if best else None),
        )
        return SolveResult(best=best, evaluated=tuple(evaluated))

    # --- candidate evaluation ---

    def _evaluate_point(
        self,
        request: SolveRequest,
        params: Mapping[str, float],
        seen: set[tuple[tuple[str, float], ...]],
    ) -> Candidate | None:
        """Build+validate one point into a `Candidate`; skip (return None) if already seen.

        A None report (build failure / unusable validation) becomes an infeasible candidate
        with a clear reason rather than an exception, so the search is robust to missing
        solvers and bad param combinations.
        """
        key = _params_key(params)
        if key in seen:
            return None
        seen.add(key)

        report = self._evaluate(params)
        if report is None:
            log.info("solve.eval_unavailable", params=dict(params))
            return Candidate(
                params=dict(params),
                metrics={},
                feasible=False,
                violations=("build/validation unavailable (no report)",),
            )

        metrics = dict(report.metrics)
        violations = tuple(
            t.describe_violation(metrics)
            for t in request.targets
            if not t.is_satisfied(metrics)
        )
        feasible = not violations
        log.info(
            "solve.eval",
            params=dict(params),
            feasible=feasible,
            objective=metrics.get(request.objective.metric),
            violations=len(violations),
        )
        return Candidate(
            params=dict(params),
            metrics=metrics,
            feasible=feasible,
            violations=violations,
        )

    def _maybe_better(
        self, request: SolveRequest, best: Candidate | None, candidate: Candidate
    ) -> Candidate | None:
        """Keep whichever of `best`/`candidate` is the better feasible point.

        Only feasible candidates with a present objective metric are eligible; among those
        the objective's direction decides. Infeasible candidates never win.
        """
        if not candidate.feasible:
            return best
        score = candidate.objective_value(request.objective)
        if score is None:
            return best
        if best is None:
            return candidate
        incumbent = best.objective_value(request.objective)
        if incumbent is None:
            return candidate
        return candidate if request.objective.is_better(score, incumbent) else best

    # --- Phase 1: coarse sampling ---

    def _coarse_points(
        self, request: SolveRequest, rng: random.Random, budget: int
    ) -> list[dict[str, float]]:
        """Generate up to `budget` distinct param points spanning the space.

        Stepped axes contribute their grid; continuous axes contribute evenly spaced
        samples. The full Cartesian product is shuffled with the seeded RNG (so an early cut
        by `budget` still spreads across the space rather than favouring one corner) and
        truncated to `budget`.
        """
        if budget <= 0:
            return []

        axes: list[tuple[str, tuple[float, ...]]] = [
            (r.name, r.grid_values(_DEFAULT_AXIS_SAMPLES)) for r in request.ranges
        ]
        product = _cartesian(axes)
        rng.shuffle(product)
        return product[:budget]

    # --- Phase 2: local refinement ---

    def _refine(
        self,
        request: SolveRequest,
        best: Candidate,
        evaluated: list[Candidate],
        seen: set[tuple[tuple[str, float], ...]],
        budget: _Budget,
    ) -> Candidate:
        """Hill-climb around `best`: nudge one axis at a time toward a better objective.

        Coordinate descent — for each axis we try a step below and above the current value
        (step = the axis `step`, or a fraction of its span) and adopt any feasible move that
        improves the objective, shrinking the step when a full sweep yields nothing. Bounded
        by the shared evaluation `budget`.
        """
        current = best
        steps = {r.name: _initial_step(r) for r in request.ranges}

        while not budget.exhausted and any(s > 0 for s in steps.values()):
            improved = False
            for rng_axis in request.ranges:
                if budget.exhausted:
                    break
                step = steps[rng_axis.name]
                if step <= 0:
                    continue
                moved = self._try_axis_moves(
                    request, current, rng_axis, step, evaluated, seen, budget
                )
                if moved is not None:
                    current = moved
                    improved = True
            if not improved:
                # No axis improved at this resolution — halve every step and retry finer.
                steps = {name: _halve(step) for name, step in steps.items()}
        return current

    def _try_axis_moves(
        self,
        request: SolveRequest,
        current: Candidate,
        axis: ParamRange,
        step: float,
        evaluated: list[Candidate],
        seen: set[tuple[tuple[str, float], ...]],
        budget: _Budget,
    ) -> Candidate | None:
        """Try nudging `axis` down then up by `step`; return the best improving feasible move."""
        base = current.params.get(axis.name, axis.lo)
        best_move: Candidate | None = None
        for delta in (-step, step):
            if budget.exhausted:
                break
            value = _clamp(base + delta, axis.lo, axis.hi)
            if value == base:
                continue
            trial_params = {**current.params, axis.name: value}
            candidate = self._evaluate_point(request, trial_params, seen)
            if candidate is None:
                continue
            budget.spend()
            evaluated.append(candidate)
            reference = best_move or current
            improved = self._maybe_better(request, reference, candidate)
            if improved is candidate:
                best_move = candidate
        return best_move


# --- small pure helpers (module-level so they stay trivially testable) ---


class _Budget:
    """Mutable evaluation counter shared across both search phases."""

    __slots__ = ("_remaining",)

    def __init__(self, total: int) -> None:
        self._remaining = total

    @property
    def exhausted(self) -> bool:
        return self._remaining <= 0

    def spend(self) -> None:
        self._remaining -= 1


def _params_key(params: Mapping[str, float]) -> tuple[tuple[str, float], ...]:
    """A hashable, order-independent identity for a param point (for de-duplication)."""
    return tuple(sorted(params.items()))


def _cartesian(axes: list[tuple[str, tuple[float, ...]]]) -> list[dict[str, float]]:
    """Full Cartesian product of the axis grids as a list of param dicts."""
    points: list[dict[str, float]] = [{}]
    for name, values in axes:
        points = [{**point, name: value} for point in points for value in values]
    return points


def _initial_step(axis: ParamRange) -> float:
    """The starting refinement step for an axis: its grid step, else a fraction of its span."""
    if axis.step is not None:
        return axis.step
    span = axis.hi - axis.lo
    return span / _DEFAULT_AXIS_SAMPLES if span > 0 else 0.0


def _halve(step: float) -> float:
    """Shrink a refinement step; collapse to 0 once it is below a meaningful resolution."""
    nxt = step / 2.0
    return nxt if nxt >= _MIN_STEP else 0.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# Refinement stops once every axis step shrinks below this absolute floor — further nudges
# would be smaller than manufacturing tolerance and only burn budget.
_MIN_STEP = 1e-4
