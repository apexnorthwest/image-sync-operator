"""ImageSync lifecycle: deletion, spec changes, restarts and independence between objects."""

from __future__ import annotations

import asyncio

import pytest

from conftest import StartOperator
from fakek8s import ApiClient, FakeCluster
from harness import (
    NAMESPACE,
    OperatorProcess,
    apply_job_status,
    completed_job_status,
    cronjob_of,
    failed_job_status,
    imagesync,
    job_of,
    make_ready,
    nudge,
    status_of,
    stays_true,
    wait_for,
)

SCHEDULE = "*/15 * * * *"


async def _stop_and_release(operator: OperatorProcess, api: ApiClient) -> None:
    """Stop the operator and drop its lease, as if the lease had already expired."""
    await operator.stop()
    await api.delete("leases", NAMESPACE, "image-sync-operator")


async def test_existing_imagesync_is_processed_at_startup(
    cluster: FakeCluster, api: ApiClient, start_operator: StartOperator
) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync())
    operator = await start_operator()
    await wait_for(lambda: job_of(cluster), desc="job", operator=operator)


async def test_restart_does_not_duplicate_work(cluster: FakeCluster, api: ApiClient, start_operator: StartOperator) -> None:
    first = await start_operator()
    await make_ready(cluster, api, first, cronSchedule=SCHEDULE)
    await wait_for(lambda: cronjob_of(cluster), desc="cronjob", operator=first)
    await _stop_and_release(first, api)

    second = await start_operator()
    await wait_for(
        lambda: status_of(cluster).get("message") == "ImageSync CronJob is running", desc="steady state", operator=second
    )
    await asyncio.sleep(7)
    assert len(cluster.calls_for("jobs", "create")) == 1
    assert len(cluster.calls_for("cronjobs", "create")) == 1
    assert not cluster.calls_for("jobs", "delete")
    assert not cluster.calls_for("cronjobs", "delete")


async def test_restart_resumes_a_running_job(cluster: FakeCluster, api: ApiClient, start_operator: StartOperator) -> None:
    first = await start_operator()
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await wait_for(lambda: job_of(cluster), desc="job", operator=first)
    await _stop_and_release(first, api)

    second = await start_operator()
    await apply_job_status(api, completed_job_status())
    await wait_for(lambda: status_of(cluster).get("ready"), desc="ready", operator=second)
    assert len(cluster.calls_for("jobs", "create")) == 1


async def test_restart_resumes_a_failed_job(cluster: FakeCluster, api: ApiClient, start_operator: StartOperator) -> None:
    first = await start_operator()
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await wait_for(lambda: job_of(cluster), desc="job", operator=first)
    await apply_job_status(api, failed_job_status())
    await wait_for(lambda: status_of(cluster).get("message") == "ImageSync job failed", desc="failed", operator=first)
    await _stop_and_release(first, api)

    second = await start_operator()
    await nudge(api)
    await wait_for(lambda: status_of(cluster).get("message") == "ImageSync job failed", desc="still failed", operator=second)
    assert len(cluster.calls_for("jobs", "create")) == 1


