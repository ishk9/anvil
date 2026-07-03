"""Versioned design history: a lineage of parametric revisions and how to diff them.

A `DesignVersion` is an immutable snapshot of *what produced a part*: the design intent
(`DesignSpec`), the CAD `code`, and the numeric `params` the code read from. Storing all
three together is what makes "make the arms 10% thicker" a param edit + re-run rather than
a from-scratch rebuild — the same `code` runs against overridden `params`, and the result
is a new version whose `parent_version` points back at the one it was derived from.

`diff_versions` is a pure function producing the human/model-readable delta between any two
versions (param changes + changed `DesignSpec` fields), so an interface can explain what a
revision actually changed without re-deriving it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.models.design_spec import DesignSpec

# Params are named scalar dimensions the CAD code reads (e.g. {"arm_width_mm": 8.0}).
# Kept float-only so diffs are numeric and overrides stay unambiguous across the port.
Params = dict[str, float]


@dataclass(frozen=True, slots=True)
class DesignVersion:
    """One immutable point in a part's history — the inputs that made a given artifact.

    `parent_version` is None for the root version. `artifact_id` links to the built
    geometry recorded in the session/repository; it is None only if a version is recorded
    before a successful build (not the normal path). `timestamp` is an ISO-8601 string so
    the value object stays trivially JSON-serialisable and free of tz/clock dependencies.
    """

    version: int
    spec: DesignSpec
    code: str
    params: Params = field(default_factory=dict)
    parent_version: int | None = None
    artifact_id: str | None = None
    note: str = ""
    timestamp: str = ""

    def with_overrides(
        self,
        *,
        version: int,
        param_overrides: Params,
        code: str | None = None,
        spec: DesignSpec | None = None,
        note: str = "",
        timestamp: str = "",
    ) -> DesignVersion:
        """Derive a child version: inherit this version's inputs, apply overrides.

        `param_overrides` is merged onto (not replacing) the parent params so a revision
        only needs to name the dimensions it touches. `code`/`spec` fall back to the
        parent's when not supplied, which is the common "same model, new numbers" case.
        The child's `artifact_id` is intentionally left unset — it is assigned once the
        revision actually builds.
        """
        merged: Params = {**self.params, **param_overrides}
        return DesignVersion(
            version=version,
            spec=spec if spec is not None else self.spec,
            code=code if code is not None else self.code,
            params=merged,
            parent_version=self.version,
            artifact_id=None,
            note=note,
            timestamp=timestamp,
        )


@dataclass(frozen=True, slots=True)
class RevisionHistory:
    """The ordered lineage of every version recorded for a session."""

    versions: tuple[DesignVersion, ...] = field(default_factory=tuple)

    @property
    def latest(self) -> DesignVersion | None:
        return self.versions[-1] if self.versions else None

    @property
    def next_version(self) -> int:
        """Monotonic version number. Versions are 1-indexed; 0 means "none yet"."""
        return (max(v.version for v in self.versions) + 1) if self.versions else 1

    def get(self, version: int) -> DesignVersion | None:
        for v in self.versions:
            if v.version == version:
                return v
        return None

    def append(self, version: DesignVersion) -> RevisionHistory:
        return RevisionHistory(versions=(*self.versions, version))


@dataclass(frozen=True, slots=True)
class ParamDelta:
    """A single param's change between two versions.

    `before`/`after` are None when the param exists on only one side (added/removed),
    which lets an interface distinguish "8.0 -> 8.8" from "newly introduced".
    """

    name: str
    before: float | None
    after: float | None


@dataclass(frozen=True, slots=True)
class SpecFieldDelta:
    """A changed `DesignSpec` field, rendered to text so the diff stays presentation-free."""

    field: str
    before: str
    after: str


@dataclass(frozen=True, slots=True)
class VersionDiff:
    from_version: int
    to_version: int
    param_changes: tuple[ParamDelta, ...] = field(default_factory=tuple)
    spec_changes: tuple[SpecFieldDelta, ...] = field(default_factory=tuple)
    code_changed: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.param_changes or self.spec_changes or self.code_changed)


# DesignSpec fields worth diffing. `material` is a StrEnum (renders via .value); the tuple
# fields render as sorted comma-joined text so element order noise doesn't show as a change.
_SPEC_SCALAR_FIELDS = ("title", "summary", "material")
_SPEC_TUPLE_FIELDS = ("requirements", "constraints", "interfaces")


def _render_spec_field(spec: DesignSpec, name: str) -> str:
    value = getattr(spec, name)
    if name in _SPEC_TUPLE_FIELDS:
        return ", ".join(sorted(str(item) for item in value))
    return str(value)


def diff_versions(a: DesignVersion, b: DesignVersion) -> VersionDiff:
    """Pure delta from version `a` to version `b` (direction matters: a=before, b=after)."""
    param_changes: list[ParamDelta] = []
    for name in sorted(set(a.params) | set(b.params)):
        before = a.params.get(name)
        after = b.params.get(name)
        if before != after:
            param_changes.append(ParamDelta(name=name, before=before, after=after))

    spec_changes: list[SpecFieldDelta] = []
    for name in (*_SPEC_SCALAR_FIELDS, *_SPEC_TUPLE_FIELDS):
        before_text = _render_spec_field(a.spec, name)
        after_text = _render_spec_field(b.spec, name)
        if before_text != after_text:
            spec_changes.append(
                SpecFieldDelta(field=name, before=before_text, after=after_text)
            )

    return VersionDiff(
        from_version=a.version,
        to_version=b.version,
        param_changes=tuple(param_changes),
        spec_changes=tuple(spec_changes),
        code_changed=a.code != b.code,
    )
