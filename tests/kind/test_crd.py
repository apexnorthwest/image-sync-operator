"""Admission and schema defaults, exercised against the real API server."""

from __future__ import annotations

import pytest

from cluster import KindCluster, imagesync


def test_defaults_and_optional_field_removal(cluster: KindCluster, namespace: str, destination: str) -> None:
    obj = imagesync("schema", destination, cronSchedule="* * * * *")
    cluster.create(obj, namespace)
    created = cluster.get_wait("imagesync", "schema", namespace)
    assert created["spec"]["allArchitectures"] is False
    assert created["spec"]["preserveDigests"] is False
    cluster.patch("imagesync", "schema", namespace, {"spec": {"cronSchedule": None}})
    updated = cluster.get_wait("imagesync", "schema", namespace)
    assert "cronSchedule" not in updated["spec"]


@pytest.mark.parametrize(
    "spec",
    [
        {},
        {"source": {"image": "registry.example.com/test/image:1"}},
        {"source": {"image": "x"}, "destination": {"image": "y"}, "allArchitectures": "yes"},
        {"source": {"image": "x", "registryLoginSecret": "Upper_Case"}, "destination": {"image": "y"}},
    ],
)
def test_invalid_crd_spec_is_rejected(cluster: KindCluster, namespace: str, spec: dict[str, object]) -> None:
    obj = imagesync("bad-schema", "registry.example.com/test/image:1")
    obj["spec"] = spec
    with pytest.raises(RuntimeError, match="(?i)(invalid|required)"):
        cluster.create(obj, namespace)
    assert cluster.get("imagesync", "bad-schema", namespace) is None
