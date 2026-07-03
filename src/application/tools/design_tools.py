"""The concrete tools the design agent uses to reach a manufacturable part."""

from __future__ import annotations

from typing import ClassVar, cast

from application.formatting import format_validation_report
from application.tools.base import Tool, ToolContext
from domain.models.conversation import ContentImage, ToolResult
from domain.models.design_spec import DesignSpec, LoadCase, Material


class SetDesignSpecTool(Tool):
    name = "set_design_spec"
    description = (
        "Record the agreed design intent once you and the user have discussed the physics, "
        "material, loads, and mounting interfaces. Call this before building geometry."
    )
    input_schema: ClassVar[dict[str, object]] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short part name."},
            "summary": {"type": "string", "description": "One-paragraph description of intent."},
            "material": {
                "type": "string",
                "enum": [m.value for m in Material],
                "description": "Target material.",
            },
            "requirements": {"type": "array", "items": {"type": "string"}},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "interfaces": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Mounting/mating features, e.g. 'M3 holes on 30.5mm square'.",
            },
            "load_cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "force_newtons": {"type": "number"},
                    },
                    "required": ["name", "description"],
                },
            },
        },
        "required": ["title", "summary", "material"],
    }

    def run(self, ctx: ToolContext, arguments: dict[str, object], call_id: str) -> ToolResult:
        loads = tuple(
            LoadCase(
                name=str(lc.get("name", "load")),
                description=str(lc.get("description", "")),
                force_newtons=cast("float | None", lc.get("force_newtons")),
            )
            for lc in cast("list[dict[str, object]]", arguments.get("load_cases", []))
        )
        spec = DesignSpec(
            title=str(arguments["title"]),
            summary=str(arguments["summary"]),
            material=Material(str(arguments["material"])),
            requirements=tuple(cast("list[str]", arguments.get("requirements", []))),
            constraints=tuple(cast("list[str]", arguments.get("constraints", []))),
            interfaces=tuple(cast("list[str]", arguments.get("interfaces", []))),
            load_cases=loads,
        )
        ctx.session.spec = spec
        return ToolResult(
            tool_call_id=call_id,
            ok=True,
            text=(
                f"Design spec recorded: '{spec.title}' in {spec.material.value}. "
                f"Now write build123d code and call build_part."
            ),
        )


class BuildPartTool(Tool):
    name = "build_part"
    description = (
        "Execute build123d Python code to build the part. The code MUST bind the final "
        "solid to a variable named 'part' and work in millimetres. On success you receive "
        "the bounding box and rendered views to inspect. On failure you receive the error "
        "to fix and retry."
    )
    input_schema: ClassVar[dict[str, object]] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Full build123d script (algebra mode) that binds `part`.",
            },
            "note": {
                "type": "string",
                "description": "Short note on what changed vs the previous attempt.",
            },
        },
        "required": ["code"],
    }

    def run(self, ctx: ToolContext, arguments: dict[str, object], call_id: str) -> ToolResult:
        code = str(arguments["code"])
        result = ctx.toolkit.build(session=ctx.session, code=code)
        if result.is_err():
            return ToolResult(
                tool_call_id=call_id,
                ok=False,
                text=(
                    "build_part failed. Fix the code and call build_part again.\n\n"
                    + result.error.as_feedback()  # type: ignore[union-attr]
                ),
            )

        artifact = result.unwrap()
        bbox = artifact.bounding_box
        images = tuple(ContentImage(path=p) for p in artifact.render_paths)
        return ToolResult(
            tool_call_id=call_id,
            ok=True,
            text=(
                f"Built '{artifact.artifact_id}'. Bounding box: "
                f"{bbox.x_mm:.1f} x {bbox.y_mm:.1f} x {bbox.z_mm:.1f} mm. "
                f"{len(images)} rendered view(s) attached — inspect them, then refine the "
                f"code or call validate_part."
            ),
            images=images,
        )


class ValidatePartTool(Tool):
    name = "validate_part"
    description = (
        "Run mass, printability, and FEA-stub checks on the most recently built part, "
        "in the context of the recorded design spec."
    )
    input_schema: ClassVar[dict[str, object]] = {"type": "object", "properties": {}}

    def run(self, ctx: ToolContext, arguments: dict[str, object], call_id: str) -> ToolResult:
        artifact = ctx.session.latest
        if artifact is None:
            return ToolResult(
                tool_call_id=call_id,
                ok=False,
                text="No part has been built yet. Call build_part first.",
            )
        report = ctx.toolkit.validate(session=ctx.session, artifact=artifact)
        return ToolResult(
            tool_call_id=call_id,
            ok=report.passed,
            text=format_validation_report(report),
        )


class ExportPartTool(Tool):
    name = "export_part"
    description = (
        "Copy the latest part's STEP and STL into the session export folder as the final "
        "deliverable. Call only after validation passes and the user approves."
    )
    input_schema: ClassVar[dict[str, object]] = {"type": "object", "properties": {}}

    def run(self, ctx: ToolContext, arguments: dict[str, object], call_id: str) -> ToolResult:
        artifact = ctx.session.latest
        if artifact is None:
            return ToolResult(
                tool_call_id=call_id,
                ok=False,
                text="Nothing to export — build a part first.",
            )
        paths = ctx.toolkit.export(session=ctx.session, artifact=artifact)
        listed = "\n".join(f"- {p}" for p in paths)
        return ToolResult(
            tool_call_id=call_id,
            ok=True,
            text=f"Exported {len(paths)} file(s):\n{listed}",
        )
