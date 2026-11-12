"""Scheduled syncs: CronJob creation after the first successful run, status mirroring and drift repair."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from fakek8s import ApiClient, FakeCluster, timestamp
from harness import (
    NAMESPACE,
    OperatorProcess,
    apply_job_status,
    completed_job_status,
    cronjob_of,
    imagesync,
    job_name,
    job_of,
    make_ready,
    running_job_status,
    secret,
    status_of,
    stays_true,
    wait_for,
)

SCHEDULE = "*/15 * * * *"


async def _cronjob(cluster: FakeCluster, operator: OperatorProcess) -> dict[str, Any]:
    return await wait_for(lambda: cronjob_of(cluster), desc="cronjob", operator=operator)


async def test_cronjob_is_created_after_first_success(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await make_ready(cluster, api, operator, cronSchedule=SCHEDULE, allArchitectures=True)
    cronjob = await _cronjob(cluster, operator)
    assert cronjob["metadata"]["name"] == "imagesync-alpine"
    assert cronjob["metadata"]["labels"] == {"imagesync.apexnw.dev/imagesync-cron": "alpine"}
    assert cronjob["spec"]["schedule"] == SCHEDULE
    job_spec = cronjob["spec"]["jobTemplate"]["spec"]
    assert job_spec["backoffLimit"] == 4
    pod = job_spec["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    container = pod["containers"][0]
    assert container["image"] == "quay.io/skopeo/stable:test"
    assert "--all" in container["command"][2]
    assert (
        "docker://registry.example.com/library/alpine:3.20 docker://mirror.example.com/library/alpine:3.20"
        in container["command"][2]
    )


async def test_cronjob_carries_credentials(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("secrets", NAMESPACE, secret("src-creds"))
    await api.create("secrets", NAMESPACE, secret("dst-creds"))
    await make_ready(cluster, api, operator, cronSchedule=SCHEDULE, source_secret="src-creds", dest_secret="dst-creds")
    cronjob = await _cronjob(cluster, operator)
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert volumes["creds-src"]["secret"]["secretName"] == "src-creds"
    assert volumes["creds-dest"]["secret"]["secretName"] == "dst-creds"
    script = pod["containers"][0]["command"][2]
    assert "--src-authfile" in script
    assert "--dest-authfile" in script


async def test_cronjob_waits_for_the_one_shot_job(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync(cronSchedule=SCHEDULE))
    await wait_for(lambda: job_of(cluster), desc="job", operator=operator)
    await apply_job_status(api, running_job_status())
    await stays_true(lambda: cronjob_of(cluster) is None, duration=6, desc="no cronjob yet")


async def test_status_mirrors_the_cronjob(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await make_ready(cluster, api, operator, cronSchedule=SCHEDULE)
    await _cronjob(cluster, operator)
    await wait_for(
        lambda: status_of(cluster).get("message") == "ImageSync CronJob is running", desc="cron status", operator=operator
    )
    status = status_of(cluster)
    assert status["accepted"] is True
    assert status["ready"] is True
    # Nothing has run yet, so no success time is known.
    assert "lastCompletionTime" not in status

    last_success = timestamp()
    code, body = await api.patch("cronjobs", NAMESPACE, "imagesync-alpine", {"status": {"lastSuccessfulTime": last_success}})
    assert code == 200, body
    await wait_for(lambda: status_of(cluster).get("lastCompletionTime") == last_success, desc="last success", operator=operator)
    assert status_of(cluster)["ready"] is True


async def test_cronjob_is_not_touched_when_correct(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await make_ready(cluster, api, operator, cronSchedule=SCHEDULE)
    await _cronjob(cluster, operator)
    await asyncio.sleep(7)
    assert len(cluster.calls_for("cronjobs", "create")) == 1
    assert not cluster.calls_for("cronjobs", "delete")


async def test_deleted_cronjob_is_recreated(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await make_ready(cluster, api, operator, cronSchedule=SCHEDULE)
    await _cronjob(cluster, operator)
    assert await api.delete("cronjobs", NAMESPACE, "imagesync-alpine") == 200
    await wait_for(lambda: len(cluster.calls_for("cronjobs", "create")) == 2, desc="recreate", operator=operator)
    assert (await _cronjob(cluster, operator))["spec"]["schedule"] == SCHEDULE


def _container_patch(cluster: FakeCluster, mutate: Any) -> dict[str, Any]:
    cronjob = cronjob_of(cluster)
    assert cronjob is not None
    containers = copy.deepcopy(cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"])
    mutate(containers[0])
    return {"spec": {"jobTemplate": {"spec": {"template": {"spec": {"containers": containers}}}}}}


def _pod_patch(cluster: FakeCluster, mutate: Any) -> dict[str, Any]:
    cronjob = cronjob_of(cluster)
    assert cronjob is not None
    pod = copy.deepcopy(cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"])
    mutate(pod)
    return {"spec": {"jobTemplate": {"spec": {"template": {"spec": pod}}}}}


DRIFTS = {
    "schedule": lambda c: {"spec": {"schedule": "0 0 1 1 *"}},
    "backoff-limit": lambda c: {"spec": {"jobTemplate": {"spec": {"backoffLimit": 9}}}},
    "image": lambda c: _container_patch(c, lambda ct: ct.update(image="evil/skopeo:latest")),
    "command": lambda c: _container_patch(c, lambda ct: ct.update(command=["/bin/sh", "-c", "true"])),
    "pull-policy": lambda c: _container_patch(c, lambda ct: ct.update(imagePullPolicy="Never")),
    "volume-mounts": lambda c: _container_patch(c, lambda ct: ct.update(volumeMounts=[])),
    "missing-containers": lambda c: _pod_patch(c, lambda pod: pod.update(containers=[])),
    "missing-volumes": lambda c: _pod_patch(c, lambda pod: pod.update(volumes=[])),
    "source-secret": lambda c: _pod_patch(c, lambda pod: pod["volumes"][0].update(secret={"secretName": "hijacked"})),
}


@pytest.mark.parametrize("drift", sorted(DRIFTS))
async def test_drifted_cronjob_is_replaced(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess, drift: str) -> None:
    await make_ready(cluster, api, operator, cronSchedule=SCHEDULE)
    await _cronjob(cluster, operator)
    expected = copy.deepcopy(cronjob_of(cluster))
    code, body = await api.patch("cronjobs", NAMESPACE, "imagesync-alpine", DRIFTS[drift](cluster))
    assert code == 200, body
    await api.patch("imagesyncs", NAMESPACE, "alpine", {"metadata": {"annotations": {"test/nudge": "1"}}})
    await wait_for(lambda: len(cluster.calls_for("cronjobs", "create")) == 2, desc="cronjob replaced", operator=operator)
    repaired = await _cronjob(cluster, operator)
    assert expected is not None
    assert repaired["spec"] == expected["spec"]


async def _complete_replacement_job(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess, previous_uid: str
) -> dict[str, Any]:
    job = await wait_for(
        lambda: (j := job_of(cluster)) and j["metadata"]["uid"] != previous_uid and j,
        desc="replacement one-shot job",
        operator=operator,
    )
    await apply_job_status(api, running_job_status())
    await wait_for(
        lambda: status_of(cluster).get("message") == "ImageSync job is running" and not status_of(cluster).get("ready"),
        desc="replacement job running",
        operator=operator,
    )
    await stays_true(
        lambda: not status_of(cluster).get("ready") and cronjob_of(cluster) is None,
        duration=2,
        desc="not ready and no cronjob while replacement job runs",
    )
    await apply_job_status(api, completed_job_status())
    await wait_for(lambda: status_of(cluster).get("ready"), desc="ready after replacement job", operator=operator)
    return job


async def test_schedule_change_updates_the_cronjob(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await make_ready(cluster, api, operator, cronSchedule=SCHEDULE)
    await _cronjob(cluster, operator)
    original_job = job_of(cluster)
    assert original_job is not None
    code, body = await api.patch("imagesyncs", NAMESPACE, "alpine", {"spec": {"cronSchedule": "@hourly"}})
    assert code == 200, body
    await _complete_replacement_job(cluster, api, operator, original_job["metadata"]["uid"])
    await wait_for(
        lambda: (c := cronjob_of(cluster)) is not None and c["spec"]["schedule"] == "@hourly",
        desc="new schedule",
        operator=operator,
    )
    assert status_of(cluster)["accepted"] is True


async def test_destination_change_updates_the_cronjob(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await make_ready(cluster, api, operator, cronSchedule=SCHEDULE)
    await _cronjob(cluster, operator)
    original_job = job_of(cluster)
    assert original_job is not None
    new_dest = "mirror.example.com/library/alpine:edge"
    code, body = await api.patch("imagesyncs", NAMESPACE, "alpine", {"spec": {"destination": {"image": new_dest}}})
    assert code == 200, body
    job = await _complete_replacement_job(cluster, api, operator, original_job["metadata"]["uid"])
    assert new_dest in job["spec"]["template"]["spec"]["containers"][0]["command"][2]
    await wait_for(
        lambda: (
            (c := cronjob_of(cluster)) is not None
            and new_dest in c["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["command"][2]
        ),
        desc="cronjob picks up new destination",
        operator=operator,
    )


async def test_cronjob_for_long_names_is_truncated(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    name = "c" * 45 + "-" + "d" * 30
    await make_ready(cluster, api, operator, name, cronSchedule=SCHEDULE)
    cronjob = await wait_for(lambda: cluster.stored("cronjobs", NAMESPACE, job_name(name)), desc="cronjob", operator=operator)
    assert cronjob["metadata"]["labels"]["imagesync.apexnw.dev/imagesync-cron"] == name[:50]


async def test_scheduled_sync_still_runs_a_one_shot_first(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync(cronSchedule=SCHEDULE))
    await wait_for(lambda: job_of(cluster), desc="one-shot job", operator=operator)
    assert cronjob_of(cluster) is None
    await apply_job_status(api, completed_job_status())
    await _cronjob(cluster, operator)
