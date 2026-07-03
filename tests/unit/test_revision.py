"""Pure unit tests for parametric revisions: model, diff, history round-trip, service.

Deliberately free of the CAD/build123d stack — the build step is a stub callable so the
`RevisionService` orchestration is exercised without OpenCASCADE.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from application.revision_service import RevisionService
from domain.models.design_spec import DesignSpec, LoadCase, Material
from domain.models.errors import CadError, CadErrorKind
from domain.models.geometry import BoundingBox, GeometryArtifact
from domain.models.result import Err, Ok, Result
from domain.models.revision import (
    DesignVersion,
    RevisionHistory,
    diff_versions,
)
from infrastructure.persistence.filesystem_history_repository import (
    FilesystemHistoryRepository,
)

_BASE_SPEC = DesignSpec(
    title="drone arm",
    summary="quad arm bracket",
    material=Material.NYLON_CF,
    requirements=("hold motor", "survive crash"),
    interfaces=("M3", "30.5mm"),
)


def _spec(**overrides: object) -> DesignSpec:
    return replace(_BASE_SPEC, **overrides)  # type: ignore[arg-type]


def _artifact(artifact_id: str = "part_001") -> GeometryArtifact:
    return GeometryArtifact(
        artifact_id=artifact_id,
        source_code="part = Box(1, 1, 1)",
        step_path=Path(f"/tmp/{artifact_id}.step"),
        stl_path=Path(f"/tmp/{artifact_id}.stl"),
        bounding_box=BoundingBox(1.0, 1.0, 1.0),
    )


# --- DesignVersion / RevisionHistory model ---


def test_history_version_numbering_is_monotonic_and_one_indexed() -> None:
    history = RevisionHistory()
    assert history.next_version == 1
    assert history.latest is None

    v1 = DesignVersion(version=1, spec=_spec(), code="part = None", params={"w": 8.0})
    history = history.append(v1)
    assert history.next_version == 2
    assert history.latest is v1
    assert history.get(1) is v1
    assert history.get(99) is None


def test_with_overrides_merges_params_and_sets_parent() -> None:
    parent = DesignVersion(
        version=1,
        spec=_spec(),
        code="w = params['arm_width_mm']",
        params={"arm_width_mm": 8.0, "length_mm": 100.0},
    )
    child = parent.with_overrides(version=2, param_overrides={"arm_width_mm": 8.8})

    assert child.version == 2
    assert child.parent_version == 1
    # Overridden param changed; untouched param inherited.
    assert child.params == {"arm_width_mm": 8.8, "length_mm": 100.0}
    # Same model re-run: code and spec inherited when not supplied.
    assert child.code == parent.code
    assert child.spec == parent.spec
    # Child artifact is unassigned until it builds.
    assert child.artifact_id is None
    # Parent is untouched (frozen value object).
    assert parent.params == {"arm_width_mm": 8.0, "length_mm": 100.0}


def test_with_overrides_accepts_new_code_and_spec() -> None:
    parent = DesignVersion(version=1, spec=_spec(), code="old", params={})
    new_spec = _spec(title="v2 arm")
    child = parent.with_overrides(
        version=2, param_overrides={}, code="new", spec=new_spec, note="rewrite"
    )
    assert child.code == "new"
    assert child.spec.title == "v2 arm"
    assert child.note == "rewrite"


# --- diff_versions ---


def test_diff_detects_param_add_change_remove() -> None:
    a = DesignVersion(version=1, spec=_spec(), code="c", params={"w": 8.0, "gone": 1.0})
    b = DesignVersion(version=2, spec=_spec(), code="c", params={"w": 8.8, "added": 2.0})

    diff = diff_versions(a, b)
    assert diff.from_version == 1
    assert diff.to_version == 2
    assert not diff.code_changed

    changes = {p.name: (p.before, p.after) for p in diff.param_changes}
    assert changes == {
        "added": (None, 2.0),
        "gone": (1.0, None),
        "w": (8.0, 8.8),
    }
    assert not diff.is_empty


def test_diff_detects_spec_field_and_code_changes() -> None:
    a = DesignVersion(version=1, spec=_spec(material=Material.NYLON_CF), code="c1")
    b = DesignVersion(
        version=2,
        spec=_spec(material=Material.ALU_6061, requirements=("hold motor",)),
        code="c2",
    )
    diff = diff_versions(a, b)

    changed_fields = {s.field: (s.before, s.after) for s in diff.spec_changes}
    assert changed_fields["material"] == ("nylon_cf", "alu_6061")
    # requirements rendered as sorted comma-joined text.
    assert changed_fields["requirements"][0] == "hold motor, survive crash"
    assert changed_fields["requirements"][1] == "hold motor"
    assert diff.code_changed


def test_diff_of_identical_versions_is_empty() -> None:
    v = DesignVersion(version=1, spec=_spec(), code="c", params={"w": 8.0})
    other = DesignVersion(version=2, spec=_spec(), code="c", params={"w": 8.0})
    assert diff_versions(v, other).is_empty


def test_diff_ignores_tuple_ordering() -> None:
    a = DesignVersion(version=1, spec=_spec(requirements=("a", "b")), code="c")
    b = DesignVersion(version=2, spec=_spec(requirements=("b", "a")), code="c")
    assert diff_versions(a, b).is_empty


# --- FilesystemHistoryRepository round-trip ---


def test_history_repo_round_trips_full_spec_and_params(tmp_path: Path) -> None:
    repo = FilesystemHistoryRepository(workspace_dir=tmp_path)
    spec = _spec(
        load_cases=(LoadCase(name="crash", description="1.5m drop", force_newtons=120.0),),
        constraints=("mass < 20g",),
    )
    version = DesignVersion(
        version=1,
        spec=spec,
        code="part = Box(10, 10, 4)",
        params={"arm_width_mm": 8.0},
        parent_version=None,
        artifact_id="part_001",
        note="initial",
        timestamp="2026-07-03T12:00:00+00:00",
    )
    repo.save_version("sess-a", version)

    loaded = repo.load_history("sess-a")
    assert len(loaded.versions) == 1
    got = loaded.versions[0]
    assert got == version  # full structural equality across the JSON boundary
    assert got.spec.load_cases[0].force_newtons == 120.0
    assert got.params["arm_width_mm"] == 8.0


def test_history_repo_appends_and_isolates_sessions(tmp_path: Path) -> None:
    repo = FilesystemHistoryRepository(workspace_dir=tmp_path)
    repo.save_version("a", DesignVersion(version=1, spec=_spec(), code="c1"))
    repo.save_version("a", DesignVersion(version=2, spec=_spec(), code="c2"))
    repo.save_version("b", DesignVersion(version=1, spec=_spec(), code="other"))

    assert len(repo.load_history("a").versions) == 2
    assert repo.get_version("a", 2).code == "c2"  # type: ignore[union-attr]
    assert len(repo.load_history("b").versions) == 1
    assert repo.get_version("a", 99) is None


def test_history_repo_empty_when_no_file(tmp_path: Path) -> None:
    repo = FilesystemHistoryRepository(workspace_dir=tmp_path)
    assert repo.load_history("never-seen") == RevisionHistory()


def test_history_repo_params_decode_as_float(tmp_path: Path) -> None:
    # JSON encodes 8 (int) but params are semantically float; decoding must coerce.
    repo = FilesystemHistoryRepository(workspace_dir=tmp_path)
    repo.save_version(
        "s", DesignVersion(version=1, spec=_spec(), code="c", params={"n": 8.0})
    )
    got = repo.get_version("s", 1)
    assert got is not None
    assert isinstance(got.params["n"], float)


# --- RevisionService orchestration (stubbed build) ---


def test_record_initial_allocates_from_persisted_history(tmp_path: Path) -> None:
    service = RevisionService(history=FilesystemHistoryRepository(workspace_dir=tmp_path))
    v1 = service.record_initial(
        session_id="s", spec=_spec(), code="c", artifact_id="part_001", params={"w": 8.0}
    )
    v2 = service.record_initial(
        session_id="s", spec=_spec(), code="c", artifact_id="part_002", params={"w": 8.0}
    )
    assert v1.version == 1
    assert v1.parent_version is None
    assert v2.version == 2
    assert v2.parent_version == 1


def test_revise_reruns_same_code_with_merged_params(tmp_path: Path) -> None:
    service = RevisionService(history=FilesystemHistoryRepository(workspace_dir=tmp_path))
    service.record_initial(
        session_id="s",
        spec=_spec(),
        code="w = params['arm_width_mm']",
        artifact_id="part_001",
        params={"arm_width_mm": 8.0, "length_mm": 100.0},
    )

    seen: dict[str, object] = {}

    def build(code: str, params: dict[str, float]) -> Result[GeometryArtifact, CadError]:
        seen["code"] = code
        seen["params"] = params
        return Ok(_artifact("part_002"))

    result = service.revise(
        session_id="s",
        base_version=1,
        param_overrides={"arm_width_mm": 8.8},
        build=build,
        note="10% thicker arms",
    )
    assert result.is_ok()
    version, diff = result.unwrap()

    # Same stored code re-run.
    assert seen["code"] == "w = params['arm_width_mm']"
    # Overridden param merged over inherited param.
    assert seen["params"] == {"arm_width_mm": 8.8, "length_mm": 100.0}

    assert version.version == 2
    assert version.parent_version == 1
    assert version.artifact_id == "part_002"
    assert version.note == "10% thicker arms"

    # Diff reports only the touched dimension.
    assert len(diff.param_changes) == 1
    assert diff.param_changes[0].name == "arm_width_mm"
    assert diff.param_changes[0].before == 8.0
    assert diff.param_changes[0].after == 8.8

    # Persisted.
    assert len(service.history("s").versions) == 2


def test_revise_with_unknown_base_version_errors(tmp_path: Path) -> None:
    service = RevisionService(history=FilesystemHistoryRepository(workspace_dir=tmp_path))

    def build(code: str, params: dict[str, float]) -> Result[GeometryArtifact, CadError]:
        raise AssertionError("build must not run for an unknown base version")

    result = service.revise(
        session_id="s", base_version=7, param_overrides={}, build=build
    )
    assert result.is_err()
    assert result.error.kind is CadErrorKind.INTERNAL  # type: ignore[union-attr]


def test_revise_does_not_record_on_build_failure(tmp_path: Path) -> None:
    service = RevisionService(history=FilesystemHistoryRepository(workspace_dir=tmp_path))
    service.record_initial(
        session_id="s", spec=_spec(), code="c", artifact_id="part_001"
    )

    def build(code: str, params: dict[str, float]) -> Result[GeometryArtifact, CadError]:
        return Err(CadError(kind=CadErrorKind.EXECUTION, message="boom"))

    result = service.revise(
        session_id="s", base_version=1, param_overrides={"w": 9.0}, build=build
    )
    assert result.is_err()
    # History still only holds the initial version — failed builds are not recorded.
    assert len(service.history("s").versions) == 1


def test_service_diff_reports_unknown_versions(tmp_path: Path) -> None:
    service = RevisionService(history=FilesystemHistoryRepository(workspace_dir=tmp_path))
    service.record_initial(
        session_id="s", spec=_spec(), code="c", artifact_id="part_001"
    )
    result = service.diff(session_id="s", from_version=1, to_version=5)
    assert result.is_err()
    assert "5" in result.error  # type: ignore[union-attr]
