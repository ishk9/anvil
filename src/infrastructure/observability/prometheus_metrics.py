"""Prometheus-backed `MetricsSink`, active only when `prometheus_client` is installed.

`prometheus_client` is an optional dependency. Import it lazily and fall back to the
structlog sink when it's missing, so a stock install still runs. Wiring picks this via
`build_prometheus_sink()` in `container.py`.

The `anvil_*` metric set is declared once, up front — Prometheus requires every series
(name + label set) to be registered before it's touched, unlike the structlog sink which
creates series on first use.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any

import structlog

from infrastructure.observability.structlog_metrics import StructlogMetricsSink

if TYPE_CHECKING:
    from prometheus_client import Counter, Histogram

    from domain.ports.metrics import MetricsSink

log = structlog.get_logger("anvil.metrics")

_NO_TAGS: Mapping[str, str] = {}

# Registered histograms and their label keys. Counters are created on demand.
_HISTOGRAMS: dict[str, tuple[str, ...]] = {
    "anvil_build_seconds": (),
    "anvil_validate_seconds": (),
}


def prometheus_available() -> bool:
    return find_spec("prometheus_client") is not None


def build_prometheus_sink() -> MetricsSink:
    """Return a `PrometheusMetricsSink`, or the structlog sink if the dep is absent."""
    if not prometheus_available():
        log.warning(
            "metrics.prometheus_unavailable",
            detail="prometheus_client not installed; falling back to structlog sink",
        )
        return StructlogMetricsSink()
    return PrometheusMetricsSink()


class PrometheusMetricsSink:
    """Bridges the `MetricsSink` port onto `prometheus_client` collectors.

    Counters are lazily created per name; histograms are pre-declared (see
    ``_HISTOGRAMS``). Label values come from tag *values* in a fixed key order, so a
    given metric name must always be called with the same tag keys.
    """

    def __init__(self) -> None:
        from prometheus_client import CollectorRegistry, Counter, Histogram

        self._registry = CollectorRegistry()
        self._Counter = Counter
        self._counters: dict[str, Counter] = {}
        self._histograms: dict[str, Histogram] = {
            name: Histogram(name, name, labelnames=labels, registry=self._registry)
            for name, labels in _HISTOGRAMS.items()
        }

    def _counter(self, name: str, tags: Mapping[str, str]) -> Counter:
        counter = self._counters.get(name)
        if counter is None:
            counter = self._Counter(
                name, name, labelnames=tuple(sorted(tags)), registry=self._registry
            )
            self._counters[name] = counter
        return counter

    def incr(self, name: str, tags: Mapping[str, str] = _NO_TAGS) -> None:
        counter = self._counter(name, tags)
        child: Any = counter.labels(**tags) if tags else counter
        child.inc()

    def observe(self, name: str, value: float, tags: Mapping[str, str] = _NO_TAGS) -> None:
        hist = self._histograms.get(name)
        if hist is None:  # unregistered histogram — don't crash the caller
            log.warning("metrics.unregistered_histogram", metric=name)
            return
        child: Any = hist.labels(**tags) if tags else hist
        child.observe(value)

    @contextmanager
    def timer(self, name: str, tags: Mapping[str, str] = _NO_TAGS) -> Iterator[None]:
        import time

        start = time.perf_counter()
        try:
            yield
        finally:
            self.observe(name, time.perf_counter() - start, tags)

    def render(self) -> tuple[bytes, str]:
        """Exposition payload + content type for a `/metrics` route."""
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return generate_latest(self._registry), CONTENT_TYPE_LATEST
