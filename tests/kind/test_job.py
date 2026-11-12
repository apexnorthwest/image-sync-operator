"""Actual one-shot Jobs and image copies."""

from __future__ import annotations

from typing import Any

import pytest

from cluster import TLS_ARGS, KindCluster, imagesync


def test_one_shot_copies_image_and_marks_ready(cluster: KindCluster, namespace: str, destination: str) -> None:
    cluster.create(imagesync("sync", destination), namespace)
    job = cluster.get_wait("job", "imagesync-sync", namespace)
    uid = cluster.get_wait("imagesync", "sync", namespace)["metadata"]["uid"]
    assert job["metadata"]["ownerReferences"][0]["uid"] == uid
    assert job["spec"]["backoffLimit"] == 4
    assert job["spec"]["ttlSecondsAfterFinished"] == 3600
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert "skopeo copy" in container["command"][2]
    assert TLS_ARGS in container["command"][2]
    assert destination in container["command"][2]
    cluster.kubectl("wait", "--for=condition=complete", "job/imagesync-sync", "-n", namespace, "--timeout=300s")
    status = cluster.ready("sync", namespace)["status"]
    assert status["accepted"] is True
    assert status["message"] == "ImageSync job completed successfully"
    assert status["lastCompletionTime"]
    cluster.verify_image(destination)


@pytest.mark.parametrize(
    ("spec", "flag"),
    [
        ({"allArchitectures": True}, "--all"),
        ({"preserveDigests": True}, "--preserve-digests"),
        ({"extraSkopeoArguments": TLS_ARGS + " --format oci"}, "--format oci"),
    ],
)
def test_job_renders_skopeo_options(
    cluster: KindCluster, namespace: str, destination: str, spec: dict[str, Any], flag: str
) -> None:
    cluster.create(imagesync("sync", destination, **spec), namespace)
    job = cluster.get_wait("job", "imagesync-sync", namespace)
    assert flag in job["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert cluster.ready("sync", namespace)["status"]["ready"]


def test_long_resource_name_is_truncated(cluster: KindCluster, namespace: str, destination: str) -> None:
    name = "a" * 60
    cluster.create(imagesync(name, destination), namespace)
    job = cluster.get_wait("job", "imagesync-" + name[:50], namespace)
    assert job["metadata"]["labels"]["imagesync.apexnw.dev/imagesync"] == name[:50]
    assert cluster.ready(name, namespace)["status"]["ready"]
