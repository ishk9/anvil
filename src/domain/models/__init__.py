from domain.models.conversation import (
    ContentImage,
    Message,
    Role,
    ToolCall,
    ToolResult,
)
from domain.models.design_spec import (
    MATERIAL_LIBRARY,
    DesignSpec,
    LoadCase,
    Material,
    MaterialProfile,
)
from domain.models.errors import CadError, CadErrorKind
from domain.models.geometry import BoundingBox, GeometryArtifact, MassProperties
from domain.models.result import Err, Ok, Result
from domain.models.validation import (
    Severity,
    ValidationIssue,
    ValidationReport,
)

__all__ = [
    "MATERIAL_LIBRARY",
    "BoundingBox",
    "CadError",
    "CadErrorKind",
    "ContentImage",
    "DesignSpec",
    "Err",
    "GeometryArtifact",
    "LoadCase",
    "MassProperties",
    "Material",
    "MaterialProfile",
    "Message",
    "Ok",
    "Result",
    "Role",
    "Severity",
    "ToolCall",
    "ToolResult",
    "ValidationIssue",
    "ValidationReport",
]
