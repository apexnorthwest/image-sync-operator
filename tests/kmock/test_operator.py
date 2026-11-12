"""Operator startup: configuration file, scoping modes and leader election."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import StartOperator
from fakek8s import ApiClient, FakeCluster, timestamp
from harness import (
    NAMESPACE,
    OperatorProcess,
    default_config,
    imagesync,
    job_of,
    status_of,
    stays_true,
    wait_for,
)


async def _exits(operator: OperatorProcess, timeout: float = 15) -> int:
    return await operator.wait_exit(timeout)


async def test_missing_config_file_aborts_startup(cluster: FakeCluster, start_operator: StartOperator) -> None:
    operator = await start_operator(env={"IMAGE_SYNC_OPERATOR_CONFIG": "/nonexistent/config.yaml"})
    assert await _exits(operator) != 0
    assert "Failed to read config file" in operator.log


@pytest.mark.parametrize(
    "config",
    [
        pytest.param("this: [is not: valid yaml", id="malformed-yaml"),
        pytest.param("", id="empty"),
        pytest.param("cluster_scope: false\nwatched_namespaces: []\n", id="missing-skopeo"),
        pytest.param("cluster_scope: false\nskopeo: {image: x, image_pull_policy: Always}\n", id="missing-watched-namespaces"),
        pytest.param("watched_namespaces: []\nskopeo: {image: x, image_pull_policy: Always}\n", id="missing-cluster-scope"),
        pytest.param(
            "cluster_scope: maybe\nwatched_namespaces: []\nskopeo: {image: x, image_pull_policy: Always}\n",
            id="bad-cluster-scope",
        ),
        pytest.param(
            "cluster_scope: false\nwatched_namespaces: []\nskopeo: {image_pull_policy: Always}\n", id="missing-skopeo-image"
        ),
        pytest.param("cluster_scope: false\nwatched_namespaces: []\nskopeo: {image: x}\n", id="missing-pull-policy"),
    ],
)
async def test_invalid_config_aborts_startup(cluster: FakeCluster, start_operator: StartOperator, config: str) -> None:
    operator = await start_operator(config)
    assert await _exits(operator) != 0
    assert "Failed to read config file" in operator.log


async def test_operator_stays_standby_while_the_lease_api_is_down(
    cluster: FakeCluster, api: ApiClient, start_operator: StartOperator
) -> None:
    cluster.inject_fault("leases", "fetch", 503)
    cluster.inject_fault("leases", "create", 503)
    operator = await start_operator()
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await stays_true(lambda: job_of(cluster) is None, duration=4, desc="no work without the lease")
    assert "Operator has become the leader" not in operator.log


async def test_namespace_scoped_operator_ignores_other_namespaces(
    cluster: FakeCluster, api: ApiClient, operator: OperatorProcess
) -> None:
    await api.create("imagesyncs", "other", imagesync("alien", namespace="other"))
    await api.create("imagesyncs", NAMESPACE, imagesync("local"))
    await wait_for(lambda: job_of(cluster, "local"), desc="local job", operator=operator)
    assert job_of(cluster, "alien", "other") is None
    assert status_of(cluster, "alien", "other") == {}
    assert not any(c.namespace == "other" and c.plural == "jobs" for c in cluster.calls)


async def test_empty_watched_namespaces_defaults_to_install_namespace(
    cluster: FakeCluster, api: ApiClient, start_operator: StartOperator
) -> None:
    operator = await start_operator(default_config(watched_namespaces=[]))
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await wait_for(lambda: job_of(cluster), desc="job", operator=operator)


async def test_cluster_scope_watches_every_namespace(cluster: FakeCluster, api: ApiClient, start_operator: StartOperator) -> None:
    operator = await start_operator(default_config(cluster_scope=True, watched_namespaces=[]))
    await api.create("imagesyncs", "team-a", imagesync("sync", namespace="team-a"))
    await api.create("imagesyncs", "team-b", imagesync("sync", namespace="team-b"))
    await wait_for(lambda: job_of(cluster, "sync", "team-a"), desc="team-a job", operator=operator)
    await wait_for(lambda: job_of(cluster, "sync", "team-b"), desc="team-b job", operator=operator)
    assert "cluster-scoped mode" in operator.log
    assert any(c.action == "list" and c.plural == "imagesyncs" and c.namespace is None for c in cluster.calls)


async def test_multiple_watched_namespaces_are_all_served(
    cluster: FakeCluster, api: ApiClient, start_operator: StartOperator
) -> None:
    operator = await start_operator(default_config(watched_namespaces=[NAMESPACE, "team-a", "team-b"]))
    for namespace in (NAMESPACE, "team-a", "team-b"):
        await api.create("imagesyncs", namespace, imagesync("sync", namespace=namespace))
    for namespace in (NAMESPACE, "team-a", "team-b"):
        await wait_for(
            lambda ns=namespace: job_of(cluster, "sync", ns), timeout=10, desc=f"job in {namespace}", operator=operator
        )


async def _hold_lease(api: ApiClient, holder: str, renewed: str, duration: int = 3600) -> None:
    lease: dict[str, Any] = {
        "apiVersion": "coordination.k8s.io/v1",
        "kind": "Lease",
        "metadata": {"name": "image-sync-operator", "namespace": NAMESPACE},
        "spec": {
            "holderIdentity": holder,
            "leaseDurationSeconds": duration,
            "acquireTime": renewed,
            "renewTime": renewed,
            "leaseTransitions": 0,
        },
    }
    code, body = await api.create("leases", NAMESPACE, lease)
    assert code == 200, body


async def test_operator_acquires_the_lease(cluster: FakeCluster, operator: OperatorProcess) -> None:
    await wait_for(lambda: "Operator has become the leader" in operator.log, desc="leadership", operator=operator)
    lease = cluster.stored("leases", NAMESPACE, "image-sync-operator")
    assert lease is not None
    assert lease["spec"]["holderIdentity"]


async def test_operator_does_not_work_while_another_leader_holds_the_lease(
    cluster: FakeCluster, api: ApiClient, start_operator: StartOperator
) -> None:
    await _hold_lease(api, "some-other-operator", timestamp())
    operator = await start_operator()
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await stays_true(lambda: job_of(cluster) is None and "become the leader" not in operator.log, duration=6, desc="standby")
    assert operator.running
    lease = cluster.stored("leases", NAMESPACE, "image-sync-operator")
    assert lease is not None
    assert lease["spec"]["holderIdentity"] == "some-other-operator"


@pytest.mark.slow
async def test_standby_operator_gives_up_after_lock_timeout(
    cluster: FakeCluster, api: ApiClient, start_operator: StartOperator
) -> None:
    await _hold_lease(api, "some-other-operator", timestamp())
    operator = await start_operator()
    await _exits(operator, timeout=90)
    assert "Unable to get lock during 60s" in operator.log
    assert "Operator is shutting down" in operator.log


async def test_operator_takes_over_an_expired_lease(cluster: FakeCluster, api: ApiClient, start_operator: StartOperator) -> None:
    import datetime

    long_ago = timestamp(-datetime.timedelta(hours=2))
    await _hold_lease(api, "dead-operator", long_ago, duration=30)
    operator = await start_operator()
    await api.create("imagesyncs", NAMESPACE, imagesync())
    await wait_for(lambda: job_of(cluster), desc="job after takeover", operator=operator)
    lease = cluster.stored("leases", NAMESPACE, "image-sync-operator")
    assert lease is not None
    assert lease["spec"]["holderIdentity"] != "dead-operator"
