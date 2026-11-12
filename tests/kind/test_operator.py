"""Operator startup, leadership and namespace scope on Kubernetes."""

from __future__ import annotations

import pytest

from cluster import KindCluster, imagesync, manifest, wait_for


def test_operator_has_leadership(cluster: KindCluster) -> None:
    lease = cluster.get_wait("lease", "image-sync-operator", "kind-operator")
    assert lease["spec"]["holderIdentity"]
    deployment = cluster.get_wait("deployment", "operator", "kind-operator")
    assert deployment["status"]["availableReplicas"] == 1


def test_cluster_scope_processes_test_namespace(cluster: KindCluster, namespace: str, destination: str) -> None:
    cluster.create(imagesync("sync", destination), namespace)
    assert cluster.ready("sync", namespace)["status"]["accepted"] is True


@pytest.mark.parametrize("bad_config", ["missing", "malformed"])
def test_invalid_config_aborts_operator_startup(cluster: KindCluster, bad_config: str) -> None:
    pod = manifest(
        "Pod",
        "invalid-config",
        spec={
            "serviceAccountName": "operator",
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "operator",
                    "image": cluster.image,
                    "imagePullPolicy": "Never",
                    "env": [{"name": "IMAGE_SYNC_OPERATOR_CONFIG", "value": "/bad/config.yaml"}],
                }
            ],
        },
    )
    if bad_config == "malformed":
        cluster.create(manifest("Secret", "invalid-config", stringData={"config.yaml": "bad: [not yaml"}), "kind-operator")
        pod["spec"]["containers"][0]["volumeMounts"] = [{"name": "config", "mountPath": "/bad"}]
        pod["spec"]["volumes"] = [{"name": "config", "secret": {"secretName": "invalid-config"}}]
    try:
        cluster.create(pod, "kind-operator")

        def terminated() -> bool:
            obj = cluster.get("pod", "invalid-config", "kind-operator")
            return bool(obj and obj.get("status", {}).get("phase") == "Failed")

        wait_for(terminated, "operator with invalid configuration to exit", timeout=120)
        assert "Failed to read config file" in cluster.kubectl("logs", "pod/invalid-config", "-n", "kind-operator")
    finally:
        cluster.delete("pod", "invalid-config", "kind-operator")
        if bad_config == "malformed":
            cluster.delete("secret", "invalid-config", "kind-operator")
