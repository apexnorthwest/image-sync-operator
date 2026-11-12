"""Real registry authentication for Skopeo source and destination credentials."""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass

import bcrypt
import pytest

from cluster import REGISTRY, KindCluster, imagesync, manifest, wait_for

AUTH_REGISTRY = "registry-auth.kind-registry.svc.cluster.local:5000"


@dataclass(frozen=True)
class AuthRegistry:
    config: str
    source: str


@pytest.fixture(scope="session")
def auth_registry(cluster: KindCluster) -> AuthRegistry:
    password = secrets.token_urlsafe(24)
    auth = base64.b64encode(f"test-user:{password}".encode()).decode()
    config = json.dumps({"auths": {AUTH_REGISTRY: {"auth": auth}}})
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    cluster.create(
        manifest("Secret", "registry-htpasswd", stringData={"htpasswd": f"test-user:{hashed}\n"}),
        "kind-registry",
    )
    cluster.create(
        manifest("Secret", "registry-auth", type="kubernetes.io/dockerconfigjson", stringData={".dockerconfigjson": config}),
        "kind-registry",
    )
    container = {
        "name": "registry",
        "image": "registry:3",
        "ports": [{"containerPort": 5000}],
        "env": [
            {"name": "REGISTRY_AUTH", "value": "htpasswd"},
            {"name": "REGISTRY_AUTH_HTPASSWD_REALM", "value": "Kind test registry"},
            {"name": "REGISTRY_AUTH_HTPASSWD_PATH", "value": "/auth/htpasswd"},
        ],
        "volumeMounts": [{"name": "auth", "mountPath": "/auth", "readOnly": True}],
    }
    cluster.create(
        manifest(
            "Deployment",
            "registry-auth",
            spec={
                "replicas": 1,
                "selector": {"matchLabels": {"app": "registry-auth"}},
                "template": {
                    "metadata": {"labels": {"app": "registry-auth"}},
                    "spec": {
                        "containers": [container],
                        "enableServiceLinks": False,
                        "volumes": [{"name": "auth", "secret": {"secretName": "registry-htpasswd"}}],
                    },
                },
            },
        ),
        "kind-registry",
    )
    cluster.create(
        manifest("Service", "registry-auth", spec={"selector": {"app": "registry-auth"}, "ports": [{"port": 5000}]}),
        "kind-registry",
    )
    cluster.kubectl("rollout", "status", "deployment/registry-auth", "-n", "kind-registry", "--timeout=300s")
    source = f"{AUTH_REGISTRY}/test/source:1"
    cluster.run_skopeo(
        [
            "copy",
            "--dest-tls-verify=false",
            "--dest-authfile",
            "/auth/.dockerconfigjson",
            "docker://docker.io/library/alpine:3.20",
            f"docker://{source}",
        ],
        auth_secret="registry-auth",
    )
    return AuthRegistry(config, source)


@pytest.mark.parametrize("target", ["source", "destination"])
def test_authenticated_registry_copy(
    cluster: KindCluster, auth_registry: AuthRegistry, namespace: str, destination: str, target: str
) -> None:
    cluster.create(
        manifest(
            "Secret", "credentials", type="kubernetes.io/dockerconfigjson", stringData={".dockerconfigjson": auth_registry.config}
        ),
        namespace,
    )
    source = auth_registry.source if target == "source" else f"{REGISTRY}/test/source:1"
    if target == "destination":
        destination = f"{AUTH_REGISTRY}/test/{namespace}:1"
    obj = imagesync("sync", destination, source=source)
    obj["spec"][target]["registryLoginSecret"] = "credentials"
    cluster.create(obj, namespace)
    assert cluster.ready("sync", namespace)["status"]["accepted"] is True
    job = cluster.get_wait("job", "imagesync-sync", namespace)
    assert ("--src-authfile" if target == "source" else "--dest-authfile") in job["spec"]["template"]["spec"]["containers"][0][
        "command"
    ][2]
    if target == "destination":
        cluster.run_skopeo(
            ["inspect", "--tls-verify=false", "--authfile", "/auth/.dockerconfigjson", f"docker://{destination}"],
            auth_secret="registry-auth",
        )
    else:
        cluster.verify_image(destination)


def test_wrong_registry_credentials_cause_real_copy_failure(
    cluster: KindCluster, auth_registry: AuthRegistry, namespace: str, destination: str
) -> None:
    wrong_auth = base64.b64encode(b"test-user:not-the-password").decode()
    config = json.dumps({"auths": {AUTH_REGISTRY: {"auth": wrong_auth}}})
    cluster.create(
        manifest("Secret", "bad-credentials", type="kubernetes.io/dockerconfigjson", stringData={".dockerconfigjson": config}),
        namespace,
    )
    obj = imagesync("sync", destination, source=auth_registry.source)
    obj["spec"]["source"]["registryLoginSecret"] = "bad-credentials"
    cluster.create(obj, namespace)
    wait_for(
        lambda: any(
            pod.get("status", {}).get("phase") == "Failed"
            for pod in cluster.list_resources("pods", namespace, "job-name=imagesync-sync")
        ),
        "copy attempt to fail authentication",
        timeout=180,
    )
    status = cluster.get_wait("imagesync", "sync", namespace)["status"]
    assert status["accepted"] is True
    assert status["ready"] is False
