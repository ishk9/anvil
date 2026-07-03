"""`MetricsSink` implementations that ride on structlog + hold in-memory aggregates.

`StructlogMetricsSink` is the default sink: every counter/observation emits one
structured log event (so metrics show up in the same JSON stream as everything else)
and updates in-process aggregates readable via `snapshot()`. That snapshot is what the
Prometheus adapter and any diagnostics endpoint scrape.

`NullMetricsSink` is the no-op used in tests and wherever metrics are switched off.
"""

from __future__ import annotations

import bisect
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TypedDict

import structlog

log = structlog.get_logger("anvil.metrics")


class HistogramSummary(TypedDict):
    count: int
    sum: float
    p50: float
    p95: float


class MetricsSnapshot(TypedDict):
    counters: dict[str, float]
    histograms: dict[str, HistogramSummary]

_NO_TAGS: Mapping[str, str] = {}


def _key(name: str, tags: Mapping[str, str]) -> str:
    """Stable series key: ``name`` plus sorted ``k=v`` tag pairs.

    Sorting makes the key order-independent so ``{a,b}`` and ``{b,a}`` collapse to one
    series.
    """
    if not tags:
        return name
    suffix = ",".join(f"{k}={tags[k]}" for k in sorted(tags))
    return f"{name}{{{suffix}}}"


@dataclass(slots=True)
class _Histogram:
    """Kept-sorted samples so percentiles are a cheap index lookup.

    Fine for the volumes Anvil sees (builds/validations per session). If sample counts
    ever grow unbounded, swap this for a reservoir or a real backend — the port doesn't
    change.
    """

    samples: list[float] = field(default_factory=list)

    def add(self, value: float) -> None:
        bisect.insort(self.samples, value)

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def total(self) -> float:
        return sum(self.samples)

    def percentile(self, pct: float) -> float:
        """Nearest-rank percentile (`pct` in [0, 100]); 0.0 when empty."""
        if not self.samples:
            return 0.0
        rank = max(0, min(len(self.samples) - 1, round(pct / 100 * len(self.samples)) - 1))
        return self.samples[rank]


class StructlogMetricsSink:
    """Emits structured metric events and keeps thread-safe in-memory aggregates."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._histograms: dict[str, _Histogram] = {}

    def incr(self, name: str, tags: Mapping[str, str] = _NO_TAGS) -> None:
        key = _key(name, tags)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + 1.0
            total = self._counters[key]
        log.info("metric.counter", metric=name, value=1, total=total, **tags)

    def observe(self, name: str, value: float, tags: Mapping[str, str] = _NO_TAGS) -> None:
        key = _key(name, tags)
        with self._lock:
            hist = self._histograms.setdefault(key, _Histogram())
            hist.add(value)
        log.info("metric.histogram", metric=name, value=value, **tags)

    @contextmanager
    def timer(self, name: str, tags: Mapping[str, str] = _NO_TAGS) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.observe(name, time.perf_counter() - start, tags)

    def snapshot(self) -> MetricsSnapshot:
        """Point-in-time view of every series: counters + histogram summaries.

        Shape::

            {
              "counters": {"anvil_build_total{result=ok}": 3.0, ...},
              "histograms": {
                "anvil_build_seconds": {"count": 3, "sum": 4.2, "p50": 1.1, "p95": 2.0},
                ...
              },
            }
        """
        with self._lock:
            counters = dict(self._counters)
            histograms: dict[str, HistogramSummary] = {
                key: {
                    "count": hist.count,
                    "sum": hist.total,
                    "p50": hist.percentile(50),
                    "p95": hist.percentile(95),
                }
                for key, hist in self._histograms.items()
            }
        return {"counters": counters, "histograms": histograms}


class NullMetricsSink:
    """No-op sink. Every method is a cheap nothing; `snapshot()` is empty."""

    def incr(self, name: str, tags: Mapping[str, str] = _NO_TAGS) -> None:
        return None

    def observe(self, name: str, value: float, tags: Mapping[str, str] = _NO_TAGS) -> None:
        return None

    @contextmanager
    def timer(self, name: str, tags: Mapping[str, str] = _NO_TAGS) -> Iterator[None]:
        yield

    def snapshot(self) -> MetricsSnapshot:
        return {"counters": {}, "histograms": {}}
