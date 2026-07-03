"""Port for caching built geometry keyed by the code (and params) that produced it.

Builds are expensive (a sandboxed OCCT run per part). Identical `code` deterministically
yields identical geometry, so re-executing it is pure waste. The cache lets the toolkit
skip the executor when it has already built the same source, returning the stored artifact.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from domain.models.geometry import GeometryArtifact


def build_cache_key(code: str, params: Mapping[str, float] | None = None) -> str:
    """Stable sha256 over the build inputs.

    Params are sorted so key order never affects the hash. `code` and the params are
    joined with a separator that cannot appear in the JSON encoding, so no `code`/params
    pair can collide with a different pair by concatenation.
    """
    canonical_params = json.dumps(dict(params or {}), sort_keys=True, separators=(",", ":"))
    payload = f"{code}\x00{canonical_params}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@runtime_checkable
class BuildCache(Protocol):
    """Content-addressed store of built artifacts. Implementations must be crash-safe."""

    def get(self, key: str) -> GeometryArtifact | None: ...

    def put(self, key: str, artifact: GeometryArtifact) -> None: ...
