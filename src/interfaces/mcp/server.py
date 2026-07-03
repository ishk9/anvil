"""Anvil MCP server (FastMCP, stdio transport).

This turns Anvil into an MCP tool provider: your MCP client (Cursor, Claude Desktop,
Claude Code) is the "brain" that discusses the physics with you and calls these tools.
There is no embedded LLM here — the client drives the conversation.

Tool-design pattern: one-tool-per-action (small surface). Transport: stdio, so the
client launches the process (we launch it inside Docker). Structured logs go to stderr;
stdout is reserved for the JSON-RPC protocol.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ContentBlock, TextContent

from application.hardware_formatting import format_hardware_catalog
from config.settings import get_settings
from container import Container
from interfaces.mcp.service import McpDesignService
from logging_setup import configure_logging

_BUILD_DOC = """\
Build a 3D part by executing build123d Python code, then return the bounding box and
rendered preview images so you can inspect the result.

Requirements for `code`:
- Use build123d in algebra mode; all imports are implicit (write `Box`, `Cylinder`, ...).
- Work in MILLIMETRES.
- Bind the final solid to a variable named exactly `part`.
- For revisable dimensions, read them from the injected `params` dict, e.g.
  `w = params.get("arm_width_mm", 8.0)`, and pass `params` — later revise_part calls
  override those values and re-run this same code.

Example:
    plate = Box(30, 30, 4)
    plate = plate - Pos(0, 0, 0) * Cylinder(radius=1.7, height=4)
    part = plate

