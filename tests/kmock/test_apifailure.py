"""External failures of the Kubernetes API itself, and recovery once the API is healthy again."""

from __future__ import annotations

import datetime

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
    secret,
    status_of,
    stays_true,
    wait_for,
)


async def test_imagesync_get_error_is_survived_and_retried(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    fault = cluster.inject_fault("imagesyncs", "fetch", 500, name="alpine")
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await wait_for(lambda: fault.hits > 0, desc="fault hit", operator=operator)
    assert operator.running
    assert job_of(cluster) is None
    cluster.clear_faults()
    await nudge(api)
    await wait_for(lambda: job_of(cluster), timeout=45, desc="job after recovery", operator=operator)


async def test_secret_lookup_error_is_treated_as_missing_then_recovers(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    await api.create("secrets", NAMESPACE, secret("creds"))
    cluster.inject_fault("secrets", "fetch", 500)
    await api.create("imagesyncs", NAMESPACE, imagesync(source_secret="creds"))
    await wait_for(lambda: status_of(cluster).get("message"), desc="status message", operator=operator)
    assert not status_of(cluster).get("accepted")
    assert job_of(cluster) is None
    assert operator.running
    cluster.clear_faults()
    await nudge(api)
    await wait_for(lambda: job_of(cluster), timeout=45, desc="job after recovery", operator=operator)


async def test_failing_imagesync_list_does_not_create_work(
    cluster: FakeCluster, api: ApiClient, start_operator: StartOperator
) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync())
    cluster.inject_fault("imagesyncs", "list", 500)
    operator = await start_operator()
    await stays_true(lambda: job_of(cluster) is None, duration=4, desc="no job while the list fails")
    assert operator.running


async def test_imagesync_list_recovers_after_outage(cluster: FakeCluster, api: ApiClient, start_operator: StartOperator) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync())
    fault = cluster.inject_fault("imagesyncs", "list", 503, times=2)
    operator = await start_operator()
    await wait_for(lambda: fault.hits >= 1, desc="fault hit", operator=operator)
    await wait_for(lambda: job_of(cluster), timeout=60, desc="job after outage", operator=operator)


