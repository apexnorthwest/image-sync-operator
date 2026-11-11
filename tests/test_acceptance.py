"""Acceptance checks: secrets, cron schedule and image references are validated before any Job is created."""

from __future__ import annotations

import pytest

from fakek8s import ApiClient, FakeCluster
from harness import (
    GOOD_DEST,
    GOOD_SRC,
    NAMESPACE,
    OperatorProcess,
    cronjob_of,
    imagesync,
    job_of,
    nudge,
    secret,
    status_of,
    stays_true,
    wait_for,
)


async def _status(cluster: FakeCluster, operator: OperatorProcess, **expect: object) -> dict[str, object]:
    def probe() -> dict[str, object] | None:
        status = status_of(cluster)
        return status if status and all(status.get(k) == v for k, v in expect.items()) else None

    return await wait_for(probe, desc=f"status {expect}", operator=operator)


async def _rejected(cluster: FakeCluster, operator: OperatorProcess) -> dict[str, object]:
    """Wait for the acceptance verdict (skipping the transient 'configuration has changed' status)."""
    return await wait_for(
        lambda: (s := status_of(cluster)) and str(s.get("message", "")).startswith("ImageSync configuration is invalid"),
        desc="rejection verdict",
        operator=operator,
    ) and status_of(cluster)


async def _assert_no_workload(cluster: FakeCluster, seconds: float = 2) -> None:
    await stays_true(lambda: job_of(cluster) is None and cronjob_of(cluster) is None, duration=seconds, desc="no job or cronjob")


async def test_valid_minimal_spec_is_accepted(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync())
    status = await _status(cluster, operator, accepted=True)
    assert status["message"] in ("ImageSync configuration is valid", "ImageSync job is running")


@pytest.mark.parametrize(
    "source",
    [
        "registry.example.com/library/alpine:3.20",
        "registry.example.com:5000/library/alpine:latest",
        "localhost.localdomain/team/app:v1_2-3",
        "registry.example.com/library/alpine@sha256:0123456789abcdef",
    ],
)
async def test_valid_source_images(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess, source: str) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync(source=source))
    await _status(cluster, operator, accepted=True)


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
async def test_invalid_source_image_is_not_accepted(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess, source: str
) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync(source=source))
    status = await _rejected(cluster, operator)
    assert status["ready"] is False
    assert "Source image URL is invalid" in str(status["message"])
    assert "Destination image URL is invalid" not in str(status["message"])
    await _assert_no_workload(cluster)


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
async def test_invalid_destination_image_is_not_accepted(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess, destination: str
) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync(destination=destination))
    status = await _rejected(cluster, operator)
    assert "Destination image URL is invalid" in str(status["message"])
    assert "Source image URL is invalid" not in str(status["message"])
    await _assert_no_workload(cluster)


@pytest.mark.parametrize("schedule", ["*/15 * * * *", "0 3 * * *", "@hourly", "@daily", "0 3 * * MON"])
async def test_valid_cron_schedule(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess, schedule: str) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync(cronSchedule=schedule))
    await _status(cluster, operator, accepted=True)


@pytest.mark.parametrize("schedule", ["banana", "* * *", "every day", "", "@sometimes"])
async def test_invalid_cron_schedule_is_not_accepted(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess, schedule: str
) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync(cronSchedule=schedule))
    status = await _rejected(cluster, operator)
    assert "Cron schedule is invalid" in str(status["message"])
    await _assert_no_workload(cluster)


async def test_missing_source_secret(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync(source_secret="src-creds"))
    status = await _rejected(cluster, operator)
    assert "Source secret error: Source secret src-creds does not exist" in str(status["message"])
    assert "Destination secret" not in str(status["message"])
    await _assert_no_workload(cluster)


async def test_missing_destination_secret(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync(dest_secret="dst-creds"))
    status = await _rejected(cluster, operator)
    assert "Destination secret error: Destination secret dst-creds does not exist" in str(status["message"])
    assert "Source secret" not in str(status["message"])
    await _assert_no_workload(cluster)


