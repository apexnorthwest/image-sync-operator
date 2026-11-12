#!/usr/bin/env python3
"""Run an isolated KWOK load test against the real ImageSync operator."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import pathlib
import signal
import tempfile
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
import psutil
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
IMAGE = "registry.k8s.io/kwok/cluster:v0.8.0-k8s.v1.36.1"
API = "/apis/imagesync.apexnw.dev/v1alpha1"
OPERATOR_NAMESPACE = "operator"


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def positive_float(value: str) -> float:
    number = float(value)
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def nonnegative_float(value: str) -> float:
    number = float(value)
    if not 0 <= number < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite nonnegative number")
    return number


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-crs", type=positive_int, default=100, help="maximum concurrently existing ImageSyncs (default: 100)"
    )
    parser.add_argument("--namespaces", type=positive_int, default=10, help="number of workload namespaces (default: 10)")
    parser.add_argument(
        "--scope",
        choices=("cluster", "namespace"),
        default="cluster",
        help="operator watch scope: cluster-wide or one watcher per namespace (default: cluster)",
    )
    parser.add_argument("--duration", type=positive_float, default=60, help="load duration in seconds after initial population")
    parser.add_argument("--concurrency", type=positive_int, default=16, help="maximum simultaneous API operations (default: 16)")
    hold = parser.add_mutually_exclusive_group()
    hold.add_argument(
        "--hold-ms",
        type=nonnegative_float,
        default=2000,
        metavar="MILLISECONDS",
        help="minimum milliseconds between modifications and deletes; 0 disables the delay (default: 2000)",
    )
    hold.add_argument(
        "--hold-seconds",
        dest="hold_ms",
        type=lambda value: positive_float(value) * 1000,
        metavar="SECONDS",
        help="legacy alternative to --hold-ms, in seconds",
    )
    parser.add_argument("--sample-interval", type=positive_float, default=1, help="operator resource sample interval in seconds")
    parser.add_argument("--operator-bin", type=pathlib.Path, help="existing operator binary; otherwise build the release binary")
    parser.add_argument("--output", type=pathlib.Path, help="write the JSON report and all resource samples to this path")
    return parser.parse_args()


async def command(*args: str, timeout: float = 300) -> str:
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(f"Command failed ({process.returncode}): {' '.join(args)}\n{stderr.decode(errors='replace')}")
    return stdout.decode().strip()


class APIClient:
    def __init__(self, base: str, session: aiohttp.ClientSession) -> None:
        self.base = base
        self.session = session

    async def request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None, expected: tuple[int, ...] = (200, 201)
    ) -> Any:
        headers = {"Content-Type": "application/merge-patch+json"} if method == "PATCH" else {}
        async with self.session.request(method, self.base + path, json=body, headers=headers) as response:
            data = await response.text()
            if response.status not in expected:
                raise RuntimeError(f"{method} {path}: HTTP {response.status}: {data}")
            return json.loads(data) if data else None


async def ready(client: APIClient, container: str) -> None:
    deadline = time.monotonic() + 180
    while True:
        try:
            async with client.session.get(client.base + "/readyz") as response:
                if response.status != 200:
                    raise RuntimeError(f"readiness returned HTTP {response.status}: {await response.text()}")
                return
        except (aiohttp.ClientConnectionError, TimeoutError, RuntimeError) as exc:
            if time.monotonic() >= deadline:
                logs = await command("docker", "logs", "--tail", "100", container)
                raise TimeoutError(f"KWOK API did not become ready: {exc}\n{logs}") from exc
            await asyncio.sleep(1)


async def wait_crd(client: APIClient) -> None:
    deadline = time.monotonic() + 60
    path = "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/imagesyncs.imagesync.apexnw.dev"
    while True:
        crd = await client.request("GET", path)
        if any(c["type"] == "Established" and c["status"] == "True" for c in crd.get("status", {}).get("conditions") or []):
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("ImageSync CRD was not established")
        await asyncio.sleep(0.5)


def namespace_name(index: int) -> str:
    return f"load-{index:06d}"


def operator_config(scope: str, namespaces: int) -> dict[str, Any]:
    return {
        "cluster_scope": scope == "cluster",
        "watched_namespaces": (
            [OPERATOR_NAMESPACE, *(namespace_name(index) for index in range(namespaces))] if scope == "namespace" else []
        ),
        "skopeo": {"image": "quay.io/skopeo/stable:latest", "image_pull_policy": "IfNotPresent"},
    }


def cr_path(namespace: str, name: str = "") -> str:
    return f"{API}/namespaces/{namespace}/imagesyncs" + (f"/{name}" if name else "")


def cr_manifest(namespace: str, name: str) -> dict[str, Any]:
    return {
        "apiVersion": "imagesync.apexnw.dev/v1alpha1",
        "kind": "ImageSync",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "source": {"image": "registry.example.com/library/alpine:3.20"},
            "destination": {"image": "mirror.example.com/library/alpine:3.20"},
        },
    }


async def scrape_operator_metrics() -> dict[str, float] | None:
    """Reserved for the operator's future metrics endpoint; no endpoint exists yet."""
    return None


