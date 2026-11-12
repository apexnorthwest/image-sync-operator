from __future__ import annotations

import os
import pathlib
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio
from kmock import Server

from categories import CATEGORIES
from fakek8s import ApiClient, FakeCluster
from harness import DEFAULT_BINARY, NAMESPACE, OperatorProcess, default_config

StartOperator = Callable[..., Awaitable[OperatorProcess]]


def pytest_configure(config: pytest.Config) -> None:
    for name, text in CATEGORIES.items():
        config.addinivalue_line("markers", f"{name}: {text}")
    config.addinivalue_line("markers", "slow: test waits on the operator's long fixed timers")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        # Every test module is a category named by its file: test_<category>.py
        category = pathlib.Path(str(item.fspath)).stem.removeprefix("test_")
        if category in CATEGORIES:
            item.add_marker(getattr(pytest.mark, category))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    report = yield
    item.stash.setdefault(_REPORTS, {})[report.when] = report
    return report


_REPORTS: pytest.StashKey[dict[str, pytest.TestReport]] = pytest.StashKey()


@pytest.fixture(scope="session")
def operator_binary() -> pathlib.Path:
    path = pathlib.Path(os.environ.get("IMAGE_SYNC_OPERATOR_BIN", DEFAULT_BINARY))
    if not path.exists():
        pytest.exit(f"operator binary not found at {path}; run `cargo build` or use run_tests.py", returncode=2)
    return path


@pytest_asyncio.fixture
async def cluster() -> AsyncIterator[FakeCluster]:
    async with FakeCluster() as fake, Server(fake):
        yield fake


@pytest_asyncio.fixture
async def api(cluster: FakeCluster) -> AsyncIterator[ApiClient]:
    client = ApiClient(str(cluster.url))
    yield client
    await client.close()


@pytest_asyncio.fixture
async def start_operator(
    request: pytest.FixtureRequest, cluster: FakeCluster, operator_binary: pathlib.Path
) -> AsyncIterator[StartOperator]:
    started: list[OperatorProcess] = []

    async def _start(
        config: dict[str, Any] | str | None = None,
        *,
        namespace: str = NAMESPACE,
        env: dict[str, str] | None = None,
    ) -> OperatorProcess:
        proc = OperatorProcess(
            operator_binary,
            str(cluster.url),
            default_config() if config is None else config,
            namespace,
            env or {},
        )
        started.append(proc)
        await proc.start()
        return proc

    yield _start

    reports = request.node.stash.get(_REPORTS, {})
    failed = any(r.failed for r in reports.values())
    for proc in started:
        if failed:
            print(f"\n--- operator log ---\n{proc.log}")
        await proc.stop()


@pytest_asyncio.fixture
async def operator(start_operator: StartOperator) -> OperatorProcess:
    return await start_operator()
