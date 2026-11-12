"""ImageSync validation checks performed by the deployed operator."""

from __future__ import annotations

import pytest

from cluster import SOURCE, KindCluster, imagesync, manifest, wait_for


def rejection(cluster: KindCluster, namespace: str) -> dict[str, object]:
    def probe() -> dict[str, object] | None:
        obj = cluster.get("imagesync", "sync", namespace)
        status = obj.get("status", {}) if obj else {}
        return status if str(status.get("message", "")).startswith("ImageSync configuration is invalid") else None

    return wait_for(probe, "acceptance rejection")


@pytest.mark.parametrize(
    ("source", "destination", "schedule", "expected"),
    [
        ("bad-image", "registry.example.com/test/image:1", None, "Source image URL is invalid"),
        ("registry.example.com/test/image:1", "bad-image", None, "Destination image URL is invalid"),
        ("registry.example.com/test/image:1", "registry.example.com/test/image:1", "banana", "Cron schedule is invalid"),
    ],
)
def test_invalid_configuration(
    cluster: KindCluster, namespace: str, source: str, destination: str, schedule: str | None, expected: str
) -> None:
    obj = imagesync("sync", destination, source=source)
    if schedule is not None:
        obj["spec"]["cronSchedule"] = schedule
    cluster.create(obj, namespace)
    status = rejection(cluster, namespace)
    assert expected in str(status["message"])
    assert status["ready"] is False
    assert cluster.get("job", "imagesync-sync", namespace) is None


@pytest.mark.parametrize("target", ["source", "destination"])
@pytest.mark.parametrize("secret_type", ["missing", "Opaque"])
def test_invalid_registry_secret(cluster: KindCluster, namespace: str, destination: str, target: str, secret_type: str) -> None:
    if secret_type != "missing":
        cluster.create(manifest("Secret", "credentials", type=secret_type), namespace)
    obj = imagesync("sync", destination)
    obj["spec"][target]["registryLoginSecret"] = "credentials"
    cluster.create(obj, namespace)
    status = rejection(cluster, namespace)
    assert target.capitalize() + " secret" in str(status["message"])
    assert cluster.get("job", "imagesync-sync", namespace) is None


@pytest.mark.parametrize(
    "source",
    [
        "alpine:latest",
        "docker.io/alpine:latest",
        "registry.example.com/library/alpine",
        "registry.example.com/library/alpine@sha256:not-a-hex-digest",
        "https://registry.example.com/library/alpine:3",
        "registry.example.com/a/b/c:tag",
        "",
        " ",
    ],
)
def test_invalid_source_image(cluster: KindCluster, namespace: str, destination: str, source: str) -> None:
    cluster.create(imagesync("sync", destination, source=source), namespace)
    assert "Source image URL is invalid" in str(rejection(cluster, namespace)["message"])


@pytest.mark.parametrize(
    "destination",
    [
        "alpine:latest",
        "docker.io/alpine:latest",
        "mirror.example.com/library/alpine",
        "mirror.example.com/library/alpine@sha256:0123456789abcdef",
        "",
    ],
)
def test_invalid_destination_image(cluster: KindCluster, namespace: str, destination: str) -> None:
    cluster.create(imagesync("sync", destination), namespace)
    assert "Destination image URL is invalid" in str(rejection(cluster, namespace)["message"])


@pytest.mark.parametrize("schedule", ["banana", "* * *", "every day", "", "@sometimes"])
def test_invalid_cron_schedule(cluster: KindCluster, namespace: str, destination: str, schedule: str) -> None:
    cluster.create(imagesync("sync", destination, cronSchedule=schedule), namespace)
    assert "Cron schedule is invalid" in str(rejection(cluster, namespace)["message"])


