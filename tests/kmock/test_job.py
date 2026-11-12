"""One-shot Job: rendering of the Job object and status propagation from the Job to the ImageSync."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from conftest import StartOperator
from fakek8s import ApiClient, FakeCluster, timestamp
from harness import (
    NAMESPACE,
    OperatorProcess,
    apply_job_status,
    completed_job_status,
    cronjob_of,
    default_config,
    imagesync,
    job_name,
    job_of,
    running_job_status,
    secret,
    status_of,
    stays_true,
    wait_for,
)


async def _job(cluster: FakeCluster, operator: OperatorProcess, name: str = "alpine") -> dict[str, Any]:
    return await wait_for(lambda: job_of(cluster, name), desc=f"job for {name}", operator=operator)


def _script(job: dict[str, Any]) -> str:
    return job["spec"]["template"]["spec"]["containers"][0]["command"][2]


async def test_job_object_is_rendered_from_spec(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync())
    job = await _job(cluster, operator)
    assert job["metadata"]["name"] == "imagesync-alpine"
    assert job["metadata"]["labels"] == {"imagesync.apexnw.dev/imagesync": "alpine"}
    spec = job["spec"]
    assert spec["backoffLimit"] == 4
    assert spec["ttlSecondsAfterFinished"] == 3600
    assert spec["template"]["metadata"]["labels"] == {"imagesync.apexnw.dev/imagesync": "alpine"}
    pod = spec["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    (container,) = pod["containers"]
    assert container["name"] == "skopeo"
    assert container["image"] == "quay.io/skopeo/stable:test"
    assert container["imagePullPolicy"] == "IfNotPresent"
    assert container["command"][:2] == ["/bin/bash", "-c"]
    script = _script(job)
    assert "skopeo copy" in script
    assert "docker://registry.example.com/library/alpine:3.20 docker://mirror.example.com/library/alpine:3.20" in script
    for flag in ("--all", "--preserve-digests", "--src-authfile", "--dest-authfile", "--debug"):
        assert flag not in script
    assert {m["mountPath"] for m in container["volumeMounts"]} == {"/creds/src", "/creds/dest"}


async def test_job_is_created_only_once(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await _job(cluster, operator)
    await asyncio.sleep(7)  # more than one 5s requeue
    assert len(cluster.calls_for("jobs", "create")) == 1


@pytest.mark.parametrize(
    ("spec", "present", "absent"),
    [
        ({"allArchitectures": True}, ["--all"], ["--preserve-digests"]),
        ({"preserveDigests": True}, ["--preserve-digests"], ["--all"]),
        ({"allArchitectures": True, "preserveDigests": True}, ["--all", "--preserve-digests"], []),
        ({"allArchitectures": False, "preserveDigests": False}, [], ["--all", "--preserve-digests"]),
        ({"extraSkopeoArguments": "--dest-compress-format zstd --format oci"}, ["--dest-compress-format zstd --format oci"], []),
    ],
    ids=["all-arch", "preserve-digests", "both", "explicit-false", "extra-args"],
)
async def test_skopeo_flags(
    cluster: FakeCluster,
    api: ApiClient,
    operator: OperatorProcess,
    spec: dict[str, Any],
    present: list[str],
    absent: list[str],
) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync(**spec))
    script = _script(await _job(cluster, operator))
    for flag in present:
        assert flag in script
    for flag in absent:
        assert flag not in script


async def test_credentials_are_mounted_and_passed_to_skopeo(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    await api.create("secrets", NAMESPACE, secret("src-creds"))
    await api.create("secrets", NAMESPACE, secret("dst-creds"))
    await api.create("imagesyncs", NAMESPACE, imagesync(source_secret="src-creds", dest_secret="dst-creds"))
    job = await _job(cluster, operator)
    script = _script(job)
    assert "--src-authfile /creds/src/.dockerconfigjson" in script
    assert "--dest-authfile /creds/dest/.dockerconfigjson" in script
    volumes = {v["name"]: v for v in job["spec"]["template"]["spec"]["volumes"]}
    assert volumes["creds-src"]["secret"]["secretName"] == "src-creds"
    assert volumes["creds-dest"]["secret"]["secretName"] == "dst-creds"


@pytest.mark.parametrize("which", ["source", "destination"])
async def test_only_the_configured_credential_is_used(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess, which: str
) -> None:
    await api.create("secrets", NAMESPACE, secret("creds"))
    kwargs = {"source_secret" if which == "source" else "dest_secret": "creds"}
    await api.create("imagesyncs", NAMESPACE, imagesync(**kwargs))
    script = _script(await _job(cluster, operator))
    assert ("--src-authfile" in script) is (which == "source")
    assert ("--dest-authfile" in script) is (which == "destination")


async def test_global_skopeo_config_is_applied(cluster: FakeCluster, api: ApiClient, start_operator: StartOperator) -> None:
    config = default_config()
    config["skopeo"] = {
        "image": "registry.internal/skopeo:9",
        "image_pull_policy": "Always",
        "ca_trust_bundle": "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----",
    }
    operator = await start_operator(config)
    await api.create("imagesyncs", NAMESPACE, imagesync())
    job = await _job(cluster, operator)
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "registry.internal/skopeo:9"
    assert container["imagePullPolicy"] == "Always"
    assert "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----" in _script(job)


async def test_debug_env_enables_skopeo_debug(cluster: FakeCluster, api: ApiClient, start_operator: StartOperator) -> None:
    operator = await start_operator(env={"DEBUG": "1"})
    await api.create("imagesyncs", NAMESPACE, imagesync())
    assert "--debug" in _script(await _job(cluster, operator))


async def test_long_names_are_truncated_for_job_and_label(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    name = "a" * 40 + "-" + "b" * 40
    await api.create("imagesyncs", NAMESPACE, imagesync(name=name))
    job = await _job(cluster, operator, name)
    assert job["metadata"]["name"] == job_name(name) == f"imagesync-{name[:50]}"
    assert job["metadata"]["labels"]["imagesync.apexnw.dev/imagesync"] == name[:50]


async def test_running_job_keeps_sync_not_ready(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await _job(cluster, operator)
    await apply_job_status(api, running_job_status())
    status = await wait_for(
        lambda: status_of(cluster).get("message") == "ImageSync job is running" and status_of(cluster),
        desc="running status",
        operator=operator,
    )
    assert status["accepted"] is True
    assert status["ready"] is False
    assert "lastCompletionTime" not in status


async def test_job_with_complete_condition_false_is_still_running(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await _job(cluster, operator)
    stamp = timestamp()
    await apply_job_status(
        api, {"status": {"conditions": [{"type": "Complete", "status": "False", "lastTransitionTime": stamp}]}}
    )
    await stays_true(lambda: status_of(cluster).get("ready") is False, duration=3, desc="not ready")


async def test_completed_job_marks_sync_ready(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await _job(cluster, operator)
    finished = timestamp()
    await apply_job_status(api, completed_job_status(finished))
    status = await wait_for(lambda: status_of(cluster).get("ready") and status_of(cluster), desc="ready", operator=operator)
    assert status["accepted"] is True
    assert status["message"] == "ImageSync job completed successfully"
    assert status["lastCompletionTime"] == finished


async def test_one_shot_sync_without_schedule_stays_idle_after_success(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await _job(cluster, operator)
    await apply_job_status(api, completed_job_status())
    await wait_for(lambda: status_of(cluster).get("ready"), desc="ready", operator=operator)
    await asyncio.sleep(7)
    assert cronjob_of(cluster) is None
    assert len(cluster.calls_for("jobs", "create")) == 1
    assert not cluster.calls_for("jobs", "delete")
    assert status_of(cluster)["ready"] is True
    assert operator.running


async def test_independent_syncs_get_independent_jobs(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync("one", source="registry.example.com/team/one:1"))
    await api.create("imagesyncs", NAMESPACE, imagesync("two", source="registry.example.com/team/two:2"))
    job_one = await _job(cluster, operator, "one")
    job_two = await _job(cluster, operator, "two")
    assert "team/one:1" in _script(job_one)
    assert "team/two:2" in _script(job_two)
    await apply_job_status(api, completed_job_status(), "one")
    await wait_for(lambda: status_of(cluster, "one").get("ready"), desc="one ready", operator=operator)
    await stays_true(lambda: status_of(cluster, "two").get("ready") is False, duration=2, desc="two not ready")
