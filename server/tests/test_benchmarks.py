from datetime import UTC, datetime, timedelta

from conftest import ADAPTER, IMAGE, LIMITS, sample_plan


def test_benchmark_uses_standalone_then_service_and_distinct_contributors(cp):
    first, node1 = cp.device()
    second, node2 = cp.device()
    view = cp.preview(plan=sample_plan(policy="full", kind="map"))
    benchmark = cp.call(
        "POST",
        "/benchmarks",
        code=201,
        body={
            "preview_id": view["id"],
            "plan_revision": 1,
            "node_ids": [node1["node_id"], node2["node_id"]],
            "concurrency": 1,
            "repetitions": 3,
        },
    )
    path = f"/benchmarks/{benchmark['id']}"
    cp.maintenance()
    assert cp.call("GET", path)["status"] == "collecting"
    start = datetime.now(UTC)
    for headers, node in [(first, node1), (second, node2)]:
        record = {
            "node_id": node["node_id"],
            "manifest_digest": benchmark["manifest_digest"],
            "config_digest": benchmark["config_digest"],
            "image_digest": IMAGE,
            "adapter": ADAPTER,
            "resource_limits": LIMITS,
            "units": 3,
            "concurrency": 1,
            "quality_passed": True,
            "duration_ms": 100,
            "started_at": start.isoformat(),
            "ended_at": (start + timedelta(milliseconds=100)).isoformat(),
            "warm": True,
        }
        for i in range(3):
            cp.call(
                "POST",
                path + "/standalone-results",
                headers=headers,
                body={**record, "repetition": i},
                code=204,
            )
    cp.maintenance()
    for series in ([first], [second], [first, second]):
        for _ in range(3):
            workers = [series[i % len(series)] for i in range(3)]
            for start in range(0, 3, len(series)):
                batch = workers[start : start + len(series)]
                assigned = [cp.claim(worker) for worker in batch]
                for worker, assignment in zip(batch, assigned, strict=True):
                    cp.complete(worker, assignment)
            cp.maintenance()
    report = cp.call("GET", path + "/export")
    assert report["status"] == "complete"
    assert len(report["service_runs"]) == 9
    assert report["standalone_units_per_second"] == {node1["node_id"]: 30, node2["node_id"]: 30}
    assert report["pooled_units_per_second"] > 0
    assert report["efficiency_percent"] == report["pooled_units_per_second"] / 60 * 100
