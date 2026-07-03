"""Pure unit tests for the constraint solver, driven by a FAKE evaluator.

No CAD / build123d here: the evaluation function is plain math (``mass = f(params)``), so
the solver's search, feasibility logic, budget accounting, and missing-metric handling are
exercised in isolation. This mirrors how `test_revision.py` stubs the build callable.
"""

from __future__ import annotations

from collections.abc import Mapping

from application.constraint_solver import ConstraintSolver, EvaluateFn
from domain.models.constraints import (
    Objective,
    ParamRange,
    SolveRequest,
    Target,
)
from domain.models.validation import ValidationReport


def _report(**metrics: float) -> ValidationReport:
    return ValidationReport(metrics=metrics)


def _counting(fn: EvaluateFn) -> tuple[EvaluateFn, list[dict[str, float]]]:
    """Wrap an evaluator to record every param point it was called with."""
    calls: list[dict[str, float]] = []

    def wrapped(params: Mapping[str, float]) -> ValidationReport | None:
        calls.append(dict(params))
        return fn(params)

    return wrapped, calls


# A simple, monotone physical model: a thicker/longer arm is heavier but stronger.
# mass increases with width; safety_factor increases with width.
def _arm_evaluator(params: Mapping[str, float]) -> ValidationReport | None:
    w = params["arm_width_mm"]
    return _report(
        **{
            "mass.mass_g": 2.0 * w,
            "fea.safety_factor": 0.4 * w,
        }
    )


# --- feasible optimum ---


def test_finds_feasible_optimum_minimising_mass() -> None:
    # Want the lightest arm that still clears safety_factor >= 2.0 (=> width >= 5.0).
    # Lighter is better, so the optimum sits right at width 5.0.
    request = SolveRequest(
        code="part = arm(params)",
        ranges=(ParamRange("arm_width_mm", lo=3.0, hi=12.0, step=1.0),),
        targets=(
            Target("fea.safety_factor", ">=", 2.0),
            Target("mass.mass_g", "<=", 100.0),
        ),
        objective=Objective("mass.mass_g", "min"),
    )
    solver = ConstraintSolver(evaluate=_arm_evaluator)

    result = solver.solve(request)

    assert result.best is not None
    assert result.best.feasible
    assert result.best.params["arm_width_mm"] == 5.0
    assert result.best.metrics["mass.mass_g"] == 10.0
    # Every recorded feasible candidate indeed meets the targets.
    for c in result.evaluated:
        if c.feasible:
            assert c.metrics["fea.safety_factor"] >= 2.0


def test_maximising_objective_picks_the_largest_feasible() -> None:
    # Maximise safety_factor while keeping mass under 20g (=> width <= 10.0).
    request = SolveRequest(
        code="part = arm(params)",
        ranges=(ParamRange("arm_width_mm", lo=3.0, hi=12.0, step=1.0),),
        targets=(Target("mass.mass_g", "<=", 20.0),),
        objective=Objective("fea.safety_factor", "max"),
    )
    result = ConstraintSolver(evaluate=_arm_evaluator).solve(request)

    assert result.best is not None
    assert result.best.params["arm_width_mm"] == 10.0
    assert result.best.metrics["mass.mass_g"] == 20.0


# --- infeasibility ---


def test_reports_infeasible_when_no_candidate_meets_targets() -> None:
    # Demand a safety_factor no arm in range can reach (max width 12 => sf 4.8 < 10).
    request = SolveRequest(
        code="part = arm(params)",
        ranges=(ParamRange("arm_width_mm", lo=3.0, hi=12.0, step=1.0),),
        targets=(Target("fea.safety_factor", ">=", 10.0),),
        objective=Objective("mass.mass_g", "min"),
    )
    result = ConstraintSolver(evaluate=_arm_evaluator).solve(request)

    assert result.best is None
    assert result.feasible_count == 0
    assert len(result.evaluated) > 0
    # Every candidate carries a diagnostic for why it was rejected.
    assert all(c.violations for c in result.evaluated)


# --- budget ---


def test_respects_max_evaluations() -> None:
    # A fine continuous axis has effectively unlimited points; the budget must cap builds.
    evaluator, calls = _counting(_arm_evaluator)
    request = SolveRequest(
        code="part = arm(params)",
        ranges=(ParamRange("arm_width_mm", lo=3.0, hi=100.0),),
        targets=(Target("fea.safety_factor", ">=", 2.0),),
        objective=Objective("mass.mass_g", "min"),
        max_evaluations=6,
    )
    result = ConstraintSolver(evaluate=evaluator).solve(request)

    assert len(calls) <= 6
    assert len(result.evaluated) <= 6


def test_deduplicates_repeated_points_within_budget() -> None:
    # Refinement can revisit the incumbent's neighbours; identical points must not be
    # re-evaluated, and de-dup skips do not count against the build budget.
    evaluator, calls = _counting(_arm_evaluator)
    request = SolveRequest(
        code="part = arm(params)",
        ranges=(ParamRange("arm_width_mm", lo=3.0, hi=12.0, step=1.0),),
        targets=(Target("fea.safety_factor", ">=", 2.0),),
        objective=Objective("mass.mass_g", "min"),
        max_evaluations=40,
    )
    ConstraintSolver(evaluate=evaluator).solve(request)

    keys = [tuple(sorted(c.items())) for c in calls]
    assert len(keys) == len(set(keys))  # no point built twice


# --- missing metrics / build failure ---


