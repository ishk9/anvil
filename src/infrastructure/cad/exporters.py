"""Format producers that turn a live build123d shape into extra deliverables.

This module is imported *inside the sandbox* (by ``export_runner``) — it is where the
actual OCP/build123d calls live. Keeping every format behind a small, individually
testable function means the runner is just orchestration and the CAD API is exercised in
one place. Nothing here imports application/domain code; it deals in shapes and paths.

Format notes:
- **3MF**  — ``Mesher``: carries per-shape colour + custom metadata (print/slicer native).
- **OBJ**  — hand-written from ``Shape.tessellate`` (build123d has no OBJ writer); ships a
  sibling ``.mtl`` so the part isn't flat grey in DCC tools.
- **glTF** — ``export_gltf`` (web/graphics viewers).
- **DXF/SVG** — ``project_to_viewport`` orthographic projection of the B-rep to 2D edges,
  written with ``ExportDXF`` / ``ExportSVG`` (laser/CNC and vector).
- **drawing** — a titled multi-view SVG sheet (front/top/right) with overall bounding-box
  dimension callouts. Not full GD&T; see ``build_drawing_sheet`` for the honest scope.

The functions accept ``Any`` for the shape because the concrete type (``Part``/``Solid``/
``Compound``) varies and build123d is only importable in the CAD environment.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from domain.models.export import ExportFormat

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable

# Deflection used when meshing for OBJ. Matches build123d's Mesher default so all mesh
# outputs share a comparable fidelity.
_OBJ_LINEAR_DEFLECTION = 0.001
_OBJ_ANGULAR_DEFLECTION = 0.1


def export_3mf(shape: Any, path: Path, *, label: str = "part") -> None:
    """Write a 3MF with a default colour and a small Anvil provenance metadata block."""
    from build123d import Color, Mesher

    try:
        shape.color = Color("steelblue")
        shape.label = label
    except Exception:
        pass
    mesher = Mesher()
    mesher.add_shape(shape)
    # add_code_to_metadata() reads the *caller's* source file and blows up under exec();
    # embed provenance explicitly instead.
    mesher.add_meta_data(
        name_space="anvil",
        name="generator",
        value="Anvil",
        metadata_type="str",
        must_preserve=False,
    )
    mesher.write(str(path))


def export_obj(shape: Any, path: Path, *, name: str = "part") -> None:
    """Write a Wavefront OBJ (+ sibling MTL) from a tessellation of the shape.

    build123d has no OBJ exporter, so we triangulate with ``Shape.tessellate`` and emit
    the mesh by hand. A minimal ``.mtl`` gives the part a default material so it does not
    import as untextured grey.
    """
    vertices, faces = shape.tessellate(_OBJ_LINEAR_DEFLECTION, _OBJ_ANGULAR_DEFLECTION)
    mtl_path = path.with_suffix(".mtl")
    material = "anvil_default"

    lines: list[str] = [
        "# Wavefront OBJ exported by Anvil",
        f"mtllib {mtl_path.name}",
        f"o {name}",
    ]
    for v in vertices:
        lines.append(f"v {v.X:.6f} {v.Y:.6f} {v.Z:.6f}")
    lines.append(f"usemtl {material}")
    # OBJ face indices are 1-based.
    for tri in faces:
        lines.append(f"f {tri[0] + 1} {tri[1] + 1} {tri[2] + 1}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    mtl_path.write_text(
        "\n".join(
            [
                "# Material library exported by Anvil",
                f"newmtl {material}",
                "Ka 0.200 0.200 0.200",
                "Kd 0.275 0.510 0.706",  # steelblue-ish diffuse
                "Ks 0.100 0.100 0.100",
                "Ns 20.0",
                "d 1.0",
                "illum 2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def export_gltf_file(shape: Any, path: Path) -> None:
    """Write a glTF (text). Wraps build123d's ``export_gltf`` with a location guard."""
    from build123d import Location, export_gltf

    # export_gltf requires a location; a freshly-built algebra-mode shape may lack one.
    if getattr(shape, "location", None) is None:
        shape.location = Location()
    export_gltf(shape, str(path))


def _projected_edges(
    shape: Any,
    *,
    look_from: tuple[float, float, float],
    up: tuple[float, float, float],
) -> Any:
    """Orthographic projection of ``shape`` to a Compound of visible 2D edges.

    Hidden edges are discarded — for laser/CNC and clean vector output the visible
    outline is what matters. The projection plane is defined by ``look_from``/``up``.
    """
    from build123d import Compound

    visible, _hidden = shape.project_to_viewport(look_from, viewport_up=up, look_at=(0, 0, 0))
    return Compound(children=list(visible))


def export_dxf(shape: Any, path: Path) -> None:
    """Write a top-down (plan) 2D projection as DXF for laser cutting / CAM."""
    from build123d import ExportDXF, Unit

    edges = _projected_edges(shape, look_from=(0, 0, 1000), up=(0, 1, 0))
    exporter = ExportDXF(unit=Unit.MM)
    exporter.add_layer("outline")
    exporter.add_shape(edges, layer="outline")
    exporter.write(str(path))


def export_svg(shape: Any, path: Path) -> None:
    """Write a top-down (plan) 2D projection as a plain SVG."""
    from build123d import ExportSVG, Unit

    edges = _projected_edges(shape, look_from=(0, 0, 1000), up=(0, 1, 0))
    exporter = ExportSVG(unit=Unit.MM)
    exporter.add_layer("outline")
    exporter.add_shape(edges, layer="outline")
    exporter.write(str(path))