async def test_metadata_changes_do_not_reset_the_sync(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await make_ready(cluster, api, operator)
    before = status_of(cluster)
    await api.patch("imagesyncs", NAMESPACE, "alpine", {"metadata": {"labels": {"team": "platform"}}})
    await nudge(api)
    await asyncio.sleep(3)
    assert status_of(cluster) == before
    assert len(cluster.calls_for("jobs", "create")) == 1
    assert not cluster.calls_for("jobs", "delete")


async def test_spec_change_resets_status_before_reprocessing(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    await make_ready(cluster, api, operator)
    new_dest = "mirror.example.com/library/alpine:edge"
    await api.patch("imagesyncs", NAMESPACE, "alpine", {"spec": {"destination": {"image": new_dest}}})
    await wait_for(
        lambda: new_dest in status_of(cluster).get("lastAppliedConfig", "") and status_of(cluster).get("accepted"),
        desc="new spec applied",
        operator=operator,
    )
    assert any(
        c.subresource == "status" and (c.data or {}).get("status", {}).get("message") == "ImageSync configuration has changed"
        for c in cluster.calls
    )


async def test_spec_change_reruns_the_sync(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await make_ready(cluster, api, operator)
    new_dest = "mirror.example.com/library/alpine:edge"
    await api.patch("imagesyncs", NAMESPACE, "alpine", {"spec": {"destination": {"image": new_dest}}})
    await wait_for(lambda: cluster.calls_for("jobs", "delete"), timeout=15, desc="old job deleted", operator=operator)
    job = await wait_for(lambda: job_of(cluster), desc="new job", operator=operator)
    assert new_dest in job["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert status_of(cluster)["ready"] is False


async def test_spec_change_cleans_up_truncated_job_and_cronjob_names(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    name = "a" * 60
    await make_ready(cluster, api, operator, name, cronSchedule=SCHEDULE)
    await wait_for(lambda: cronjob_of(cluster, name), desc="cronjob", operator=operator)
    original_job = job_of(cluster, name)
    assert original_job is not None
    destination = "mirror.example.com/library/alpine:edge"
    code, body = await api.patch("imagesyncs", NAMESPACE, name, {"spec": {"destination": {"image": destination}}})
    assert code == 200, body
    job = await wait_for(
        lambda: (job := job_of(cluster, name)) and job["metadata"]["uid"] != original_job["metadata"]["uid"] and job,
        desc="replacement job",
        operator=operator,
    )
    assert destination in job["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert cronjob_of(cluster, name) is None


async def test_removing_the_schedule_removes_the_cronjob(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await make_ready(cluster, api, operator, cronSchedule=SCHEDULE)
    await wait_for(lambda: cronjob_of(cluster), desc="cronjob", operator=operator)
    original_job = job_of(cluster)
    assert original_job is not None
    code, body = await api.patch("imagesyncs", NAMESPACE, "alpine", {"spec": {"cronSchedule": None}})
    assert code == 200, body
    assert "cronSchedule" not in body["spec"]
    await wait_for(lambda: cronjob_of(cluster) is None, timeout=15, desc="cronjob removed", operator=operator)
    await wait_for(
        lambda: (job := job_of(cluster)) is not None and job["metadata"]["uid"] != original_job["metadata"]["uid"],
        desc="replacement job",
        operator=operator,
    )
    await apply_job_status(api, completed_job_status())
    await wait_for(lambda: status_of(cluster).get("ready"), desc="ready after replacement", operator=operator)
    await stays_true(lambda: cronjob_of(cluster) is None, duration=2, desc="cronjob stays removed")


async def test_deleting_the_imagesync_removes_job_and_cronjob(
    cluster: FakeCluster, api: ApiClient, start_operator: StartOperator
) -> None:
    operator = await start_operator()
    await make_ready(cluster, api, operator, cronSchedule=SCHEDULE)
    await wait_for(lambda: cronjob_of(cluster), desc="cronjob", operator=operator)
    owner = cluster.stored("imagesyncs", NAMESPACE, "alpine")
    job = job_of(cluster)
    cronjob = cronjob_of(cluster)
    assert owner is not None and job is not None and cronjob is not None
    expected_ref = {
        "apiVersion": "imagesync.apexnw.dev/v1alpha1",
        "kind": "ImageSync",
        "name": "alpine",
        "uid": owner["metadata"]["uid"],
        "controller": True,
    }
    assert job["metadata"]["ownerReferences"] == [expected_ref]
    assert cronjob["metadata"]["ownerReferences"] == [expected_ref]
    await operator.stop()
    assert await api.delete("imagesyncs", NAMESPACE, "alpine") == 200
    await wait_for(lambda: job_of(cluster) is None and cronjob_of(cluster) is None, timeout=15, desc="cleanup")


@pytest.mark.parametrize("plural,kind", [("jobs", "Job"), ("cronjobs", "CronJob")])
async def test_garbage_collection_respects_uid_namespace_and_other_owners(
    cluster: FakeCluster, api: ApiClient, plural: str, kind: str
) -> None:
    code, owner = await api.create("imagesyncs", NAMESPACE, imagesync())
    assert code == 200, owner
    code, other = await api.create("imagesyncs", NAMESPACE, imagesync("other"))
    assert code == 200, other
    owner_ref = {
        "apiVersion": owner["apiVersion"],
        "kind": owner["kind"],
        "name": "alpine",
        "uid": owner["metadata"]["uid"],
    }
    other_ref = {
        "apiVersion": other["apiVersion"],
        "kind": other["kind"],
        "name": "other",
        "uid": other["metadata"]["uid"],
    }
    for namespace, name, references in (
        (NAMESPACE, "owned", [owner_ref]),
        (NAMESPACE, "shared", [owner_ref, other_ref]),
        (NAMESPACE, "old-uid", [{**owner_ref, "uid": "old-uid"}]),
        (NAMESPACE, "unowned", []),
        ("other-ns", "foreign", [owner_ref]),
    ):
        code, body = await api.create(
            plural,
            namespace,
            {"apiVersion": "batch/v1", "kind": kind, "metadata": {"name": name, "ownerReferences": references}},
        )
        assert code == 200, body

    assert await api.delete("imagesyncs", NAMESPACE, "alpine") == 200
    assert cluster.stored(plural, NAMESPACE, "owned") is None
    for name in ("shared", "old-uid", "unowned"):
        assert cluster.stored(plural, NAMESPACE, name) is not None
    assert cluster.stored(plural, "other-ns", "foreign") is not None
    assert await api.delete("imagesyncs", NAMESPACE, "other") == 200
    assert cluster.stored(plural, NAMESPACE, "shared") is None


async def test_garbage_collection_waits_for_owner_finalizers(cluster: FakeCluster, api: ApiClient) -> None:
    manifest = imagesync()
    manifest["metadata"]["finalizers"] = ["test.example/hold"]
    code, owner = await api.create("imagesyncs", NAMESPACE, manifest)
    assert code == 200, owner
    code, body = await api.create(
        "jobs",
        NAMESPACE,
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": "dependent",
                "ownerReferences": [
                    {
                        "apiVersion": owner["apiVersion"],
                        "kind": owner["kind"],
                        "name": "alpine",
                        "uid": owner["metadata"]["uid"],
                    }
                ],
            },
        },
    )
    assert code == 200, body
    assert await api.delete("imagesyncs", NAMESPACE, "alpine") == 200
    assert cluster.stored("jobs", NAMESPACE, "dependent") is not None
    code, body = await api.patch("imagesyncs", NAMESPACE, "alpine", {"metadata": {"finalizers": []}})
    assert code == 200, body
    assert cluster.stored("jobs", NAMESPACE, "dependent") is None


async def test_failed_owner_delete_does_not_collect_dependents(cluster: FakeCluster, api: ApiClient) -> None:
    code, owner = await api.create("imagesyncs", NAMESPACE, imagesync())
    assert code == 200, owner
    code, job = await api.create(
        "jobs",
        NAMESPACE,
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": "dependent",
                "ownerReferences": [
                    {
                        "apiVersion": owner["apiVersion"],
                        "kind": owner["kind"],
                        "name": "alpine",
                        "uid": owner["metadata"]["uid"],
                    }
                ],
            },
        },
    )
    assert code == 200, job
    cluster.inject_fault("imagesyncs", "delete", 500, name="alpine", times=1)
    assert await api.delete("imagesyncs", NAMESPACE, "alpine") == 500
    assert cluster.stored("imagesyncs", NAMESPACE, "alpine") is not None
    assert cluster.stored("jobs", NAMESPACE, "dependent") is not None
    assert await api.delete("imagesyncs", NAMESPACE, "alpine") == 200
    assert cluster.stored("jobs", NAMESPACE, "dependent") is None


async def test_deleting_an_imagesync_does_not_crash_the_operator(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    await make_ready(cluster, api, operator)
    assert await api.delete("imagesyncs", NAMESPACE, "alpine") == 200
    await stays_true(lambda: operator.running, duration=3, desc="operator alive")
    await api.create("imagesyncs", NAMESPACE, imagesync("other", source="registry.example.com/team/other:1"))
    await wait_for(lambda: job_of(cluster, "other"), desc="other job", operator=operator)


async def test_recreated_imagesync_is_processed_again(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await wait_for(lambda: job_of(cluster), desc="job", operator=operator)
    await api.delete("imagesyncs", NAMESPACE, "alpine")
    await wait_for(lambda: job_of(cluster) is None, desc="original job garbage collected", operator=operator)
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await wait_for(lambda: len(cluster.calls_for("jobs", "create")) == 2, desc="second job", operator=operator)


async def test_failing_sync_does_not_block_others(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync("bad", source="not-an-image"))
    await api.create("imagesyncs", NAMESPACE, imagesync("good"))
    await wait_for(lambda: job_of(cluster, "good"), desc="good job", operator=operator)
    assert job_of(cluster, "bad") is None
    await apply_job_status(api, completed_job_status(), "good")
    await wait_for(lambda: status_of(cluster, "good").get("ready"), desc="good ready", operator=operator)
    assert status_of(cluster, "bad")["accepted"] is False
