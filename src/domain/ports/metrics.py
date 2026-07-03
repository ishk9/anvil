"""Port for emitting operational metrics (counters, histograms, timers).

Kept deliberately thin — three verbs cover everything the application needs to record.
Adapters decide where the numbers go (structured logs, Prometheus, /dev/null).
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Protocol, runtime_checkable

_NO_TAGS: Mapping[str, str] = {}


@runtime_checkable
class MetricsSink(Protocol):
    """Records counters, observations, and timed spans under a metric name + tags.

    `tags` are low-cardinality dimensions (e.g. ``result=ok``). Callers MUST NOT put
    unbounded values (ids, paths) in tags — that blows up any real backend.
    """

    def incr(self, name: str, tags: Mapping[str, str] = _NO_TAGS) -> None:
        """Increment counter `name` by one."""
        ...

    def observe(self, name: str, value: float, tags: Mapping[str, str] = _NO_TAGS) -> None:
        """Record a single sample of `value` into histogram/summary `name`."""
        ...

    def timer(
        self, name: str, tags: Mapping[str, str] = _NO_TAGS
    ) -> AbstractContextManager[None]:
        """Time the enclosed block and `observe` its wall-clock duration (seconds).

        Records on exit whether or not the block raised, so failed operations still
        contribute to latency histograms.
        """
        ...
