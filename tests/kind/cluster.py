"""Private Kind cluster and kubectl helpers used by the real-cluster tests."""

from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKOPEO = "quay.io/skopeo/stable:latest"
REGISTRY_IMAGE = "registry:3"
REGISTRY = "registry.kind-registry.svc.cluster.local:5000"
SOURCE = f"{REGISTRY}/test/source:1"
TLS_ARGS = "--src-tls-verify=false --dest-tls-verify=false"


def run(args: list[str], *, data: str | None = None, timeout: int = 600) -> str:
    result = subprocess.run(args, input=data, text=True, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def wait_for[T](probe: Callable[[], T | None], description: str, *, timeout: float = 240, interval: float = 2) -> T:
    deadline = time.monotonic() + timeout
    while True:
        value = probe()
        if value is not None and value is not False:
            return value
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out after {timeout}s waiting for {description}")
        time.sleep(interval)


def manifest(kind: str, name: str, **fields: Any) -> dict[str, Any]:
    versions = {
        "Namespace": "v1",
        "ServiceAccount": "v1",
        "Secret": "v1",
        "Service": "v1",
        "Pod": "v1",
        "Deployment": "apps/v1",
        "Job": "batch/v1",
        "ClusterRole": "rbac.authorization.k8s.io/v1",
        "ClusterRoleBinding": "rbac.authorization.k8s.io/v1",
        "ImageSync": "imagesync.apexnw.dev/v1alpha1",
    }
    return {"apiVersion": versions[kind], "kind": kind, "metadata": {"name": name}, **fields}


class KindCluster:
    def __init__(self) -> None:
        self.name = f"imagesync-test-{uuid.uuid4().hex[:12]}"
        self.image = f"image-sync-operator-kind:{self.name}"
        self.temp = tempfile.TemporaryDirectory(prefix=f"{self.name}-")
        self.kubeconfig = pathlib.Path(self.temp.name) / "kubeconfig"
        self.created = False
        self.built = False

    def setup(self) -> None:
        run(["docker", "--version"], timeout=30)
        run(["kind", "version"], timeout=30)
        run(["kubectl", "version", "--client"], timeout=30)
        run(["docker", "info"], timeout=30)
        existing = run(["kind", "get", "clusters"], timeout=30).splitlines()
        if self.name in existing:
            raise RuntimeError(f"Refusing to use existing Kind cluster {self.name}")
        print(f"Building release image {self.image}", flush=True)
        run(["docker", "build", "--target", "release", "-t", self.image, str(ROOT)], timeout=3600)
        self.built = True
        self.created = True
        print(f"Creating isolated Kind cluster {self.name}", flush=True)
        run(["kind", "create", "cluster", "--name", self.name, "--kubeconfig", str(self.kubeconfig), "--wait", "5m"], timeout=600)
        run(["kind", "load", "docker-image", self.image, "--name", self.name], timeout=300)
        self.kubectl("apply", "-f", str(ROOT / "crds" / "imagesyncs.yaml"))
        self.kubectl("wait", "--for=condition=Established", "crd/imagesyncs.imagesync.apexnw.dev", "--timeout=120s")
        self._install_operator()
        self._install_registry()

    def close(self) -> None:
        try:
            if self.created:
                run(["kind", "delete", "cluster", "--name", self.name], timeout=300)
        finally:
            try:
                if self.built:
                    run(["docker", "image", "rm", self.image], timeout=120)
            finally:
                self.temp.cleanup()

    def kubectl(self, *args: str, data: str | None = None, timeout: int = 300) -> str:
        return run(["kubectl", "--kubeconfig", str(self.kubeconfig), *args], data=data, timeout=timeout)

    def apply(self, obj: dict[str, Any], namespace: str | None = None) -> None:
        args = ["apply", "-f", "-"]
        if namespace:
            args += ["-n", namespace]
        self.kubectl(*args, data=json.dumps(obj))

    def create(self, obj: dict[str, Any], namespace: str | None = None) -> None:
        args = ["create", "-f", "-"]
        if namespace:
            args += ["-n", namespace]
        self.kubectl(*args, data=json.dumps(obj))

    def get(self, resource: str, name: str, namespace: str) -> dict[str, Any] | None:
        output = self.kubectl("get", resource, name, "-n", namespace, "-o", "json", "--ignore-not-found")
        return json.loads(output) if output.strip() else None

    def list_resources(self, resource: str, namespace: str, selector: str | None = None) -> list[dict[str, Any]]:
        args = ["get", resource, "-n", namespace, "-o", "json"]
        if selector:
            args += ["-l", selector]
        return json.loads(self.kubectl(*args))["items"]

    def patch(self, resource: str, name: str, namespace: str, patch: dict[str, Any]) -> None:
        self.kubectl("patch", resource, name, "-n", namespace, "--type=merge", "-p", json.dumps(patch))

    def delete(self, resource: str, name: str, namespace: str, *, wait: bool = True) -> None:
        self.kubectl("delete", resource, name, "-n", namespace, "--ignore-not-found", f"--wait={str(wait).lower()}")

    def get_wait(self, resource: str, name: str, namespace: str, *, timeout: int = 240) -> dict[str, Any]:
        return wait_for(lambda: self.get(resource, name, namespace), f"{resource}/{name}", timeout=timeout)

    def ready(self, name: str, namespace: str, *, timeout: int = 300) -> dict[str, Any]:
        def probe() -> dict[str, Any] | None:
            obj = self.get("imagesync", name, namespace)
            return obj if obj and obj.get("status", {}).get("ready") is True else None

        return wait_for(probe, f"ImageSync {name} ready", timeout=timeout)

    def _install_operator(self) -> None:
        self.apply(manifest("Namespace", "kind-operator"))
        self.apply(manifest("ServiceAccount", "operator"), "kind-operator")
        rules = [
            {"apiGroups": ["imagesync.apexnw.dev"], "resources": ["imagesyncs"], "verbs": ["get", "list", "watch"]},
            {"apiGroups": ["imagesync.apexnw.dev"], "resources": ["imagesyncs/status"], "verbs": ["get", "update", "patch"]},
            {"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "list"]},
            {"apiGroups": ["batch"], "resources": ["jobs", "cronjobs"], "verbs": ["get", "list", "watch", "create", "delete"]},
            {
                "apiGroups": ["coordination.k8s.io"],
                "resources": ["leases"],
                "verbs": ["get", "create", "update", "patch", "delete"],
            },
        ]
        self.apply(manifest("ClusterRole", "kind-operator", rules=rules))
        self.apply(
            manifest(
                "ClusterRoleBinding",
                "kind-operator",
                roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "kind-operator"},
                subjects=[{"kind": "ServiceAccount", "name": "operator", "namespace": "kind-operator"}],
            )
        )
        config = {
            "cluster_scope": True,
            "watched_namespaces": [],
            "skopeo": {"image": SKOPEO, "image_pull_policy": "IfNotPresent"},
        }
        self.apply(manifest("Secret", "operator-config", stringData={"config.yaml": yaml.safe_dump(config)}), "kind-operator")
        pod_spec = {
            "serviceAccountName": "operator",
            "containers": [
                {
                    "name": "operator",
                    "image": self.image,
                    "imagePullPolicy": "Never",
                    "volumeMounts": [{"name": "config", "mountPath": "/config", "readOnly": True}],
                }
            ],
            "volumes": [{"name": "config", "secret": {"secretName": "operator-config"}}],
        }
        self.apply(
            manifest(
                "Deployment",
                "operator",
                spec={
                    "replicas": 1,
                    "selector": {"matchLabels": {"app": "operator"}},
                    "template": {"metadata": {"labels": {"app": "operator"}}, "spec": pod_spec},
                },
            ),
            "kind-operator",
        )
        self.kubectl("rollout", "status", "deployment/operator", "-n", "kind-operator", "--timeout=180s")
        wait_for(
            lambda: "Operator has become the leader" in self.kubectl("logs", "deployment/operator", "-n", "kind-operator"),
            "operator leadership",
            timeout=180,
        )

    def _install_registry(self) -> None:
        self.apply(manifest("Namespace", "kind-registry"))
        self.apply(
            manifest(
                "Deployment",
                "registry",
                spec={
                    "replicas": 1,
                    "selector": {"matchLabels": {"app": "registry"}},
                    "template": {
                        "metadata": {"labels": {"app": "registry"}},
                        "spec": {
                            "containers": [{"name": "registry", "image": REGISTRY_IMAGE, "ports": [{"containerPort": 5000}]}]
                        },
                    },
                },
            ),
            "kind-registry",
        )
        self.apply(
            manifest("Service", "registry", spec={"selector": {"app": "registry"}, "ports": [{"port": 5000}]}),
            "kind-registry",
        )
        self.kubectl("rollout", "status", "deployment/registry", "-n", "kind-registry", "--timeout=300s")
        self.seed(SOURCE)

    def run_skopeo(self, arguments: list[str], *, auth_secret: str | None = None) -> None:
        name = f"skopeo-{uuid.uuid4().hex[:10]}"
        container: dict[str, Any] = {"name": "skopeo", "image": SKOPEO, "command": ["skopeo", *arguments]}
        pod: dict[str, Any] = {"restartPolicy": "Never", "containers": [container]}
        if auth_secret:
            container["volumeMounts"] = [{"name": "auth", "mountPath": "/auth", "readOnly": True}]
            pod["volumes"] = [{"name": "auth", "secret": {"secretName": auth_secret}}]
        job = manifest(
            "Job",
            name,
            spec={
                "backoffLimit": 0,
                "template": {"spec": pod},
            },
        )
        self.create(job, "kind-registry")
        try:

            def completed() -> bool:
                current = self.get("job", name, "kind-registry")
                if current is None:
                    return False
                conditions = current.get("status", {}).get("conditions", [])
                if any(c["type"] == "Failed" and c["status"] == "True" for c in conditions):
                    raise RuntimeError(f"Skopeo Job {name} failed")
                return any(c["type"] == "Complete" and c["status"] == "True" for c in conditions)

            wait_for(completed, f"Skopeo Job {name} completion", timeout=300)
        except (RuntimeError, subprocess.TimeoutExpired, TimeoutError):
            try:
                print(self.kubectl("logs", f"job/{name}", "-n", "kind-registry"))
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                print(f"Could not retrieve Skopeo Job logs: {exc}")
            raise
        finally:
            self.delete("job", name, "kind-registry")

    def seed(self, destination: str) -> None:
        self.run_skopeo(["copy", "--dest-tls-verify=false", "docker://docker.io/library/alpine:3.20", f"docker://{destination}"])

    def verify_image(self, image: str) -> None:
        self.run_skopeo(["inspect", "--tls-verify=false", f"docker://{image}"])

    def diagnostics(self, namespace: str) -> None:
        for args in (
            ("get", "imagesyncs,jobs,cronjobs,pods", "-n", namespace, "-o", "yaml"),
            ("get", "events", "-n", namespace, "--sort-by=.lastTimestamp"),
            ("logs", "deployment/operator", "-n", "kind-operator", "--tail=200"),
        ):
            try:
                print(f"\n$ kubectl {' '.join(args)}\n{self.kubectl(*args, timeout=30)}")
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                print(f"Diagnostics failed: {exc}")


def imagesync(name: str, destination: str, *, source: str = SOURCE, **spec: Any) -> dict[str, Any]:
    return manifest(
        "ImageSync",
        name,
        spec={
            "source": {"image": source},
            "destination": {"image": destination},
            "extraSkopeoArguments": TLS_ARGS,
            **spec,
        },
    )


def namespace_names() -> Iterator[str]:
    while True:
        yield f"test-{uuid.uuid4().hex[:12]}"
