"""Process management and manifest builders shared by the test modules."""

from __future__ import annotations

import asyncio
import os
import pathlib
import tempfile
from collections.abc import Callable
from typing import Any, cast

import yaml

from fakek8s import FakeCluster, timestamp

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_BINARY = REPO_ROOT / "target" / "debug" / "image-sync-operator"
NAMESPACE = "ns1"

GOOD_SRC = "registry.example.com/library/alpine:3.20"
GOOD_DEST = "mirror.example.com/library/alpine:3.20"
DOCKER_SECRET_TYPE = "kubernetes.io/dockerconfigjson"


def default_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "cluster_scope": False,
        "watched_namespaces": [NAMESPACE],
        "skopeo": {"image": "quay.io/skopeo/stable:test", "image_pull_policy": "IfNotPresent"},
    }
    config.update(overrides)
    return config


class OperatorProcess:
    """One running operator binary, wired to a fake API server through a generated kubeconfig."""

    def __init__(self, binary: pathlib.Path, api_url: str, config: dict[str, Any] | str, namespace: str, env: dict[str, str]):
        self._dir = tempfile.TemporaryDirectory(prefix="imagesync-op-")
        workdir = pathlib.Path(self._dir.name)
        kubeconfig = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [{"name": "fake", "cluster": {"server": api_url}}],
            "users": [{"name": "fake", "user": {"token": "fake"}}],
            "contexts": [{"name": "fake", "context": {"cluster": "fake", "user": "fake", "namespace": namespace}}],
            "current-context": "fake",
        }
        (workdir / "kubeconfig").write_text(yaml.safe_dump(kubeconfig))
        config_text = config if isinstance(config, str) else yaml.safe_dump(config)
        (workdir / "config.yaml").write_text(config_text)
        self.log_path = workdir / "operator.log"
        self._binary = binary
        self._env = {
            **os.environ,
            "KUBECONFIG": str(workdir / "kubeconfig"),
            "IMAGE_SYNC_OPERATOR_CONFIG": str(workdir / "config.yaml"),
            "RUST_BACKTRACE": "0",
            **env,
        }
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        with self.log_path.open("wb") as log:
            self._proc = await asyncio.create_subprocess_exec(
                str(self._binary), stdout=log, stderr=asyncio.subprocess.STDOUT, env=self._env
            )

    @property
    def log(self) -> str:
        return self.log_path.read_text(errors="replace") if self.log_path.exists() else ""

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc is not None else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def wait_exit(self, timeout: float) -> int:
        assert self._proc is not None
        return await asyncio.wait_for(self._proc.wait(), timeout)

    async def stop(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), 5)
            except TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._dir.cleanup()


async def wait_for[T](
    probe: Callable[[], T | None | bool],
    *,
    timeout: float = 30,
    interval: float = 0.2,
    desc: str = "condition",
    operator: OperatorProcess | None = None,
) -> T:
    """Poll ``probe`` until it returns something truthy and return that value."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        result = probe()
        if result:
            return cast(T, result)
        if loop.time() > deadline:
            tail = f"\n--- operator log ---\n{operator.log}" if operator is not None else ""
            raise AssertionError(f"timed out after {timeout}s waiting for {desc}{tail}")
        await asyncio.sleep(interval)


async def stays_true(probe: Callable[[], bool], *, duration: float, interval: float = 0.2, desc: str = "condition") -> None:
    """Assert that ``probe`` keeps returning true for ``duration`` seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration
    while loop.time() < deadline:
        assert probe(), f"{desc} stopped holding"
        await asyncio.sleep(interval)


def imagesync(
    name: str = "alpine",
    namespace: str = NAMESPACE,
    *,
    source: str | None = GOOD_SRC,
    destination: str | None = GOOD_DEST,
    source_secret: str | None = None,
    dest_secret: str | None = None,
    **spec_fields: Any,
) -> dict[str, Any]:
    spec: dict[str, Any] = {}
    if source is not None:
        spec["source"] = {"image": source}
        if source_secret is not None:
            spec["source"]["registryLoginSecret"] = source_secret
    if destination is not None:
        spec["destination"] = {"image": destination}
        if dest_secret is not None:
            spec["destination"]["registryLoginSecret"] = dest_secret
    spec.update(spec_fields)
    return {
        "apiVersion": "imagesync.apexnw.dev/v1alpha1",
        "kind": "ImageSync",
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec,
    }


def secret(name: str, namespace: str = NAMESPACE, type_: str = DOCKER_SECRET_TYPE) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": namespace},
        "type": type_,
        "data": {".dockerconfigjson": "e30="},
    }


def job_name(sync_name: str) -> str:
    return f"imagesync-{sync_name[:50]}"


def status_of(cluster: FakeCluster, name: str = "alpine", namespace: str = NAMESPACE) -> dict[str, Any]:
    obj = cluster.stored("imagesyncs", namespace, name)
    return (obj or {}).get("status") or {}


def job_of(cluster: FakeCluster, sync_name: str = "alpine", namespace: str = NAMESPACE) -> dict[str, Any] | None:
    return cluster.stored("jobs", namespace, job_name(sync_name))


def cronjob_of(cluster: FakeCluster, sync_name: str = "alpine", namespace: str = NAMESPACE) -> dict[str, Any] | None:
    return cluster.stored("cronjobs", namespace, job_name(sync_name))


def completed_job_status(completed_at: str | None = None) -> dict[str, Any]:
    stamp = completed_at or timestamp()
    return {
        "status": {
            "startTime": stamp,
            "completionTime": stamp,
            "succeeded": 1,
            "conditions": [{"type": "Complete", "status": "True", "lastTransitionTime": stamp, "lastProbeTime": stamp}],
        }
    }


def failed_job_status(failed_at: str | None = None, reason: str = "BackoffLimitExceeded") -> dict[str, Any]:
    stamp = failed_at or timestamp()
    return {
        "status": {
            "startTime": stamp,
            "failed": 5,
            "conditions": [
                {
                    "type": "Failed",
                    "status": "True",
                    "reason": reason,
                    "message": "Job has reached the specified backoff limit",
                    "lastTransitionTime": stamp,
                    "lastProbeTime": stamp,
                }
            ],
        }
    }


def running_job_status() -> dict[str, Any]:
    return {"status": {"startTime": timestamp(), "active": 1}}


async def nudge(api: Any, name: str = "alpine", namespace: str = NAMESPACE) -> None:
    """Touch the ImageSync metadata so the operator reconciles it right now instead of on its next timer."""
    await api.patch("imagesyncs", namespace, name, {"metadata": {"annotations": {"test/nudge": timestamp()}}})


async def apply_job_status(api: Any, status: dict[str, Any], sync_name: str = "alpine", namespace: str = NAMESPACE) -> None:
    code, body = await api.patch("jobs", namespace, job_name(sync_name), status)
    assert code == 200, body


async def make_ready(cluster: FakeCluster, api: Any, operator: OperatorProcess, name: str = "alpine", **spec: Any) -> str:
    """Create an ImageSync, complete its one-shot Job and wait until the operator reports it ready."""
    code, body = await api.create("imagesyncs", NAMESPACE, imagesync(name, **spec))
    assert code == 200, body
    await wait_for(lambda: job_of(cluster, name), desc="job", operator=operator)
    finished = timestamp()
    await apply_job_status(api, completed_job_status(finished), name)
    await wait_for(lambda: status_of(cluster, name).get("ready"), desc="ready", operator=operator)
    return finished
