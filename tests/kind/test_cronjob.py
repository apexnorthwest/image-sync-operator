"""Real CronJob creation, scheduled execution, and drift correction."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from cluster import REGISTRY, KindCluster, imagesync, wait_for


@pytest.mark.cronjob
def test_cronjob_runs_after_first_success(cluster: KindCluster, namespace: str, destination: str) -> None:
    cluster.create(imagesync("sync", destination, cronSchedule="* * * * *"), namespace)
    cluster.ready("sync", namespace)
    cron = cluster.get_wait("cronjob", "imagesync-sync", namespace)
    sync = cluster.get_wait("imagesync", "sync", namespace)
    assert cron["metadata"]["ownerReferences"][0]["uid"] == sync["metadata"]["uid"]
    assert cron["spec"]["schedule"] == "* * * * *"

    def successful_cron() -> dict[str, Any] | None:
        current = cluster.get("cronjob", "imagesync-sync", namespace)
        return current if current and current.get("status", {}).get("lastSuccessfulTime") else None

    cron = wait_for(successful_cron, "successful scheduled image copy", timeout=360, interval=5)
    last_success = cron["status"]["lastSuccessfulTime"]
    wait_for(
        lambda: (
            (sync := cluster.get("imagesync", "sync", namespace))
            and sync.get("status", {}).get("lastCompletionTime") == last_success
        ),
        "ImageSync mirrors scheduled completion",
        timeout=120,
    )
    scheduled_jobs = cluster.list_resources("jobs", namespace, "imagesync.apexnw.dev/imagesync-cron=sync")
    assert scheduled_jobs
    assert all(
        any(owner["uid"] == cron["metadata"]["uid"] for owner in job["metadata"]["ownerReferences"]) for job in scheduled_jobs
    )
    cluster.delete("imagesync", "sync", namespace)
    wait_for(
        lambda: (
            cluster.get("cronjob", "imagesync-sync", namespace) is None
            and not cluster.list_resources("jobs", namespace, "imagesync.apexnw.dev/imagesync-cron=sync")
        ),
        "CronJob and scheduled Jobs to be garbage collected",
        timeout=180,
    )


def test_cronjob_is_not_created_while_initial_copy_fails(cluster: KindCluster, namespace: str, destination: str) -> None:
    source = f"{REGISTRY}/test/{namespace}-absent:1"
    cluster.create(imagesync("sync", destination, source=source, cronSchedule="* * * * *"), namespace)
    cluster.get_wait("job", "imagesync-sync", namespace)
    wait_for(
        lambda: any(
            pod.get("status", {}).get("phase") == "Failed"
            for pod in cluster.list_resources("pods", namespace, "job-name=imagesync-sync")
        ),
        "first failed copy attempt",
    )
    sync = cluster.get_wait("imagesync", "sync", namespace)
    assert sync["status"]["ready"] is False
    assert cluster.get("cronjob", "imagesync-sync", namespace) is None


def test_deleted_cronjob_is_recreated(cluster: KindCluster, namespace: str, destination: str) -> None:
    cluster.create(imagesync("sync", destination, cronSchedule="* * * * *"), namespace)
    cluster.ready("sync", namespace)
    before = cluster.get_wait("cronjob", "imagesync-sync", namespace)
    cluster.delete("cronjob", "imagesync-sync", namespace)

    def replacement() -> dict[str, Any] | None:
        cronjob = cluster.get("cronjob", "imagesync-sync", namespace)
        return cronjob if cronjob and cronjob["metadata"]["uid"] != before["metadata"]["uid"] else None

    assert wait_for(replacement, "deleted CronJob replacement")["spec"]["schedule"] == "* * * * *"


@pytest.mark.parametrize(
    "drift", ["schedule", "backoff-limit", "image", "command", "pull-policy", "volume-mounts", "source-secret"]
)
def test_drifted_cronjob_is_recreated(cluster: KindCluster, namespace: str, destination: str, drift: str) -> None:
    cluster.create(imagesync("sync", destination, cronSchedule="* * * * *"), namespace)
    cluster.ready("sync", namespace)
    before = cluster.get_wait("cronjob", "imagesync-sync", namespace)
    if drift == "schedule":
        patch = {"spec": {"schedule": "0 0 1 1 *"}}
    elif drift == "backoff-limit":
        patch = {"spec": {"jobTemplate": {"spec": {"backoffLimit": 9}}}}
    else:
        pod = copy.deepcopy(before["spec"]["jobTemplate"]["spec"]["template"]["spec"])
        container = pod["containers"][0]
        if drift == "image":
            container["image"] = "example.invalid/skopeo:latest"
        elif drift == "command":
            container["command"] = ["/bin/sh", "-c", "true"]
        elif drift == "pull-policy":
            container["imagePullPolicy"] = "Never"
        elif drift == "volume-mounts":
            container["volumeMounts"] = []
        else:
            pod["volumes"] = [
                {"name": "creds-src", "secret": {"secretName": "hijacked"}} if v["name"] == "creds-src" else v
                for v in pod["volumes"]
            ]
        patch = {"spec": {"jobTemplate": {"spec": {"template": {"spec": pod}}}}}
    cluster.patch("cronjob", "imagesync-sync", namespace, patch)

    def repaired() -> dict[str, Any] | None:
        obj = cluster.get("cronjob", "imagesync-sync", namespace)
        return obj if obj and obj["metadata"]["uid"] != before["metadata"]["uid"] else None

    assert wait_for(repaired, "CronJob drift repair", timeout=180)["spec"] == before["spec"]
