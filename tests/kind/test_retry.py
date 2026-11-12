"""Exercise real Job failure, Kubernetes backoff and the operator's retry timer."""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from cluster import REGISTRY, KindCluster, imagesync, wait_for


@pytest.mark.retry
def test_missing_remote_image_fails_then_retries_and_succeeds(cluster: KindCluster, namespace: str, destination: str) -> None:
    source = f"{REGISTRY}/test/{namespace}-missing:1"
    cluster.create(imagesync("sync", destination, source=source), namespace)
    original = cluster.get_wait("job", "imagesync-sync", namespace)
    uid = original["metadata"]["uid"]

    def failed_job() -> dict[str, Any] | None:
        job = cluster.get("job", "imagesync-sync", namespace)
        if job and any(c["type"] == "Failed" and c["status"] == "True" for c in job.get("status", {}).get("conditions", [])):
            return job
        return None

    failed = wait_for(failed_job, "Kubernetes Job to exhaust backoff", timeout=900, interval=5)
    condition = next(c for c in failed["status"]["conditions"] if c["type"] == "Failed" and c["status"] == "True")
    failed_at = datetime.datetime.fromisoformat(condition["lastTransitionTime"].replace("Z", "+00:00"))
    wait_for(
        lambda: (
            (sync := cluster.get("imagesync", "sync", namespace))
            and sync.get("status", {}).get("message") == "ImageSync job failed"
        ),
        "ImageSync failed status",
        timeout=120,
    )
    assert cluster.get_wait("imagesync", "sync", namespace)["status"]["ready"] is False
    cluster.seed(source)

    def replacement() -> dict[str, Any] | None:
        job = cluster.get("job", "imagesync-sync", namespace)
        return job if job and job["metadata"]["uid"] != uid else None

    wait_for(replacement, "operator retry after ten minutes", timeout=900, interval=10)
    assert (datetime.datetime.now(datetime.UTC) - failed_at).total_seconds() >= 600
    status = cluster.ready("sync", namespace, timeout=360)["status"]
    assert status["accepted"] is True
    assert status["ready"] is True