@pytest.mark.parametrize("target", ["source", "destination"])
def test_valid_registry_secret_is_mounted(cluster: KindCluster, namespace: str, destination: str, target: str) -> None:
    cluster.create(
        manifest(
            "Secret",
            "credentials",
            type="kubernetes.io/dockerconfigjson",
            stringData={".dockerconfigjson": '{"auths":{}}'},
        ),
        namespace,
    )
    obj = imagesync("sync", destination)
    obj["spec"][target]["registryLoginSecret"] = "credentials"
    cluster.create(obj, namespace)
    job = cluster.get_wait("job", "imagesync-sync", namespace)
    pod = job["spec"]["template"]["spec"]
    flag = "--src-authfile" if target == "source" else "--dest-authfile"
    volume = "creds-src" if target == "source" else "creds-dest"
    assert flag in pod["containers"][0]["command"][2]
    assert next(item for item in pod["volumes"] if item["name"] == volume)["secret"]["secretName"] == "credentials"
    cluster.ready("sync", namespace)


def test_missing_secret_is_rechecked_after_creation(cluster: KindCluster, namespace: str, destination: str) -> None:
    obj = imagesync("sync", destination)
    obj["spec"]["source"]["registryLoginSecret"] = "credentials"
    cluster.create(obj, namespace)
    assert "does not exist" in str(rejection(cluster, namespace)["message"])
    cluster.create(
        manifest(
            "Secret",
            "credentials",
            type="kubernetes.io/dockerconfigjson",
            stringData={".dockerconfigjson": '{"auths":{}}'},
        ),
        namespace,
    )
    cluster.ready("sync", namespace, timeout=180)


def test_secret_in_another_namespace_is_not_accepted(cluster: KindCluster, namespace: str, destination: str) -> None:
    secret_name = f"credentials-{namespace}"
    cluster.create(
        manifest(
            "Secret",
            secret_name,
            type="kubernetes.io/dockerconfigjson",
            stringData={".dockerconfigjson": '{"auths":{}}'},
        ),
        "kind-registry",
    )
    obj = imagesync("sync", destination)
    obj["spec"]["source"]["registryLoginSecret"] = secret_name
    cluster.create(obj, namespace)
    assert "does not exist" in str(rejection(cluster, namespace)["message"])
    assert cluster.get("job", "imagesync-sync", namespace) is None


def test_multiple_configuration_errors_are_reported(cluster: KindCluster, namespace: str) -> None:
    cluster.create(manifest("Secret", "wrong", type="Opaque"), namespace)
    obj = imagesync("sync", "nope", source="nope", cronSchedule="banana")
    obj["spec"]["source"]["registryLoginSecret"] = "missing"
    obj["spec"]["destination"]["registryLoginSecret"] = "wrong"
    cluster.create(obj, namespace)
    message = str(rejection(cluster, namespace)["message"])
    for fragment in (
        "Source secret missing does not exist",
        "Destination secret wrong is not of type kubernetes.io/dockerconfigjson",
        "Cron schedule is invalid",
        "Source image URL is invalid",
        "Destination image URL is invalid",
    ):
        assert fragment in message
    assert cluster.get("job", "imagesync-sync", namespace) is None


def test_editing_invalid_spec_allows_sync(cluster: KindCluster, namespace: str, destination: str) -> None:
    cluster.create(imagesync("sync", destination, source="broken"), namespace)
    assert "Source image URL is invalid" in str(rejection(cluster, namespace)["message"])
    cluster.patch("imagesync", "sync", namespace, {"spec": {"source": {"image": SOURCE}}})
    assert cluster.ready("sync", namespace)["status"]["accepted"] is True
    cluster.verify_image(destination)


def test_editing_valid_spec_to_invalid_rejects_sync(cluster: KindCluster, namespace: str, destination: str) -> None:
    cluster.create(imagesync("sync", destination), namespace)
    cluster.ready("sync", namespace)
    cluster.patch("imagesync", "sync", namespace, {"spec": {"destination": {"image": "nope"}}})
    status = rejection(cluster, namespace)
    assert "Destination image URL is invalid" in str(status["message"])
    assert status["ready"] is False