def test_candidate_missing_required_metric_is_infeasible_not_crash() -> None:
    # Simulate an absent FEA solver: mass is reported but safety_factor is omitted.
    # The safety_factor target can never be proven, so nothing is feasible.
    def no_fea(params: Mapping[str, float]) -> ValidationReport | None:
        return _report(**{"mass.mass_g": 2.0 * params["arm_width_mm"]})

    request = SolveRequest(
        code="part = arm(params)",
        ranges=(ParamRange("arm_width_mm", lo=3.0, hi=12.0, step=1.0),),
        targets=(Target("fea.safety_factor", ">=", 2.0),),
        objective=Objective("mass.mass_g", "min"),
    )
    result = ConstraintSolver(evaluate=no_fea).solve(request)

    assert result.best is None
    assert result.feasible_count == 0
    # The diagnostic names the missing metric.
    assert any("fea.safety_factor missing" in v for c in result.evaluated for v in c.violations)


def test_build_failure_returns_none_and_is_recorded_infeasible() -> None:
    # Evaluator returns None (build failed) for thin arms, a report otherwise.
    def flaky(params: Mapping[str, float]) -> ValidationReport | None:
        w = params["arm_width_mm"]
        if w < 6.0:
            return None
        return _report(**{"mass.mass_g": 2.0 * w, "fea.safety_factor": 0.4 * w})

    request = SolveRequest(
        code="part = arm(params)",
        ranges=(ParamRange("arm_width_mm", lo=3.0, hi=12.0, step=1.0),),
        targets=(Target("fea.safety_factor", ">=", 2.0),),
        objective=Objective("mass.mass_g", "min"),
    )
    result = ConstraintSolver(evaluate=flaky).solve(request)

    # A feasible optimum still exists among the buildable arms (width 6 => sf 2.4).
    assert result.best is not None
    assert result.best.params["arm_width_mm"] == 6.0
    # The failed builds are recorded as infeasible with a clear reason.
    failed = [c for c in result.evaluated if "unavailable" in " ".join(c.violations)]
    assert failed
    assert all(not c.feasible for c in failed)


# --- objective metric missing on an otherwise feasible candidate ---


def test_feasible_candidate_without_objective_metric_is_not_selected() -> None:
    # Targets pass, but the objective metric is absent — such a candidate cannot be ranked
    # and must not be returned as best.
    def no_objective(params: Mapping[str, float]) -> ValidationReport | None:
        return _report(**{"fea.safety_factor": 0.4 * params["arm_width_mm"]})

    request = SolveRequest(
        code="part = arm(params)",
        ranges=(ParamRange("arm_width_mm", lo=6.0, hi=12.0, step=1.0),),
        targets=(Target("fea.safety_factor", ">=", 2.0),),
        objective=Objective("mass.mass_g", "min"),
    )
    result = ConstraintSolver(evaluate=no_objective).solve(request)

    # Candidates are feasible (safety target met) but unrankable — no best selected.
    assert result.feasible_count > 0
    assert result.best is None


# --- multi-dimensional search + determinism ---


def test_two_axis_search_finds_feasible_point() -> None:
    # width drives strength+mass; length adds mass only. Minimise mass subject to strength.
    def two_axis(params: Mapping[str, float]) -> ValidationReport | None:
        w = params["arm_width_mm"]
        length = params["length_mm"]
        return _report(
            **{
                "mass.mass_g": 2.0 * w + 0.1 * length,
                "fea.safety_factor": 0.4 * w,
            }
        )

    request = SolveRequest(
        code="part = arm(params)",
        ranges=(
            ParamRange("arm_width_mm", lo=3.0, hi=12.0, step=1.0),
            ParamRange("length_mm", lo=50.0, hi=100.0, step=10.0),
        ),
        targets=(Target("fea.safety_factor", ">=", 2.0),),
        objective=Objective("mass.mass_g", "min"),
        max_evaluations=64,
    )
    result = ConstraintSolver(evaluate=two_axis).solve(request)

    assert result.best is not None
    # Lightest feasible point: min width that clears safety (5.0) and shortest length (50).
    assert result.best.params["arm_width_mm"] == 5.0
    assert result.best.params["length_mm"] == 50.0


def test_same_request_is_reproducible() -> None:
    evaluator_a, calls_a = _counting(_arm_evaluator)
    evaluator_b, calls_b = _counting(_arm_evaluator)
    request = SolveRequest(
        code="part = arm(params)",
        ranges=(ParamRange("arm_width_mm", lo=3.0, hi=100.0),),
        targets=(Target("fea.safety_factor", ">=", 2.0),),
        objective=Objective("mass.mass_g", "min"),
        max_evaluations=8,
        seed=1234,
    )

    ConstraintSolver(evaluate=evaluator_a).solve(request)
    ConstraintSolver(evaluate=evaluator_b).solve(request)

    # Identical seed => identical candidates in identical order.
    assert calls_a == calls_b


# --- equality target ---


def test_equality_target_within_tolerance() -> None:
    request = SolveRequest(
        code="part = arm(params)",
        ranges=(ParamRange("arm_width_mm", lo=3.0, hi=12.0, step=1.0),),
        targets=(Target("mass.mass_g", "==", 10.0),),  # exactly width 5.0
        objective=Objective("fea.safety_factor", "max"),
    )
    result = ConstraintSolver(evaluate=_arm_evaluator).solve(request)

    assert result.best is not None
    assert result.best.params["arm_width_mm"] == 5.0
