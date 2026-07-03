"""Unit tests for the metrics sinks — aggregation, percentiles, tags, timers."""

from __future__ import annotations

import time

from infrastructure.observability.structlog_metrics import (
    NullMetricsSink,
    StructlogMetricsSink,
)


def test_incr_accumulates_per_series() -> None:
    sink = StructlogMetricsSink()
    sink.incr("anvil_export_total")
    sink.incr("anvil_export_total")
    sink.incr("anvil_export_total")

    assert sink.snapshot()["counters"] == {"anvil_export_total": 3.0}


def test_tags_partition_counter_series() -> None:
    sink = StructlogMetricsSink()
    sink.incr("anvil_build_total", {"result": "ok"})
    sink.incr("anvil_build_total", {"result": "ok"})
    sink.incr("anvil_build_total", {"result": "error"})

    counters = sink.snapshot()["counters"]
    assert counters == {
        "anvil_build_total{result=ok}": 2.0,
        "anvil_build_total{result=error}": 1.0,
    }


def test_tag_key_order_does_not_split_series() -> None:
    sink = StructlogMetricsSink()
    sink.incr("m", {"a": "1", "b": "2"})
    sink.incr("m", {"b": "2", "a": "1"})

    assert sink.snapshot()["counters"] == {"m{a=1,b=2}": 2.0}


def test_observe_aggregates_count_and_sum() -> None:
    sink = StructlogMetricsSink()
    for v in (1.0, 2.0, 3.0):
        sink.observe("anvil_build_seconds", v)

    hist = sink.snapshot()["histograms"]["anvil_build_seconds"]
    assert hist["count"] == 3
    assert hist["sum"] == 6.0


def test_percentiles_p50_p95() -> None:
    sink = StructlogMetricsSink()
    for v in range(1, 101):  # 1..100
        sink.observe("lat", float(v))

    hist = sink.snapshot()["histograms"]["lat"]
    # Nearest-rank: p50 -> index 49 (=50), p95 -> index 94 (=95).
    assert hist["p50"] == 50.0
    assert hist["p95"] == 95.0


def test_percentile_empty_histogram_is_zero() -> None:
    # A histogram only appears once observed, but the guard still returns 0.0.
    from infrastructure.observability.structlog_metrics import _Histogram

    assert _Histogram().percentile(95) == 0.0


def test_timer_records_a_duration_observation() -> None:
    sink = StructlogMetricsSink()
    with sink.timer("anvil_build_seconds"):
        time.sleep(0.01)

    hist = sink.snapshot()["histograms"]["anvil_build_seconds"]
    assert hist["count"] == 1
    assert hist["sum"] > 0.0


def test_timer_records_even_when_body_raises() -> None:
    sink = StructlogMetricsSink()
    try:
        with sink.timer("anvil_build_seconds"):
            raise ValueError("boom")
    except ValueError:
        pass

    assert sink.snapshot()["histograms"]["anvil_build_seconds"]["count"] == 1


def test_null_sink_is_inert() -> None:
    sink = NullMetricsSink()
    sink.incr("x")
    sink.observe("y", 1.0)
    with sink.timer("z"):
        pass

    assert sink.snapshot() == {"counters": {}, "histograms": {}}


def test_sinks_satisfy_the_port() -> None:
    from domain.ports.metrics import MetricsSink

    assert isinstance(StructlogMetricsSink(), MetricsSink)
    assert isinstance(NullMetricsSink(), MetricsSink)