async def test_job_create_failure_is_retried(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    cluster.inject_fault("jobs", "create", 500, times=1)
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await wait_for(lambda: cluster.calls_for("jobs", "create"), desc="job create attempt", operator=operator)
    await stays_true(lambda: operator.running, duration=3, desc="operator alive")
    await nudge(api)
    await wait_for(lambda: job_of(cluster), timeout=45, desc="job after retry", operator=operator)


async def test_cronjob_create_failure_is_retried(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    fault = cluster.inject_fault("cronjobs", "create", 500, times=1)
    await api.create("imagesyncs", NAMESPACE, imagesync(cronSchedule="*/5 * * * *"))
    await wait_for(lambda: job_of(cluster), desc="job", operator=operator)
    await apply_job_status(api, completed_job_status())
    await wait_for(lambda: fault.hits == 1, desc="cronjob create failure", operator=operator)
    await stays_true(lambda: operator.running, duration=3, desc="operator alive")
    await wait_for(lambda: cronjob_of(cluster), timeout=45, desc="cronjob after retry", operator=operator)


async def test_job_list_failure_is_retried(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    fault = cluster.inject_fault("jobs", "list", 500, times=1)
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await wait_for(lambda: fault.hits == 1, desc="job list failure", operator=operator)
    await stays_true(lambda: operator.running, duration=3, desc="operator alive")
    await nudge(api)
    await wait_for(lambda: job_of(cluster), timeout=45, desc="job after list retry", operator=operator)


async def test_status_update_failure_is_retried(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    fault = cluster.inject_fault("imagesyncs", "update", 500, times=1)
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await wait_for(lambda: fault.hits == 1, desc="fault hit", operator=operator)
    await stays_true(lambda: operator.running, duration=3, desc="operator alive")
    await nudge(api)
    await wait_for(lambda: job_of(cluster), timeout=45, desc="job after status retry", operator=operator)


async def test_job_delete_failure_during_retry_is_survived(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:

    await api.create("imagesyncs", NAMESPACE, imagesync())
    await wait_for(lambda: job_of(cluster), desc="job", operator=operator)
    old = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=11)).strftime("%Y-%m-%dT%H:%M:%SZ")
    original_job = job_of(cluster)
    assert original_job is not None
    original_uid = original_job["metadata"]["uid"]
    fault = cluster.inject_fault("jobs", "delete", 500, times=1)
    await apply_job_status(api, failed_job_status(old))
    await wait_for(lambda: fault.hits == 1, timeout=30, desc="delete failure", operator=operator)
    await stays_true(lambda: operator.running, duration=3, desc="operator alive")
    await nudge(api)
    await wait_for(
        lambda: (job := job_of(cluster)) is not None and job["metadata"]["uid"] != original_uid,
        timeout=45,
        desc="replacement job after delete retry",
        operator=operator,
    )


@pytest.mark.parametrize(
    ("plural", "action"),
    [("jobs", "fetch"), ("jobs", "delete"), ("cronjobs", "fetch"), ("cronjobs", "delete")],
)
async def test_spec_change_cleanup_retries_api_errors(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess, plural: str, action: str
) -> None:
    await make_ready(cluster, api, operator, cronSchedule="*/5 * * * *")
    await wait_for(lambda: cronjob_of(cluster), desc="initial cronjob", operator=operator)
    original_job = job_of(cluster)
    assert original_job is not None
    fault = cluster.inject_fault(plural, action, 500, name="imagesync-alpine", times=1)
    destination = "mirror.example.com/library/alpine:edge"
    code, body = await api.patch("imagesyncs", NAMESPACE, "alpine", {"spec": {"destination": {"image": destination}}})
    assert code == 200, body
    await wait_for(lambda: fault.hits == 1, desc="cleanup failure", operator=operator)
    await stays_true(lambda: operator.running, duration=2, desc="operator survives cleanup failure")
    assert destination not in status_of(cluster).get("lastAppliedConfig", "")
    await nudge(api)
    job = await wait_for(
        lambda: (job := job_of(cluster)) and job["metadata"]["uid"] != original_job["metadata"]["uid"] and job,
        timeout=45,
        desc="replacement job after cleanup retry",
        operator=operator,
    )
    assert destination in job["spec"]["template"]["spec"]["containers"][0]["command"][2]


async def test_cronjob_list_failure_recovers(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await make_ready(cluster, api, operator, cronSchedule="*/5 * * * *")
    await wait_for(lambda: cronjob_of(cluster), desc="cronjob", operator=operator)
    fault = cluster.inject_fault("cronjobs", "list", 500, times=1)
    await nudge(api)
    await wait_for(lambda: fault.hits == 1, desc="cronjob list failure", operator=operator)
    await stays_true(lambda: operator.running, duration=2, desc="operator survives list failure")
    await nudge(api)
    await wait_for(
        lambda: status_of(cluster).get("message") == "ImageSync CronJob is running",
        timeout=45,
        desc="cronjob status after retry",
        operator=operator,
    )


@pytest.mark.parametrize("condition", ["Failed", "Complete"])
async def test_incomplete_terminal_job_status_is_retried(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess, condition: str
) -> None:
    code, body = await api.create("imagesyncs", NAMESPACE, imagesync())
    assert code == 200, body
    await wait_for(lambda: job_of(cluster), desc="job", operator=operator)
    await apply_job_status(api, {"status": {"conditions": [{"type": condition, "status": "True"}]}})
    await wait_for(lambda: "has no " in operator.log, desc="incomplete status reported", operator=operator)
    assert operator.running
    assert not status_of(cluster).get("ready")
    await apply_job_status(api, completed_job_status())
    await nudge(api)
    await wait_for(lambda: status_of(cluster).get("ready"), timeout=45, desc="ready after status repair", operator=operator)


async def test_ready_state_survives_a_transient_job_list_outage_after_restart(
    cluster: FakeCluster, api: ApiClient, start_operator: StartOperator
) -> None:
    operator = await start_operator()
    await make_ready(cluster, api, operator)
    assert status_of(cluster)["ready"] is True
