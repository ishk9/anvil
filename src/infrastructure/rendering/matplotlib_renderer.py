"""Headless, GPU-free `Renderer` using matplotlib's Agg backend.

Quality is modest, but it needs no display, no GL, and no GPU — it runs anywhere,
including a slim Docker image. The renders exist to give the vision model something to
critique, not to be marketing shots. Swap this adapter for a pyvista/OSMesa one later
if higher fidelity is needed.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import structlog
import trimesh
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

log = structlog.get_logger(__name__)

# Elevation/azimuth pairs giving a readable spread of viewpoints.
_VIEW_ANGLES: tuple[tuple[float, float], ...] = (
    (25.0, 45.0),
    (25.0, 135.0),
    (25.0, 225.0),
    (70.0, 315.0),
)


class MatplotlibRenderer:
    def __init__(self, *, image_size: int = 768) -> None:
        self._image_size = image_size

    def render(self, *, stl_path: Path, out_dir: Path, views: int) -> tuple[Path, ...]:
        out_dir.mkdir(parents=True, exist_ok=True)
        mesh = trimesh.load(stl_path, force="mesh")
        if not isinstance(mesh, trimesh.Trimesh) or mesh.faces.size == 0:
            log.warning("render.empty_mesh", stl=str(stl_path))
            return ()

        triangles = mesh.vertices[mesh.faces]
        shading = self._face_shading(mesh)
        angles = _VIEW_ANGLES[: max(1, min(views, len(_VIEW_ANGLES)))]

        paths: list[Path] = []
        for idx, (elev, azim) in enumerate(angles):
            path = out_dir / f"{stl_path.stem}_view{idx}.png"
            self._render_one(triangles, shading, mesh.bounds, elev, azim, path)
            paths.append(path)

        log.info("render.done", stl=str(stl_path), views=len(paths))
        return tuple(paths)

    def _face_shading(self, mesh: trimesh.Trimesh) -> np.ndarray:
        """Simple Lambert shading from a fixed light so faces read as 3D."""
        light = np.array([0.4, 0.3, 1.0])
        light = light / np.linalg.norm(light)
        normals = mesh.face_normals
        intensity = np.clip(normals @ light, 0.0, 1.0)
        # keep a base level so back-faces aren't fully black
        return np.asarray(0.35 + 0.6 * intensity, dtype=float)

    def _render_one(
        self,
        triangles: np.ndarray,
        shading: np.ndarray,
        bounds: np.ndarray,
        elev: float,
        azim: float,
        path: Path,
    ) -> None:
        dpi = 100
        size_in = self._image_size / dpi
        fig = plt.figure(figsize=(size_in, size_in), dpi=dpi)
        ax = fig.add_subplot(111, projection="3d")

        base = np.array([0.30, 0.55, 0.85])
        facecolors = np.clip(shading[:, None] * base[None, :], 0.0, 1.0)
        collection = Poly3DCollection(triangles, facecolors=facecolors, edgecolors="none")
        ax.add_collection3d(collection)

        low, high = bounds[0], bounds[1]
        ax.set_xlim(low[0], high[0])
        ax.set_ylim(low[1], high[1])
        ax.set_zlim(low[2], high[2])
        ax.set_box_aspect(high - low)
        ax.view_init(elev=elev, azim=azim)
        ax.set_axis_off()

        fig.savefig(path, bbox_inches="tight", pad_inches=0.1, transparent=False)
        plt.close(fig)
