"""Real linear-elastic FEA via gmsh (mesh) + CalculiX ``ccx`` (solve).

This replaces ``NullFeaValidator``. It tetra-meshes the built part, applies boundary
conditions derived from the design's load cases, solves a linear-static step, and folds
peak von Mises stress, peak displacement, and the resulting safety factor into a
``ValidationReport``. It also renders a stress-map PNG for the vision loop.

The heavy, crash-prone work (gmsh links OpenCASCADE; a solve can hang) runs in a
subprocess — ``infrastructure/validation/fea_meshing.py`` — mirroring the CAD sandbox.

Graceful degradation is a first-class path, not an afterthought: gmsh (pip) and ``ccx``
(apt binary) are frequently absent in dev/test/CI. When either is missing the validator
returns an honest WARNING report ("FEA solver not available — stress not verified")
instead of raising, so the rest of the pipeline keeps working with the heavy stack absent.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import structlog

from domain.models.design_spec import DesignSpec, Material
from domain.models.geometry import GeometryArtifact
from domain.models.validation import Severity, ValidationIssue, ValidationReport

if TYPE_CHECKING:
    from collections.abc import Mapping

    import numpy as np

log = structlog.get_logger(__name__)

_RUNNER_MODULE = "infrastructure.validation.fea_meshing"

# Young's modulus (MPa) and Poisson's ratio per material. These are NOT on MaterialProfile
# (which only carries density + tensile for mass/strength narrative), so the elastic
# constants needed for a stress solve live here.
#
# Sources: MatWeb / manufacturer datasheets, room-temperature nominal values. Polymer
# moduli vary widely with print settings and infill; treat these as representative of
# ~solid FDM/injection stock, not a specific print. Poisson's ratios are typical handbook
# values. All documented so the numbers are defensible, not invented.
_ELASTIC: Mapping[Material, tuple[float, float]] = {
    # (E_mpa, poisson)
    Material.PLA: (3500.0, 0.36),      # Ultimaker/Prusa PLA datasheets ~3.3-3.6 GPa
    Material.PETG: (2100.0, 0.40),     # PETG ~2.0-2.2 GPa, tougher/less stiff than PLA
    Material.ABS: (2200.0, 0.35),      # ABS ~2.0-2.3 GPa
    Material.NYLON: (1700.0, 0.39),    # PA12/PA6 unfilled ~1.5-2.0 GPa (very hygroscopic)
    Material.NYLON_CF: (7000.0, 0.35), # Short-CF nylon ~6-8 GPa (highly anisotropic in FDM)
    Material.ALU_6061: (68900.0, 0.33),  # 6061-T6, 68.9 GPa, classic handbook value
    Material.STEEL_MILD: (200000.0, 0.29),  # Mild steel ~200 GPa
}


class _FeaResult(NamedTuple):
    """One solved load case. ``load_case``/``safety_factor`` are filled in by the adapter."""

    von_mises_max: float
    displacement_max: float
    mesh_nodes: int
    frd_path: Path
    load_case: str
    safety_factor: float


def _slug(name: str) -> str:
    """Filesystem-safe token from a load-case name (used in job/artifact filenames)."""
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower() or "case"


class CalculiXFeaValidator:
    """``GeometryValidator`` that runs a real linear-static structural analysis.

    Pipeline per load case with a force: gmsh tetra-mesh the STEP (STL fallback) → write a
    CalculiX ``.inp`` → ``ccx`` solve → parse peak von Mises + displacement → safety factor
    = tensile_mpa / von_mises_max. The worst (lowest safety factor) load case drives the
    report's severity and metrics.
    """

    def __init__(
        self,
        *,
        solver_cmd: str = "ccx",
        mesh_size_mm: float = 2.0,
        cpu_seconds: int = 600,
        timeout_seconds: int = 600,
        warn_safety_factor: float = 2.0,
    ) -> None:
        self._solver_cmd = solver_cmd
        self._mesh_size_mm = mesh_size_mm
        self._cpu_seconds = cpu_seconds
        self._timeout = timeout_seconds
        self._warn_sf = warn_safety_factor

    @property
    def name(self) -> str:
        return "fea"

    def validate(self, *, artifact: GeometryArtifact, spec: DesignSpec) -> ValidationReport:
        # --- graceful degradation: is the heavy stack even present? ---
        missing = self._missing_dependency()
        if missing is not None:
            return ValidationReport(
                issues=(
                    ValidationIssue(
                        self.name,
                        Severity.WARNING,
                        f"FEA solver not available ({missing}) — stress not verified. "
                        f"Install gmsh + CalculiX (ccx) to enable structural checks. "
                        f"Bench-test before flight.",
                    ),
                )
            )

        profile = spec.material_profile()
        if spec.material not in _ELASTIC:
            return ValidationReport(
                issues=(
                    ValidationIssue(
                        self.name,
                        Severity.WARNING,
                        f"No elastic constants on record for material "
                        f"'{spec.material.value}' — FEA skipped.",
                    ),
                )
            )
        youngs_mpa, poisson = _ELASTIC[spec.material]

        force_cases = [lc for lc in spec.load_cases if lc.force_newtons]
        issues: list[ValidationIssue] = [self._assumptions_issue(youngs_mpa, poisson)]

        # INFO for every load case that carries no force — we can't solve those.
        for lc in spec.load_cases:
            if not lc.force_newtons:
                issues.append(
                    ValidationIssue(
                        self.name,
                        Severity.INFO,
                        f"Load case '{lc.name}' has no force in newtons — skipped "
                        f"(described only: {lc.description}).",
                    )
                )

        if not force_cases:
            issues.append(
                ValidationIssue(
                    self.name,
                    Severity.INFO,
                    "No load case specified a force in newtons; nothing to solve.",
                )
            )
            return ValidationReport(issues=tuple(issues))

        geometry_path = self._pick_geometry(artifact)

        # Solve each forced load case; keep the worst (lowest safety factor).
        worst: _FeaResult | None = None
        for lc in force_cases:
            result = self._solve_case(
                geometry_path=geometry_path,
                out_dir=artifact.step_path.parent,
                job_name=f"{artifact.artifact_id}_{_slug(lc.name)}",
                youngs_mpa=youngs_mpa,
                poisson=poisson,
                force_newtons=float(lc.force_newtons or 0.0),  # non-None: filtered above
            )
            if result is None:
                issues.append(
                    ValidationIssue(
                        self.name,
                        Severity.WARNING,
                        f"Load case '{lc.name}' could not be solved (mesh or solver "
                        f"failure) — stress not verified for it.",
                    )
                )
                continue

            sf = (
                profile.tensile_mpa / result.von_mises_max
                if result.von_mises_max > 0
                else float("inf")
            )
            result = result._replace(load_case=lc.name, safety_factor=sf)
            if worst is None or sf < worst.safety_factor:
                worst = result

        if worst is None:
            issues.append(
                ValidationIssue(
                    self.name,
                    Severity.WARNING,
                    "FEA ran but no load case produced a usable result — stress not verified.",
                )
            )
            return ValidationReport(issues=tuple(issues))

        issues.extend(self._result_issues(worst, tensile_mpa=profile.tensile_mpa))
        stress_png = self._render_stress_map(worst, out_dir=artifact.step_path.parent)
        if stress_png is not None:
            issues.append(
                ValidationIssue(
                    self.name,
                    Severity.INFO,
                    f"Stress map rendered: {stress_png}",
                )
            )

        metrics = {
            "fea.von_mises_max_mpa": worst.von_mises_max,
            "fea.displacement_max_mm": worst.displacement_max,
            "fea.safety_factor": worst.safety_factor,
            "fea.mesh_nodes": float(worst.mesh_nodes),
        }
        return ValidationReport(issues=tuple(issues), metrics=metrics)

    # --- dependency / geometry selection -------------------------------------------------

    def _missing_dependency(self) -> str | None:
        """Return a human string naming the first missing piece, or None if all present."""
        if importlib.util.find_spec("gmsh") is None:
            return "gmsh not importable"
        if shutil.which(self._solver_cmd) is None:
            return f"'{self._solver_cmd}' not on PATH"
        return None

    def _pick_geometry(self, artifact: GeometryArtifact) -> Path:
        """Prefer the STEP (true solid) for meshing; fall back to STL if STEP is absent."""
        if artifact.step_path.exists():
            return artifact.step_path
        return artifact.stl_path

    # --- subprocess orchestration --------------------------------------------------------

    def _solve_case(
        self,
        *,
        geometry_path: Path,
        out_dir: Path,
        job_name: str,
        youngs_mpa: float,
        poisson: float,
        force_newtons: float,
    ) -> _FeaResult | None:
        """Run mesh+solve in the isolated subprocess. Returns None on any failure."""
        out_dir.mkdir(parents=True, exist_ok=True)
        request_path = out_dir / f"{job_name}.fea_request.json"
        response_path = out_dir / f"{job_name}.fea_response.json"
        request_path.write_text(
            json.dumps(
                {
                    "geometry_path": str(geometry_path),
                    "work_dir": str(out_dir),
                    "job_name": job_name,
                    "mesh_size_mm": self._mesh_size_mm,
                    "youngs_mpa": youngs_mpa,
                    "poisson": poisson,
                    "force_newtons": force_newtons,
                    "cpu_seconds": self._cpu_seconds,
                    "ccx_cmd": self._solver_cmd,
                }
            )
        )

        try:
            subprocess.run(
                [sys.executable, "-m", _RUNNER_MODULE, str(request_path), str(response_path)],
                timeout=self._timeout,
                capture_output=True,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning("fea.timeout", job=job_name, timeout=self._timeout)
            return None

        if not response_path.exists():
            log.warning("fea.no_response", job=job_name)
            return None

        payload = json.loads(response_path.read_text())
        if not payload.get("ok"):
            log.warning(
                "fea.failed", job=job_name, kind=payload.get("kind"), message=payload.get("message")
            )
            return None

        return _FeaResult(
            von_mises_max=float(payload["von_mises_max_mpa"]),
            displacement_max=float(payload["displacement_max_mm"]),
            mesh_nodes=int(payload["mesh_nodes"]),
            frd_path=Path(payload["frd_path"]),
            load_case="",
            safety_factor=0.0,
        )

    # --- report assembly -----------------------------------------------------------------

    def _assumptions_issue(self, youngs_mpa: float, poisson: float) -> ValidationIssue:
        return ValidationIssue(
            self.name,
            Severity.INFO,
            "FEA assumptions (linear-elastic, small-strain): base = lowest-Z face fixed "
            "(encastre); load applied to the highest-Z face in -Z, split equally across "
            f"its nodes; E={youngs_mpa:.0f} MPa, nu={poisson}. First-order tetra mesh "
            "under-reports peak stress on coarse meshes — treat the safety factor as "
            "indicative and bench-test before flight.",
        )

    def _result_issues(
        self, r: _FeaResult, *, tensile_mpa: float
    ) -> list[ValidationIssue]:
        issues = [
            ValidationIssue(
                self.name,
                Severity.INFO,
                f"Worst load case '{r.load_case}': von Mises max "
                f"{r.von_mises_max:.1f} MPa over {r.mesh_nodes} nodes "
                f"(material tensile {tensile_mpa:.0f} MPa).",
                value=r.von_mises_max,
                limit=tensile_mpa,
            ),
            ValidationIssue(
                self.name,
                Severity.INFO,
                f"Max displacement: {r.displacement_max:.3f} mm.",
                value=r.displacement_max,
            ),
        ]

        sf = r.safety_factor
        if sf < 1.0:
            severity = Severity.ERROR
            note = "part is predicted to yield under this load."
        elif sf < self._warn_sf:
            severity = Severity.WARNING
            note = f"thin margin (target >= {self._warn_sf:g})."
        else:
            severity = Severity.INFO
            note = "adequate margin."
        issues.append(
            ValidationIssue(
                self.name,
                severity,
                f"Safety factor {sf:.2f} — {note}",
                value=sf,
                limit=1.0,
            )
        )
        return issues

    # --- stress-map render ---------------------------------------------------------------

    def _render_stress_map(self, r: _FeaResult, *, out_dir: Path) -> Path | None:
        """Render per-node von Mises stress from the .frd as a colored point cloud PNG.

        Deliberately lightweight (matplotlib Agg, no GL) to match ``MatplotlibRenderer`` and
        run headless in the slim image. It exists to give the vision model something to
        critique — a stress hot-spot map — not to be a polished contour plot. Failures here
        are non-fatal: we log and return None.
        """
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import numpy as np

            coords, vm = self._read_frd_stress_points(r.frd_path)
            if coords.size == 0:
                return None

            out_dir.mkdir(parents=True, exist_ok=True)
            png_path = out_dir / "fea_stress.png"

            fig = plt.figure(figsize=(7.68, 7.68), dpi=100)
            ax = fig.add_subplot(111, projection="3d")
            sc = ax.scatter(
                coords[:, 0], coords[:, 1], coords[:, 2],
                c=vm, cmap="turbo", s=6, depthshade=True,
            )
            span = coords.max(axis=0) - coords.min(axis=0)
            ax.set_box_aspect(tuple(np.where(span > 0, span, 1.0)))
            ax.view_init(elev=25.0, azim=45.0)
            ax.set_axis_off()
            fig.colorbar(sc, ax=ax, shrink=0.6, label="von Mises (MPa)")
            ax.set_title(f"von Mises — {r.load_case} (max {r.von_mises_max:.1f} MPa)")
            fig.savefig(png_path, bbox_inches="tight", pad_inches=0.1)
            plt.close(fig)
            return png_path
        except Exception as exc:
            log.warning("fea.stress_render_failed", error=str(exc))
            return None

    def _read_frd_stress_points(
        self, frd_path: Path
    ) -> tuple[np.ndarray, np.ndarray]:
        """Read node coords + per-node von Mises from the .frd for the stress map.

        Local, dependency-light parse (numpy only). Mirrors the solver harness's parser but
        keeps coordinates too so we can plot in space.
        """
        import math

        import numpy as np

        node_xyz: dict[int, tuple[float, float, float]] = {}
        node_vm: dict[int, float] = {}
        mode: str | None = None  # "coord" | "stress"

        with frd_path.open("r") as fh:
            for raw in fh:
                stripped = raw.strip()
                if stripped.startswith("2C"):
                    mode = "coord"
                    continue
                if stripped.startswith("-4"):
                    parts = stripped.split()
                    mode = "stress" if (len(parts) > 1 and parts[1] == "STRESS") else None
                    continue
                if stripped.startswith("-3"):
                    if mode == "coord":
                        mode = None  # coordinate block ended
                    continue
                if not stripped.startswith("-1"):
                    continue

                parts = stripped.split()
                try:
                    node = int(parts[1])
                    vals = [float(p) for p in parts[2:]]
                except (ValueError, IndexError):
                    continue

                if mode == "coord" and len(vals) >= 3:
                    node_xyz[node] = (vals[0], vals[1], vals[2])
                elif mode == "stress" and len(vals) >= 6:
                    sxx, syy, szz, sxy, syz, szx = vals[:6]
                    node_vm[node] = math.sqrt(
                        0.5
                        * (
                            (sxx - syy) ** 2
                            + (syy - szz) ** 2
                            + (szz - sxx) ** 2
                            + 6.0 * (sxy**2 + syz**2 + szx**2)
                        )
                    )

        shared = [n for n in node_vm if n in node_xyz]
        if not shared:
            return np.empty((0, 3)), np.empty((0,))
        coords = np.array([node_xyz[n] for n in shared], dtype=float)
        vm = np.array([node_vm[n] for n in shared], dtype=float)
        return coords, vm
