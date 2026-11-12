"""Reconciliation when the real Kubernetes API denies and later permits a request."""

from __future__ import annotations

from typing import Any

from cluster import KindCluster, imagesync, wait_for


def test_job_creation_forbidden_then_retried(cluster: KindCluster, namespace: str, destination: str) -> None:
    role = cluster.get_wait("clusterrole", "kind-operator", namespace)
    original: list[dict[str, Any]] = role["rules"]
    restricted = [
        {**rule, "verbs": [verb for verb in rule["verbs"] if verb != "create"]} if rule.get("apiGroups") == ["batch"] else rule
        for rule in original
    ]
    cluster.patch("clusterrole", "kind-operator", namespace, {"rules": restricted})
    try:
        cluster.create(imagesync("sync", destination), namespace)
        wait_for(
            lambda: "forbidden" in cluster.kubectl("logs", "deployment/operator", "-n", "kind-operator").lower(),
            "job creation forbidden",
            timeout=120,
        )
        assert cluster.get("job", "imagesync-sync", namespace) is None
        assert cluster.get_wait("imagesync", "sync", namespace)["status"]["ready"] is False
    finally:
        cluster.patch("clusterrole", "kind-operator", namespace, {"rules": original})
    cluster.ready("sync", namespace, timeout=180)