async def monitor(
    process: asyncio.subprocess.Process, interval: float, stop: asyncio.Event, samples: list[dict[str, Any]]
) -> None:
    proc = psutil.Process(process.pid)
    previous_time = time.monotonic()
    cpu = proc.cpu_times()
    previous_cpu = cpu.user + cpu.system
    while True:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), interval)
        if process.returncode is not None:
            raise RuntimeError(f"operator exited during monitoring with code {process.returncode}")
        now = time.monotonic()
        cpu = proc.cpu_times()
        total_cpu = cpu.user + cpu.system
        samples.append(
            {
                "timestamp_unix": time.time(),
                "interval_seconds": now - previous_time,
                "cpu_percent": 100 * (total_cpu - previous_cpu) / (now - previous_time),
                "peak_eligible": not (stop.is_set() and samples and now - previous_time < interval / 2),
                "rss_bytes": proc.memory_info().rss,
                "operator_metrics": await scrape_operator_metrics(),
            }
        )
        previous_time, previous_cpu = now, total_cpu
        if stop.is_set():
            return


def summarize_cpu(samples: list[dict[str, Any]]) -> dict[str, float]:
    if not samples:
        raise ValueError("no operator CPU samples were collected")
    total_interval = sum(sample["interval_seconds"] for sample in samples)
    if total_interval <= 0:
        raise ValueError("operator CPU sample duration must be positive")
    return {
        "average_cpu_percent": sum(sample["cpu_percent"] * sample["interval_seconds"] for sample in samples) / total_interval,
        "peak_cpu_percent": max(sample["cpu_percent"] for sample in samples if sample["peak_eligible"]),
    }


async def parallel_map(items: list[int], concurrency: int, operation: Callable[[int], Awaitable[None]]) -> None:
    queue: asyncio.Queue[int] = asyncio.Queue()
    for item in items:
        queue.put_nowait(item)

    async def worker() -> None:
        while not queue.empty():
            item = queue.get_nowait()
            await operation(item)

    async with asyncio.TaskGroup() as group:
        for _ in range(min(concurrency, len(items))):
            group.create_task(worker())


