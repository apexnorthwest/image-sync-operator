"""CRD schema behaviour: what the API server accepts or rejects before the operator ever sees an ImageSync."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from fakek8s import ApiClient, FakeCluster, load_crd_schema
from harness import NAMESPACE, OperatorProcess, imagesync, status_of, wait_for


def test_crd_declares_expected_fields() -> None:
    schema = load_crd_schema()
    spec = schema["properties"]["spec"]
    assert schema["required"] == ["spec"]
    assert set(spec["required"]) == {"source", "destination"}
    assert set(spec["properties"]) == {
        "source",
        "destination",
        "cronSchedule",
        "allArchitectures",
        "preserveDigests",
        "extraSkopeoArguments",
    }
    status = schema["properties"]["status"]
    assert set(status["required"]) == {"accepted", "ready", "lastAppliedConfig", "message"}


def _without(path: str) -> dict[str, Any]:
    obj = imagesync()
    target = obj
    *parents, leaf = path.split(".")
    for part in parents:
        target = target[part]
    del target[leaf]
    return obj


@pytest.mark.parametrize(
    "manifest",
    [
        pytest.param(
            {"apiVersion": "imagesync.apexnw.dev/v1alpha1", "kind": "ImageSync", "metadata": {"name": "x"}}, id="no-spec"
        ),
        pytest.param(_without("spec.source"), id="no-source"),
        pytest.param(_without("spec.destination"), id="no-destination"),
        pytest.param(_without("spec.source.image"), id="no-source-image"),
        pytest.param(_without("spec.destination.image"), id="no-destination-image"),
        pytest.param(imagesync(allArchitectures="yes"), id="all-architectures-not-bool"),
        pytest.param(imagesync(preserveDigests="true"), id="preserve-digests-not-bool"),
        pytest.param(imagesync(cronSchedule=5), id="cron-not-string"),
        pytest.param(imagesync(extraSkopeoArguments=["--insecure-policy"]), id="extra-args-not-string"),
        pytest.param(imagesync(source_secret="Upper-Case"), id="source-secret-uppercase"),
        pytest.param(imagesync(source_secret="under_score"), id="source-secret-underscore"),
        pytest.param(imagesync(dest_secret="-leading-dash"), id="dest-secret-leading-dash"),
        pytest.param(imagesync(dest_secret="trailing-dash-"), id="dest-secret-trailing-dash"),
        pytest.param(imagesync(dest_secret=""), id="dest-secret-empty"),
    ],
)
async def test_invalid_manifest_is_rejected(cluster: FakeCluster, api: ApiClient, manifest: dict[str, Any]) -> None:
    manifest = copy.deepcopy(manifest)
    manifest["metadata"]["name"] = "bad"
    code, body = await api.create("imagesyncs", NAMESPACE, manifest)
    assert code == 422, body
    assert cluster.stored("imagesyncs", NAMESPACE, "bad") is None


@pytest.mark.parametrize("secret", ["a", "registry", "my-registry", "my.registry", "a1.b2-c3", "0abc"])
async def test_valid_secret_names_are_accepted(api: ApiClient, secret: str) -> None:
    code, body = await api.create("imagesyncs", NAMESPACE, imagesync(source_secret=secret, dest_secret=secret))
    assert code == 200, body


async def test_defaults_are_applied(api: ApiClient) -> None:
    code, body = await api.create("imagesyncs", NAMESPACE, imagesync())
    assert code == 200
    assert body["spec"]["allArchitectures"] is False
    assert body["spec"]["preserveDigests"] is False
    assert "cronSchedule" not in body["spec"]


async def test_merge_patch_removes_optional_fields_and_rejects_missing_required_fields(
    cluster: FakeCluster, api: ApiClient
) -> None:
    code, body = await api.create("imagesyncs", NAMESPACE, imagesync(cronSchedule="*/5 * * * *"))
    assert code == 200, body
    code, body = await api.patch("imagesyncs", NAMESPACE, "alpine", {"spec": {"cronSchedule": None}})
    assert code == 200, body
    assert "cronSchedule" not in body["spec"]
    code, body = await api.patch("imagesyncs", NAMESPACE, "alpine", {"spec": {"source": None}})
    assert code == 422, body
    stored = cluster.stored("imagesyncs", NAMESPACE, "alpine")
    assert stored is not None
    assert "source" in stored["spec"]


async def test_duplicate_name_is_rejected(api: ApiClient) -> None:
    assert (await api.create("imagesyncs", NAMESPACE, imagesync()))[0] == 200
    assert (await api.create("imagesyncs", NAMESPACE, imagesync()))[0] == 409


async def test_user_supplied_status_is_ignored_on_create(cluster: FakeCluster, api: ApiClient) -> None:
    manifest = imagesync()
    manifest["status"] = {"accepted": True, "ready": True, "lastAppliedConfig": "{}", "message": "forged"}
    code, _ = await api.create("imagesyncs", NAMESPACE, manifest)
    assert code == 200
    assert status_of(cluster) == {}


async def test_rejected_manifest_never_reaches_the_operator(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    await wait_for(lambda: cluster.calls_for("imagesyncs", "list"), desc="operator list call", operator=operator)
    code, _ = await api.create("imagesyncs", NAMESPACE, _without("spec.source.image"))
    assert code == 422
    assert not cluster.calls_for("jobs", "create")
    assert not cluster.calls_for("imagesyncs", "update")


async def test_operator_status_writes_satisfy_the_schema(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    """The fake validates status writes against the CRD, so a rejected write would crash the operator."""
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await wait_for(lambda: status_of(cluster).get("accepted"), desc="accepted", operator=operator)
    assert operator.running
    assert not any(c.subresource == "status" and c.action == "update" and not c.data for c in cluster.calls)