# View definitions for the drawing sheet: (title, camera position, view-up vector).
# Standard third-angle-ish arrangement: front (looking down -Y), top (down -Z), right (down -X).
_DRAWING_VIEWS: tuple[tuple[str, tuple[float, float, float], tuple[float, float, float]], ...] = (
    ("FRONT", (0, -1000, 0), (0, 0, 1)),
    ("TOP", (0, 0, 1000), (0, 1, 0)),
    ("RIGHT", (1000, 0, 0), (0, 0, 1)),
)


def build_drawing_sheet(shape: Any, path: Path, *, title: str) -> None:
    """Write a titled multi-view SVG drawing sheet with bounding-box dimensions.

    Scope (be honest): this is a *layout* drawing, not GD&T. It renders three
    orthographic views (front/top/right) as projected outlines and annotates the overall
    bounding box (X/Y/Z extents in mm) plus a title block. It does not place per-feature
    dimensions, tolerances, or section views — build123d has no full drafting engine, so
    those are out of scope for an auto-generated sheet.

    Implementation note: we render each view to an SVG fragment via build123d, then
    compose them onto one sheet by hand (build123d exports one viewport per file). The
    dimension callouts are computed from the shape's bounding box, not from the SVG.
    """
    from build123d import ExportSVG, Unit

    bbox = shape.bounding_box()
    size = bbox.size
    dims = (float(size.X), float(size.Y), float(size.Z))

    # Render each view's outline to its own SVG string, then splice the <path>/<g> body
    # into a composed sheet. We keep a fixed cell per view and a title block underneath.
    cell_w, cell_h = 300, 220
    margin = 20
    sheet_w = cell_w * len(_DRAWING_VIEWS) + margin * 2
    sheet_h = cell_h + 110 + margin * 2

    fragments: list[str] = []
    for idx, (name, look_from, up) in enumerate(_DRAWING_VIEWS):
        edges = _projected_edges(shape, look_from=look_from, up=up)
        # Export this single view to a temp SVG string via a bytes stream-free path:
        # ExportSVG only writes files, so write to a sibling temp and read it back.
        tmp = path.with_name(f"{path.stem}.__view{idx}.svg")
        exporter = ExportSVG(unit=Unit.MM, margin=5)
        exporter.add_layer("outline")
        exporter.add_shape(edges, layer="outline")
        exporter.write(str(tmp))
        inner = _extract_svg_body(tmp.read_text(encoding="utf-8"))
        tmp.unlink(missing_ok=True)

        x = margin + idx * cell_w
        fragments.append(
            f'<g transform="translate({x + 10},{margin + 20})">'
            f'<text x="0" y="-6" font-size="11" font-family="monospace">{name}</text>'
            f'<g transform="scale(2)">{inner}</g>'
            f"</g>"
        )

    title_y = margin + cell_h + 30
    title_block = (
        f'<g font-family="monospace" font-size="12">'
        f'<text x="{margin}" y="{title_y}" font-size="15">{_svg_escape(title)}</text>'
        f'<text x="{margin}" y="{title_y + 24}">Overall bounding box (mm):</text>'
        f'<text x="{margin}" y="{title_y + 42}">'
        f"X = {dims[0]:.2f}   Y = {dims[1]:.2f}   Z = {dims[2]:.2f}</text>"
        f'<text x="{margin}" y="{title_y + 60}" font-size="10" fill="#666">'
        f"Auto-generated layout drawing - overall dimensions only, not GD&amp;T.</text>"
        f"</g>"
    )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{sheet_w}" height="{sheet_h}" '
        f'viewBox="0 0 {sheet_w} {sheet_h}">'
        f'<rect x="0" y="0" width="{sheet_w}" height="{sheet_h}" fill="white" '
        f'stroke="black" stroke-width="1"/>'
        + "".join(fragments)
        + title_block
        + "</svg>"
    )
    path.write_text(svg, encoding="utf-8")


def _extract_svg_body(svg_text: str) -> str:
    """Return the inner markup of an SVG document (everything between the root tags).

    Used to composite build123d's per-view SVG output into one sheet.
    """
    start = svg_text.find(">", svg_text.find("<svg"))
    end = svg_text.rfind("</svg>")
    if start == -1 or end == -1:
        return ""
    return svg_text[start + 1 : end]


def _svg_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# Dispatch table: format -> (shape, path) producer. DRAWING is handled separately because
# it needs the title; the runner passes it in.
_PRODUCERS: dict[ExportFormat, Callable[[Any, Path], None]] = {
    ExportFormat.THREE_MF: export_3mf,
    ExportFormat.OBJ: export_obj,
    ExportFormat.GLTF: export_gltf_file,
    ExportFormat.DXF: export_dxf,
    ExportFormat.SVG: export_svg,
}


def produce(fmt: ExportFormat, shape: Any, path: Path, *, title: str = "part") -> None:
    """Produce a single derived format for ``shape`` at ``path``.

    Raises ``KeyError`` for baseline formats (STEP/STL) — those are never produced here;
    the caller copies the existing build outputs instead.
    """
    if fmt is ExportFormat.DRAWING:
        build_drawing_sheet(shape, path, title=title)
        return
    _PRODUCERS[fmt](shape, path)