async def run_load(client: APIClient, args: argparse.Namespace) -> dict[str, Any]:
    counts = {"created": 0, "modified": 0, "deleted": 0}
    live = 0
    peak_live = 0

    def location(slot: int) -> tuple[str, str]:
        return namespace_name(slot % args.namespaces), f"sync-{slot:08d}"

    async def create(slot: int) -> None:
        nonlocal live, peak_live
        namespace, name = location(slot)
        await client.request("POST", cr_path(namespace), body=cr_manifest(namespace, name), expected=(201,))
        live += 1
        peak_live = max(peak_live, live)
        counts["created"] += 1

    started = time.monotonic()
    await parallel_map(list(range(args.max_crs)), args.concurrency, create)
    populated = time.monotonic()
    deadline = populated + args.duration

    async def cycle(slot: int) -> None:
        nonlocal live
        if time.monotonic() >= deadline:
            return
        namespace, name = location(slot)
        await client.request("PATCH", cr_path(namespace, name), body={"spec": {"allArchitectures": True}}, expected=(200,))
        counts["modified"] += 1
        await asyncio.sleep(args.hold_ms / 1000)
        await client.request("DELETE", cr_path(namespace, name), expected=(200, 202))
        counts["deleted"] += 1
        delete_deadline = time.monotonic() + 60
        while True:
            obj = await client.request("GET", cr_path(namespace, name), expected=(200, 404))
            if obj.get("code") == 404:
                break
            if time.monotonic() >= delete_deadline:
                raise TimeoutError(f"ImageSync {namespace}/{name} was not deleted within 60 seconds")
            await asyncio.sleep(0.1)
        live -= 1
        if time.monotonic() < deadline:
            await create(slot)

    while time.monotonic() < deadline:
        await parallel_map(list(range(args.max_crs)), args.concurrency, cycle)

    return {
        "operations": counts,
        "peak_live_crs": peak_live,
        "live_crs_at_end": live,
        "population_seconds": populated - started,
        "churn_seconds": time.monotonic() - populated,
    }


