from __future__ import annotations

from collections.abc import Generator, Iterator

import pytest

from cluster import REGISTRY, KindCluster, manifest, namespace_names


@pytest.fixture(scope="session")
def cluster() -> Iterator[KindCluster]:
    instance = KindCluster()
    try:
        instance.setup()
        yield instance
    finally:
        instance.close()


@pytest.fixture
def namespace(cluster: KindCluster, request: pytest.FixtureRequest) -> Iterator[str]:
    name = next(namespace_names())
    cluster.create(manifest("Namespace", name))
    try:
        yield name
    finally:
        try:
            if request.node.stash.get(REPORTS, {}).get("call", False):
                cluster.diagnostics(name)
        finally:
            cluster.kubectl("delete", "namespace", name, "--wait=false", "--ignore-not-found")


@pytest.fixture
def destination(namespace: str) -> str:
    return f"{REGISTRY}/test/{namespace}:1"


REPORTS: pytest.StashKey[dict[str, bool]] = pytest.StashKey()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if report.when is not None:
        item.stash.setdefault(REPORTS, {})[report.when] = report.failed
    return report