On failure you get the error text — fix the code and call again. On success you get an
`artifact_id` to pass to validate_part / export_part.
"""


def create_mcp(*, host: str = "0.0.0.0", port: int = 8000) -> FastMCP:
    settings = get_settings()
    # stdio uses stdout for protocol frames, so force logs to stderr (our default sink).
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    container = Container()
    service = McpDesignService(
        toolkit=container.toolkit(),
        revisions=container.revision_service(),
        catalog=container.catalog_service(),
        assemblies=container.assembly_toolkit(),
    )

    mcp = FastMCP("Anvil", host=host, port=port)

    @mcp.tool(description="List available materials with density and tensile strength.")
    def list_materials() -> str:
        return service.list_materials()

    @mcp.tool(description=_BUILD_DOC)
    def build_part(
        code: str, params: dict[str, float] | None = None, session_id: str = "default"
    ) -> list[ContentBlock]:
        outcome = service.build(session_id=session_id, code=code, params=params)
        blocks: list[ContentBlock] = [TextContent(type="text", text=outcome.message)]
        blocks.extend(Image(path=str(p)).to_image_content() for p in outcome.image_paths)
        return blocks

    @mcp.tool(
        description=(
            "Run mass, printability, drone-balance, slicer-cost, and FEA (stress/safety "
            "factor) checks on a built part. material is one of the values from "
            "list_materials (default 'petg')."
        )
    )
    def validate_part(artifact_id: str, material: str = "petg", session_id: str = "default") -> str:
        return service.validate(session_id=session_id, artifact_id=artifact_id, material=material)

    @mcp.tool(
        description=(
            "Estimate print time, filament grams, and per-material cost for a built part "
            "by slicing it (falls back to a geometric estimate when no slicer is installed)."
        )
    )
    def estimate_cost(
        artifact_id: str, material: str = "petg", session_id: str = "default"
    ) -> str:
        return service.estimate_cost(
            session_id=session_id, artifact_id=artifact_id, material=material
        )

    @mcp.tool(
        description=(
            "Export a built part to the session export folder. Always writes STEP + STL. "
            "Pass `formats` as a comma-separated list of extras: "
            "3mf, obj, gltf, dxf, svg, drawing (2D multi-view dimensioned sheet). "
            "e.g. formats='3mf,dxf,drawing'. Call after validation passes and user approves."
        )
    )
    def export_part(
        artifact_id: str, title: str = "", formats: str = "", session_id: str = "default"
    ) -> str:
        fmt_list = [t.strip() for t in formats.split(",") if t.strip()] or None
        return service.export(
            session_id=session_id, artifact_id=artifact_id, title=title or None, formats=fmt_list
        )

    @mcp.tool(
        description=(
            "Revise a built part by overriding numeric params and re-running the SAME "
            "stored code (e.g. make arms 10% thicker: param_overrides={'arm_width_mm': 8.8}). "
            "base_version is the version to derive from (see get_design_history)."
        )
    )
    def revise_part(
        base_version: int,
        param_overrides: dict[str, float],
        note: str = "",
        session_id: str = "default",
    ) -> str:
        return service.revise(
            session_id=session_id,
            base_version=base_version,
            param_overrides=param_overrides,
            note=note,
        )

    @mcp.tool(description="Show the version lineage recorded for a session.")
    def get_design_history(session_id: str = "default") -> str:
        return service.design_history(session_id=session_id)

    @mcp.tool(description="Diff two recorded versions (params + spec fields + code).")
    def diff_versions(from_version: int, to_version: int, session_id: str = "default") -> str:
        return service.diff_versions(
            session_id=session_id, from_version=from_version, to_version=to_version
        )

    @mcp.tool(
        description=(
            "Save a built part to the searchable design catalog with tags. "
            "tags is a comma-separated list, e.g. 'drone,arm,nylon-cf'."
        )
    )
    def add_to_catalog(
        artifact_id: str, title: str = "", tags: str = "", session_id: str = "default"
    ) -> str:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] or None
        return service.add_to_catalog(
            session_id=session_id, artifact_id=artifact_id, title=title, tags=tag_list
        )

    @mcp.tool(description="Search the design catalog by free text and/or comma-separated tags.")
    def search_catalog(query: str = "", tags: str = "") -> str:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] or None
        return service.search_catalog(query=query, tags=tag_list)

    @mcp.tool(description="List every design recorded in the catalog.")
    def list_catalog() -> str:
        return service.list_catalog()

    @mcp.resource(
        "anvil://hardware/catalog",
        description="ISO metric fasteners, heat-set inserts, and bearings with fit dimensions.",
    )
    def hardware_catalog() -> str:
        return format_hardware_catalog()

    @mcp.tool(description="List the standard hardware catalog (screws, inserts, bearings).")
    def list_hardware() -> str:
        return format_hardware_catalog()

    @mcp.tool(
        description=(
            "Automatically search parameter values to meet engineering targets. The `code` "
            "must read dimensions from the injected `params` dict. ranges: "
            "[{'name','lo','hi','step'?}]. targets: [{'metric','op','value'}] with op in "
            "<=,>=,== and metric one of mass.mass_g, fea.safety_factor, fea.von_mises_max_mpa, "
            "slicer.cost_usd, drone.com_offset_xy_mm, etc. objective: {'metric','direction'} "
            "with direction in min,max. Builds+validates each candidate and returns the best "
            "target-satisfying design. FEA/slicer targets require those binaries installed."
        )
    )
    def solve_design(
        code: str,
        ranges: list[dict[str, float | str | None]],
        targets: list[dict[str, float | str]],
        objective: dict[str, str],
        max_evaluations: int = 32,
        session_id: str = "default",
    ) -> str:
        return service.solve_design(
            session_id=session_id,
            code=code,
            ranges=ranges,
            targets=targets,
            objective=objective,
            max_evaluations=max_evaluations,
        )

    @mcp.tool(
        description=(
            "Build a multi-part assembly. `instances` is a list of dicts with keys "
            "instance_id, name, material, code_ref (a key into code_by_ref binding `part`), "
            "and optional location [x,y,z] mm, rotation [rx,ry,rz] deg, quantity. `mates` is "
            "a list of {kind: rigid|coincident|concentric|offset, instance_a, instance_b, "
            "params:{...}}. `code_by_ref` maps each code_ref to build123d code binding `part`."
        )
    )
    def build_assembly(
        title: str,
        instances: list[dict[str, object]],
        code_by_ref: dict[str, str],
        mates: list[dict[str, object]] | None = None,
        session_id: str = "default",
    ) -> list[ContentBlock]:
        outcome = service.build_assembly(
            session_id=session_id,
            title=title,
            instances=instances,
            mates=mates,
            code_by_ref=code_by_ref,
        )
        blocks: list[ContentBlock] = [TextContent(type="text", text=outcome.message)]
        blocks.extend(Image(path=str(p)).to_image_content() for p in outcome.image_paths)
        return blocks

    @mcp.tool(
        description=(
            "Bill of materials for an assembly. `part_masses` maps instance_id -> single-copy "
            "mass in grams (quantity is multiplied in). Same `instances` shape as build_assembly."
        )
    )
    def assembly_bom(
        title: str, instances: list[dict[str, object]], part_masses: dict[str, float]
    ) -> str:
        return service.assembly_bom(title=title, instances=instances, part_masses=part_masses)

    @mcp.tool(
        description=(
            "Interference/collision check for an assembly. `instance_stl_paths` maps "
            "instance_id -> an STL path (part's own frame); each is placed at its resolved "
            "location/rotation and checked pairwise. Instances without an STL are skipped."
        )
    )
    def check_interference(
        title: str,
        instances: list[dict[str, object]],
        instance_stl_paths: dict[str, str],
        mates: list[dict[str, object]] | None = None,
    ) -> str:
        return service.check_interference(
            title=title,
            instances=instances,
            mates=mates,
            instance_stl_paths=instance_stl_paths,
        )

    @mcp.tool(
        description=(
            "Open the live 3D viewer for a session in Cursor's internal browser. "
            "Returns a one-click command link (simpleBrowser.show) plus the raw URL. "
            "Call this after building so the user can watch the model live."
        )
    )
    def open_viewer(session_id: str = "default") -> str:
        base = settings.viewer_url.rstrip("/")
        url = f"{base}/?session={quote(session_id)}"
        # Cursor/VS Code command URI: runs simpleBrowser.show with the URL as its arg.
        cmd_uri = "command:simpleBrowser.show?" + quote(json.dumps([url]))
        return (
            f"Live viewer for session '{session_id}':\n"
            f"- Open in Cursor's internal browser: [Open viewer]({cmd_uri})\n"
            f"- Or press \u2325\u2318V, or paste this URL: {url}"
        )

    return mcp


_TRANSPORTS = {"stdio": "stdio", "http": "streamable-http", "sse": "sse"}


def main(*, transport: str = "stdio", host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the server. `transport` is one of: stdio (default), http, sse.

    http/sse serve on host:port (streamable-http path is /mcp) — used behind ngrok.
    """
    resolved = _TRANSPORTS.get(transport, transport)
    create_mcp(host=host, port=port).run(transport=resolved)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