async def main(args: argparse.Namespace) -> None:
    binary = args.operator_bin
    if binary is None:
        print("Building release operator...", flush=True)
        await command("cargo", "build", "--release", "--manifest-path", str(ROOT / "Cargo.toml"), timeout=3600)
        binary = ROOT / "target/release/image-sync-operator"
    binary = binary.resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError(f"operator binary is not executable: {binary}")

    container = f"imagesync-kwok-{uuid.uuid4().hex[:12]}"
    created = False
    operator: asyncio.subprocess.Process | None = None
    with tempfile.TemporaryDirectory(prefix=container + "-") as directory:
        workdir = pathlib.Path(directory)
        log_path = workdir / "operator.log"
        try:
            await command("docker", "create", "--name", container, "--network", "host", IMAGE)
            created = True
            await command("docker", "start", container)
            base = "http://127.0.0.1:8080"
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                client = APIClient(base, session)
                print(f"Starting isolated KWOK API at {base}...", flush=True)
                await ready(client, container)
                crd = yaml.safe_load((ROOT / "crds/imagesyncs.yaml").read_text())
                await client.request("POST", "/apis/apiextensions.k8s.io/v1/customresourcedefinitions", body=crd, expected=(201,))
                await wait_crd(client)
                await client.request(
                    "POST",
                    "/api/v1/namespaces",
                    body={"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": OPERATOR_NAMESPACE}},
                    expected=(201,),
                )

                async def make_namespace(index: int) -> None:
                    await client.request(
                        "POST",
                        "/api/v1/namespaces",
                        body={"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace_name(index)}},
                        expected=(201,),
                    )

                await parallel_map(list(range(args.namespaces)), args.concurrency, make_namespace)
                kubeconfig = {
                    "apiVersion": "v1",
                    "kind": "Config",
                    "clusters": [{"name": "kwok", "cluster": {"server": base}}],
                    "contexts": [{"name": "kwok", "context": {"cluster": "kwok", "namespace": OPERATOR_NAMESPACE}}],
                    "current-context": "kwok",
                }
                (workdir / "kubeconfig").write_text(json.dumps(kubeconfig))
                config = operator_config(args.scope, args.namespaces)
                (workdir / "config.yaml").write_text(json.dumps(config))
                env = {
                    **os.environ,
                    "KUBECONFIG": str(workdir / "kubeconfig"),
                    "IMAGE_SYNC_OPERATOR_CONFIG": str(workdir / "config.yaml"),
                }
                with log_path.open("wb") as log:
                    operator = await asyncio.create_subprocess_exec(
                        str(binary), env=env, stdout=log, stderr=asyncio.subprocess.STDOUT
                    )
                stop = asyncio.Event()
                samples: list[dict[str, Any]] = []
                monitor_task = asyncio.create_task(monitor(operator, args.sample_interval, stop, samples))
                try:
                    await wait_for_controllers(operator, log_path, args.scope, args.namespaces)
                    print(
                        f"Running load: scope={args.scope}, max_crs={args.max_crs}, namespaces={args.namespaces}...", flush=True
                    )
                    async with asyncio.TaskGroup() as group:
                        load_task = group.create_task(run_load(client, args))
                        group.create_task(watch_operator(operator, load_task))
                    result = load_task.result()
                    syncs = await client.request("GET", f"{API}/imagesyncs")
                    jobs = await client.request("GET", "/apis/batch/v1/jobs")
                    result["remaining_status"] = {
                        "accepted": sum(item.get("status", {}).get("accepted") is True for item in syncs["items"]),
                        "ready": sum(item.get("status", {}).get("ready") is True for item in syncs["items"]),
                        "jobs": len(jobs["items"]),
                    }
                finally:
                    stop.set()
                    await monitor_task
                report = {
                    "settings": {
                        "max_crs": args.max_crs,
                        "namespaces": args.namespaces,
                        "scope": args.scope,
                        "watchers": args.namespaces + 1 if args.scope == "namespace" else 1,
                        "duration_seconds": args.duration,
                        "concurrency": args.concurrency,
                        "hold_ms": args.hold_ms,
                        "hold_seconds": args.hold_ms / 1000,
                        "sample_interval": args.sample_interval,
                        "operator_binary": str(binary),
                        "kwok_image": IMAGE,
                    },
                    **result,
                    **summarize_cpu(samples),
                    "peak_rss_bytes": max(sample["rss_bytes"] for sample in samples),
                    "samples": samples,
                }
                if args.output:
                    args.output.write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps({key: value for key, value in report.items() if key != "samples"}, indent=2), flush=True)
        except BaseException:
            if log_path.exists():
                with log_path.open(errors="replace") as log:
                    print("--- operator log (last 100 lines) ---\n" + "".join(deque(log, maxlen=100)))
            raise
        finally:
            if operator and operator.returncode is None:
                operator.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(operator.wait(), 10)
                except TimeoutError:
                    operator.kill()
                    await operator.wait()
            if created:
                await command("docker", "rm", "-f", container)


async def watch_operator(process: asyncio.subprocess.Process, load: asyncio.Task[dict[str, Any]]) -> None:
    while not load.done():
        if process.returncode is not None:
            raise RuntimeError(f"operator exited with code {process.returncode}")
        await asyncio.sleep(0.2)


async def wait_for_controllers(process: asyncio.subprocess.Process, log_path: pathlib.Path, scope: str, namespaces: int) -> None:
    deadline = time.monotonic() + 90
    expected = (
        {"Starting controller for cluster-scoped ImageSyncs"}
        if scope == "cluster"
        else {
            f"Starting controller for namespace: {namespace}"
            for namespace in [OPERATOR_NAMESPACE, *(namespace_name(index) for index in range(namespaces))]
        }
    )
    while True:
        if process.returncode is not None:
            raise RuntimeError(f"operator exited before starting controllers with code {process.returncode}")
        lines = set(log_path.read_text(errors="replace").splitlines())
        if "Operator has become the leader" in lines and expected <= lines:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"operator did not start controllers within 90 seconds; missing: {sorted(expected - lines)}")
        await asyncio.sleep(0.2)


if __name__ == "__main__":
    asyncio.run(main(arguments()))
