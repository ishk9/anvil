"""Port for turning a mesh into preview images for the vision feedback loop."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class Renderer(Protocol):
    """Renders an STL into one or more PNGs (multiple viewpoints)."""

    def render(self, *, stl_path: Path, out_dir: Path, views: int) -> tuple[Path, ...]: ...
