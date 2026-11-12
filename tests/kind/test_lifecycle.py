"""Reconciliation on spec changes, deletion and operator restarts."""

from __future__ import annotations

import time
from typing import Any

from cluster import REGISTRY, KindCluster, imagesync, wait_for


def test_metadata_change_does_not_rerun_copy(cluster: KindCluster, namespace: str, destination: str) -> None:
    cluster.create(imagesync("sync", destination), namespace)
    cluster.ready("sync", namespace)
    uid = cluster.get_wait("job", "imagesync-sync", namespace)["metadata"]["uid"]
    cluster.patch("imagesync", "sync", namespace, {"metadata": {"labels": {"team": "tests"}}})
    time.sleep(7)
    assert cluster.get_wait("job", "imagesync-sync", namespace)["metadata"]["uid"] == uid
    assert cluster.get_wait("imagesync", "sync", namespace)["status"]["ready"] is True


def test_spec_change_reruns_job_and_updates_schedule(cluster: KindCluster, namespace: str, destination: str) -> None:
    cluster.create(imagesync("sync", destination, cronSchedule="* * * * *"), namespace)
    cluster.ready("sync", namespace)
    original = cluster.get_wait("job", "imagesync-sync", namespace)
    cron = cluster.get_wait("cronjob", "imagesync-sync", namespace)
    replacement_destination = f"{REGISTRY}/test/{namespace}-updated:1"
    cluster.patch(
        "imagesync",
        "sync",
        namespace,
        {"spec": {"destination": {"image": replacement_destination}, "cronSchedule": "*/2 * * * *"}},
    )

    def replacement() -> dict[str, Any] | None:
        job = cluster.get("job", "imagesync-sync", namespace)
        return job if job and job["metadata"]["uid"] != original["metadata"]["uid"] else None

    job = wait_for(replacement, "replacement one-shot Job")
    assert replacement_destination in job["spec"]["template"]["spec"]["containers"][0]["command"][2]

    def updated_cron() -> dict[str, Any] | None:
        obj = cluster.get("cronjob", "imagesync-sync", namespace)
        return obj if obj and obj["metadata"]["uid"] != cron["metadata"]["uid"] else None

    cluster.ready("sync", namespace)
    assert wait_for(updated_cron, "replacement CronJob")["spec"]["schedule"] == "*/2 * * * *"


def test_schedule_removal_removes_cronjob(cluster: KindCluster, namespace: str, destination: str) -> None:
    cluster.create(imagesync("sync", destination, cronSchedule="* * * * *"), namespace)
    cluster.ready("sync", namespace)
    cluster.get_wait("cronjob", "imagesync-sync", namespace)
    original = cluster.get_wait("job", "imagesync-sync", namespace)
    cluster.patch("imagesync", "sync", namespace, {"spec": {"cronSchedule": None}})
    wait_for(
        lambda: cluster.get("cronjob", "imagesync-sync", namespace) is None,
        "CronJob deletion",
    )
    wait_for(
        lambda: (
            (job := cluster.get("job", "imagesync-sync", namespace)) and job["metadata"]["uid"] != original["metadata"]["uid"]
        ),
        "replacement Job",
    )
    cluster.ready("sync", namespace)
    assert cluster.get("cronjob", "imagesync-sync", namespace) is None


def test_deleted_imagesync_is_garbage_collected(cluster: KindCluster, namespace: str, destination: str) -> None:
    cluster.create(imagesync("sync", destination, cronSchedule="* * * * *"), namespace)
    cluster.ready("sync", namespace)
    cluster.get_wait("cronjob", "imagesync-sync", namespace)
    cluster.delete("imagesync", "sync", namespace)
    wait_for(
        lambda: (
            cluster.get("job", "imagesync-sync", namespace) is None
            and cluster.get("cronjob", "imagesync-sync", namespace) is None
        ),
        "dependent garbage collection",
    )


def test_operator_restart_preserves_completed_work(cluster: KindCluster, namespace: str, destination: str) -> None:
    cluster.create(imagesync("sync", destination), namespace)
    cluster.ready("sync", namespace)
    job_uid = cluster.get_wait("job", "imagesync-sync", namespace)["metadata"]["uid"]
    cluster.kubectl("rollout", "restart", "deployment/operator", "-n", "kind-operator")
    cluster.kubectl("rollout", "status", "deployment/operator", "-n", "kind-operator", "--timeout=180s")
    wait_for(
        lambda: "Operator has become the leader" in cluster.kubectl("logs", "deployment/operator", "-n", "kind-operator"),
        "restarted operator leadership",
        timeout=120,
    )
    cluster.ready("sync", namespace)
    assert cluster.get_wait("job", "imagesync-sync", namespace)["metadata"]["uid"] == job_uid
