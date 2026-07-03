"""Canonical metric names, one place so producers and the Prometheus registry agree.

Naming follows Prometheus convention: ``anvil_`` prefix, base unit suffix (``_seconds``),
``_total`` for counters. Tag keys are documented next to each name.
"""

from __future__ import annotations

from typing import Final

# Histograms (seconds).
BUILD_SECONDS: Final = "anvil_build_seconds"
VALIDATE_SECONDS: Final = "anvil_validate_seconds"

# Counters. `BUILD_TOTAL` carries tag ``result`` in {"ok", "error"}.
BUILD_TOTAL: Final = "anvil_build_total"
CAD_TIMEOUT_TOTAL: Final = "anvil_cad_timeout_total"
CAD_OOM_TOTAL: Final = "anvil_cad_oom_total"
EXPORT_TOTAL: Final = "anvil_export_total"
