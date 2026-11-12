"""Failure handling: a Job that fails (remote image missing, timeout, ...) is reported and later retried."""

from __future__ import annotations

import asyncio
import datetime
from typing import Any

import pytest

from fakek8s import ApiClient, FakeCluster, timestamp
from harness import (
    NAMESPACE,
    OperatorProcess,
    apply_job_status,
    completed_job_status,
    cronjob_of,
    failed_job_status,
    imagesync,
    job_of,
    status_of,
    stays_true,
    wait_for,
)

MINUTES = datetime.timedelta(minutes=1)


async def _failed_sync(
    cluster: FakeCluster,
    api: ApiClient,
    operator: OperatorProcess,
    *,
    age_minutes: int = 0,
    reason: str = "BackoffLimitExceeded",
    **spec: Any,
) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync(**spec))
    await wait_for(lambda: job_of(cluster), desc="job", operator=operator)
    await apply_job_status(api, failed_job_status(timestamp(-age_minutes * MINUTES), reason))


@pytest.mark.parametrize("reason", ["BackoffLimitExceeded", "DeadlineExceeded"], ids=["image-unavailable", "timeout"])
async def test_failed_job_is_reported(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess, reason: str) -> None:
    await _failed_sync(cluster, api, operator, reason=reason)
    status = await wait_for(
        lambda: status_of(cluster).get("message") == "ImageSync job failed" and status_of(cluster),
        desc="failed status",
        operator=operator,
    )
    assert status["accepted"] is True
    assert status["ready"] is False
    assert "lastCompletionTime" not in status


async def test_recent_failure_is_not_retried_yet(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await _failed_sync(cluster, api, operator, age_minutes=9)
    await wait_for(lambda: status_of(cluster).get("message") == "ImageSync job failed", desc="failed", operator=operator)
    await asyncio.sleep(6)
    assert not cluster.calls_for("jobs", "delete")
    assert len(cluster.calls_for("jobs", "create")) == 1
    assert status_of(cluster)["ready"] is False


async def test_old_failure_deletes_the_job_and_retries(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await _failed_sync(cluster, api, operator, age_minutes=11)
    deleted = (await wait_for(lambda: cluster.calls_for("jobs", "delete"), desc="job deletion", operator=operator))[0]
    assert deleted.name == "imagesync-alpine"
    await wait_for(lambda: len(cluster.calls_for("jobs", "create")) == 2, desc="retry job", operator=operator)
    job = await wait_for(lambda: job_of(cluster), desc="new job", operator=operator)
    assert "status" not in job or not job["status"].get("conditions")
    assert status_of(cluster)["accepted"] is True
    assert status_of(cluster)["ready"] is False


async def test_retry_message_is_published(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await _failed_sync(cluster, api, operator, age_minutes=11)
    await wait_for(
        lambda: any(
            c.subresource == "status" and (c.data or {}).get("status", {}).get("message") == "ImageSync job failed, retrying"
            for c in cluster.calls
        ),
        desc="retry status write",
        operator=operator,
    )


async def test_retry_can_succeed(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await _failed_sync(cluster, api, operator, age_minutes=11)
    await wait_for(lambda: len(cluster.calls_for("jobs", "create")) == 2, desc="retry job", operator=operator)
    await wait_for(lambda: job_of(cluster) is not None, desc="new job present", operator=operator)
    finished = timestamp()
    await apply_job_status(api, completed_job_status(finished))
    status = await wait_for(lambda: status_of(cluster).get("ready") and status_of(cluster), desc="ready", operator=operator)
    assert status["message"] == "ImageSync job completed successfully"
    assert status["lastCompletionTime"] == finished


async def test_retries_repeat_while_the_remote_stays_broken(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    await _failed_sync(cluster, api, operator, age_minutes=11)
    for attempt in (2, 3):
        await wait_for(
            lambda attempt=attempt: len(cluster.calls_for("jobs", "create")) == attempt,
            desc=f"attempt {attempt}",
            operator=operator,
        )
        await wait_for(
            lambda: (j := job_of(cluster)) is not None and not (j.get("status") or {}), desc="fresh job", operator=operator
        )
        await apply_job_status(api, failed_job_status(timestamp(-12 * MINUTES)))
    assert len(cluster.calls_for("jobs", "delete")) >= 2


async def test_scheduled_sync_creates_no_cronjob_while_failing(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    await _failed_sync(cluster, api, operator, cronSchedule="*/15 * * * *")
    await wait_for(lambda: status_of(cluster).get("message") == "ImageSync job failed", desc="failed", operator=operator)
    await stays_true(lambda: cronjob_of(cluster) is None, duration=6, desc="no cronjob")


async def test_scheduled_sync_gets_cronjob_after_retry_succeeds(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    await _failed_sync(cluster, api, operator, age_minutes=11, cronSchedule="*/15 * * * *")
    await wait_for(lambda: len(cluster.calls_for("jobs", "create")) == 2, desc="retry job", operator=operator)
    await wait_for(lambda: job_of(cluster) is not None, desc="new job present", operator=operator)
    await apply_job_status(api, completed_job_status())
    cronjob = await wait_for(lambda: cronjob_of(cluster), desc="cronjob", operator=operator)
    assert cronjob["spec"]["schedule"] == "*/15 * * * *"