@pytest.mark.parametrize("which", ["source", "destination"])
async def test_secret_of_wrong_type_is_not_accepted(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess, which: str
) -> None:
    await api.create("secrets", NAMESPACE, secret("creds", type_="Opaque"))
    kwargs = {"source_secret" if which == "source" else "dest_secret": "creds"}
    await api.create("imagesyncs", NAMESPACE, imagesync(**kwargs))
    status = await _rejected(cluster, operator)
    label = "Source" if which == "source" else "Destination"
    assert f"{label} secret creds is not of type kubernetes.io/dockerconfigjson" in str(status["message"])
    await _assert_no_workload(cluster)


async def test_secret_in_other_namespace_does_not_count(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("secrets", "other", secret("creds", namespace="other"))
    await api.create("imagesyncs", NAMESPACE, imagesync(source_secret="creds"))
    status = await _rejected(cluster, operator)
    assert "does not exist" in str(status["message"])


async def test_all_problems_are_reported_together(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("secrets", NAMESPACE, secret("wrong", type_="Opaque"))
    manifest = imagesync(source="nope", destination="nope", source_secret="missing", dest_secret="wrong", cronSchedule="banana")
    await api.create("imagesyncs", NAMESPACE, manifest)
    status = await _rejected(cluster, operator)
    message = str(status["message"])
    assert message.startswith("ImageSync configuration is invalid: ")
    for fragment in (
        "Source secret error: Source secret missing does not exist",
        "Destination secret error: Destination secret wrong is not of type kubernetes.io/dockerconfigjson",
        "Cron schedule is invalid",
        "Source image URL is invalid",
        "Destination image URL is invalid",
    ):
        assert fragment in message
    await _assert_no_workload(cluster)


async def test_valid_secrets_are_accepted(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("secrets", NAMESPACE, secret("src-creds"))
    await api.create("secrets", NAMESPACE, secret("dst-creds"))
    await api.create("imagesyncs", NAMESPACE, imagesync(source_secret="src-creds", dest_secret="dst-creds"))
    await _status(cluster, operator, accepted=True)
    await wait_for(lambda: job_of(cluster), desc="job", operator=operator)


async def test_creating_the_missing_secret_lets_the_sync_proceed(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync(source_secret="late-creds"))
    await _rejected(cluster, operator)
    await api.create("secrets", NAMESPACE, secret("late-creds"))
    await nudge(api)
    await _status(cluster, operator, accepted=True)
    await wait_for(lambda: job_of(cluster), desc="job", operator=operator)


@pytest.mark.slow
async def test_unaccepted_sync_is_rechecked_periodically(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    """No nudge here: the operator itself must retry the acceptance checks (every 30s)."""
    await api.create("imagesyncs", NAMESPACE, imagesync(dest_secret="late-creds"))
    await _rejected(cluster, operator)
    await api.create("secrets", NAMESPACE, secret("late-creds"))
    await _status(cluster, operator, accepted=True)
    await wait_for(lambda: job_of(cluster), timeout=15, desc="job", operator=operator)


async def test_invalid_spec_can_be_fixed_by_editing_it(cluster: FakeCluster, api: ApiClient, operator: OperatorProcess) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync(source="broken"))
    await _rejected(cluster, operator)
    await api.patch("imagesyncs", NAMESPACE, "alpine", {"spec": {"source": {"image": GOOD_SRC}}})
    await _status(cluster, operator, accepted=True)
    await wait_for(lambda: job_of(cluster), desc="job", operator=operator)


async def test_valid_spec_edited_to_invalid_is_not_accepted(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await wait_for(lambda: job_of(cluster), desc="job", operator=operator)
    await api.patch("imagesyncs", NAMESPACE, "alpine", {"spec": {"destination": {"image": "nope"}}})
    status = await _rejected(cluster, operator)
    assert "Destination image URL is invalid" in str(status["message"])
    assert status["ready"] is False
    assert GOOD_DEST not in str(status["lastAppliedConfig"])
