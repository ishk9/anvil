"""Integration test for the MCP-facing service (build -> validate -> export)."""

from __future__ import annotations

from pathlib import Path

import pytest

from application.assembly_toolkit import AssemblyToolkit
from application.catalog_service import CatalogService
from application.design_toolkit import DesignToolkit
from application.revision_service import RevisionService
from config.settings import Settings
from infrastructure.cad.build123d_executor import Build123dExecutor
from infrastructure.persistence.filesystem_artifact_repository import (
    FilesystemArtifactRepository,
)
from infrastructure.persistence.filesystem_design_catalog import FilesystemDesignCatalog
from infrastructure.persistence.filesystem_history_repository import (
    FilesystemHistoryRepository,
)
from infrastructure.rendering.matplotlib_renderer import MatplotlibRenderer
from infrastructure.validation.composite_validator import CompositeValidator
from infrastructure.validation.mass_properties_validator import MassPropertiesValidator
from infrastructure.validation.printability_validator import PrintabilityValidator
from interfaces.mcp.service import McpDesignService

pytestmark = pytest.mark.integration

_PLATE = """
plate = Box(40, 40, 4)
plate = plate - Pos(0, 0, 0) * Cylinder(radius=1.7, height=4)
part = plate
"""


def _service(tmp_path: Path) -> McpDesignService:
    toolkit = DesignToolkit(
        executor=Build123dExecutor(timeout_seconds=60, memory_limit_mb=2048),
        renderer=MatplotlibRenderer(image_size=256),
        validator=CompositeValidator(
            validators=(
                MassPropertiesValidator(),
                PrintabilityValidator(build_volume_mm=(220, 220, 250), min_wall_thickness_mm=0.8),
            )
        ),
        repository=FilesystemArtifactRepository(workspace_dir=tmp_path),
        settings=Settings(render_views=2),
    )
    history = FilesystemHistoryRepository(workspace_dir=tmp_path)
    catalog = FilesystemDesignCatalog(workspace_dir=tmp_path)
    return McpDesignService(
        toolkit=toolkit,
        revisions=RevisionService(history=history),
        catalog=CatalogService(catalog=catalog),
        assemblies=AssemblyToolkit(toolkit=toolkit),
    )


def test_build_validate_export_flow(tmp_path: Path) -> None:
    service = _service(tmp_path)

    outcome = service.build(session_id="s1", code=_PLATE)
    assert outcome.ok, outcome.message
    assert len(outcome.image_paths) == 2
    assert all(p.exists() for p in outcome.image_paths)

    report = service.validate(session_id="s1", artifact_id="part_001", material="pla")
    assert "PASS" in report

    result = service.export(session_id="s1", artifact_id="part_001", title="test plate")
    assert ".step" in result and ".stl" in result


def test_build_error_is_reported_not_raised(tmp_path: Path) -> None:
    service = _service(tmp_path)
    outcome = service.build(session_id="s2", code="widget = Box(1, 1, 1)  # no `part`")
    assert not outcome.ok
    assert "contract" in outcome.message


def test_unknown_artifact_raises(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(ValueError, match="Unknown artifact_id"):
        service.validate(session_id="s3", artifact_id="missing", material="pla")


def test_list_materials(tmp_path: Path) -> None:
    service = _service(tmp_path)
    text = service.list_materials()
    assert "petg" in text and "nylon_cf" in text
