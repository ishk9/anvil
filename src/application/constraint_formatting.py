"""UI-agnostic formatting of a `SolveResult` into readable text (mirrors `formatting.py`).

Kept separate from the always-loaded `formatting` module so the solver feature is
self-contained; the MCP/CLI layers call `format_solve_result` to present a search outcome.
"""

from __future__ import annotations

from domain.models.constraints import Candidate, Objective, SolveResult


def format_solve_result(result: SolveResult, objective: Objective) -> str:
    """Render a search outcome: the winning params + metrics, then a short evaluation tally.

    When no candidate was feasible, explain that and surface one representative rejection so
    the caller can see *why* the space was empty (e.g. a target no candidate could meet).
    """
    header = (
        f"Solve: evaluated {len(result.evaluated)} candidate(s), "
        f"{result.feasible_count} feasible. "
        f"Objective: {objective.direction} {objective.metric}."
    )

    if result.best is None:
        lines = [header, "No feasible design found."]
        sample = next((c for c in result.evaluated if c.violations), None)
        if sample is not None:
            lines.append("Example rejection:")
            lines.append(f"  params: {_fmt_params(sample)}")
            for v in sample.violations:
                lines.append(f"  - {v}")
        return "\n".join(lines)

    best = result.best
    score = best.objective_value(objective)
    lines = [header, "Best design:"]
    lines.append(f"  params: {_fmt_params(best)}")
    if score is not None:
        lines.append(f"  {objective.metric}: {score:.4g}")
    for key in sorted(best.metrics):
        if key == objective.metric:
            continue
        lines.append(f"  {key}: {best.metrics[key]:.4g}")
    return "\n".join(lines)


def _fmt_params(candidate: Candidate) -> str:
    return ", ".join(f"{name}={value:.4g}" for name, value in sorted(candidate.params.items()))
