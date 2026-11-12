"""A small fake Kubernetes API built on KMock's stateful emulator.

On top of what KMock provides, this adds the pieces of API server behaviour the operator relies on:
label selectors, namespace/uid stamping, CRD schema validation and defaulting, the ``status`` subresource
split, and programmable fault injection.
Deleting an ImageSync also simulates background garbage collection of its namespaced
Job and CronJob dependents. Full Kubernetes garbage-collection semantics are not emulated.
"""

from __future__ import annotations

import copy
import datetime
import itertools
import pathlib
import uuid
from collections.abc import Mapping
from typing import Any

import aiohttp
import attrs
import jsonschema
import yaml
from kmock import KubernetesEmulator, KubernetesError, resource
from kmock._internal import enums, rendering
from kmock._internal.references import ResourceInfo

CRD_PATH = pathlib.Path(__file__).resolve().parents[2] / "crds" / "imagesyncs.yaml"

GROUP = "imagesync.apexnw.dev"
IMAGESYNCS = resource(GROUP, "v1alpha1", "imagesyncs")
JOBS = resource("batch", "v1", "jobs")
CRONJOBS = resource("batch", "v1", "cronjobs")
SECRETS = resource("", "v1", "secrets")
LEASES = resource("coordination.k8s.io", "v1", "leases")

PATHS = {
    "imagesyncs": f"/apis/{GROUP}/v1alpha1",
    "jobs": "/apis/batch/v1",
    "cronjobs": "/apis/batch/v1",
    "secrets": "/api/v1",
    "leases": "/apis/coordination.k8s.io/v1",
}


def now() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.UTC)


def timestamp(delta: datetime.timedelta = datetime.timedelta()) -> str:
    return (now() + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_crd_schema() -> dict[str, Any]:
    crd = yaml.safe_load(CRD_PATH.read_text())
    return crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]


def apply_defaults(schema: Mapping[str, Any], value: Any) -> None:
    """Apply OpenAPI ``default`` values in place, like the API server does when persisting a CR."""
    if not isinstance(value, dict):
        return
    for key, sub in schema.get("properties", {}).items():
        if key not in value and "default" in sub and sub.get("type") != "object":
            # Defaults only apply when the parent object exists, which is the case here.
            value[key] = sub["default"]
        if key in value:
            apply_defaults(sub, value[key])


def prune_nulls(value: Any) -> None:
    """Drop null values in place; the API server prunes nulls of non-nullable CRD fields before validating."""
    if isinstance(value, dict):
        for key in [k for k, v in value.items() if v is None]:
            del value[key]
        for sub in value.values():
            prune_nulls(sub)
    elif isinstance(value, list):
        for sub in value:
            prune_nulls(sub)


def removals(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    """Build a merge patch that removes everything in ``old`` that a full replace with ``new`` would drop."""
    result: dict[str, Any] = {}
    for key, val in old.items():
        if key not in new:
            result[key] = None
        elif isinstance(val, dict) and isinstance(new[key], dict):
            nested = removals(val, new[key])
            if nested:
                result[key] = nested
    return result


def add_markers(target: dict[str, Any], markers: Mapping[str, Any]) -> None:
    for key, val in markers.items():
        if isinstance(val, dict):
            add_markers(target[key], val)
        else:
            target[key] = None


def deep_merge(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, val in patch.items():
        if val is None:
            result.pop(key, None)
        elif isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def parse_selector(selector: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in filter(None, selector.split(",")):
        key, _, val = part.partition("=")
        result[key.strip()] = val.strip()
    return result


@attrs.frozen
class Call:
    """One request received by the fake API server."""

    action: str
    plural: str
    namespace: str | None
    name: str | None
    subresource: str | None
    data: Any


@attrs.define
class Fault:
    plural: str
    action: str
    status: int
    name: str | None = None
    namespace: str | None = None
    times: int | None = None
    hits: int = 0

    def matches(self, request: rendering.Request) -> bool:
        if request.resource is None or request.resource.plural != self.plural:
            return False
        if request.action is None or request.action.value.lower() != self.action.lower():
            return False
        if self.name is not None and request.name != self.name:
            return False
        if self.namespace is not None and request.namespace != self.namespace:
            return False
        return self.times is None or self.hits < self.times


@attrs.define(kw_only=True)
class FakeCluster(KubernetesEmulator):
    """KMock emulator with the extra API server behaviours needed by the operator."""

    calls: list[Call] = attrs.field(factory=list, init=False)
    faults: list[Fault] = attrs.field(factory=list, init=False)
    schema: dict[str, Any] = attrs.field(factory=load_crd_schema, init=False)
    _rv: Any = attrs.field(factory=lambda: itertools.count(1), init=False)

    def __attrs_post_init__(self) -> None:
        super().__attrs_post_init__()
        self.resources[IMAGESYNCS] = ResourceInfo(
            namespaced=True, kind="ImageSync", singular="imagesync", subresources={"status"}
        )
        self.resources[JOBS] = ResourceInfo(namespaced=True, kind="Job")
        self.resources[CRONJOBS] = ResourceInfo(namespaced=True, kind="CronJob")
        self.resources[SECRETS] = ResourceInfo(namespaced=True, kind="Secret")
        self.resources[LEASES] = ResourceInfo(namespaced=True, kind="Lease")

    # ---- request handling -------------------------------------------------------------------------

    async def _handle(self, request: rendering.Request):  # type: ignore[no-untyped-def]
        if request.method == enums.method.PUT and request.name is not None and request.action is None:
            # KMock does not classify PUT (replace) on named objects; treat it as an update.
            object.__setattr__(request, "action", enums.action.UPDATE)
        plural = request.resource.plural if request.resource is not None else ""
        if request.resource is not None:
            self.calls.append(
                Call(
                    action=request.action.value.lower() if request.action is not None else "",
                    plural=plural or "",
                    namespace=request.namespace,
                    name=request.name,
                    subresource=request.subresource,
                    data=copy.deepcopy(request.data),
                )
            )
        for fault in self.faults:
            if fault.matches(request):
                fault.hits += 1
                raise KubernetesError(
                    status=fault.status,
                    reason="InjectedFault",
                    message=f"injected {fault.status} for {fault.action} {fault.plural}",
                )
        if request.resource is not None and isinstance(request.data, dict):
            self._admit(request)
        response = await super()._handle(request)
        if (
            request.resource == IMAGESYNCS
            and request.name is not None
            and request.action in (enums.action.DELETE, enums.action.UPDATE)
        ):
            await self._collect_imagesync_dependents(request)
        return response

    async def _collect_imagesync_dependents(self, request: rendering.Request) -> None:
        async with self._lock:
            owner = next(
                (
                    obj
                    for (res, ns, name), obj in self._objects.items()
                    if (res, ns, name) == (IMAGESYNCS, request.namespace, request.name)
                ),
                None,
            )
            if owner is None or not owner.deleted:
                return
            owner_uid = owner.last.raw["metadata"]["uid"]
            live_uids = {
                obj.raw.get("metadata", {}).get("uid")
                for (_, namespace, _), obj in self._objects.items()
                if namespace == request.namespace and not obj.deleted
            }
            for (resource_type, namespace, _), obj in self._objects.items():
                if resource_type not in (JOBS, CRONJOBS) or namespace != request.namespace or obj.deleted:
                    continue
                metadata = obj.raw.get("metadata", {})
                references = metadata.get("ownerReferences", [])
                if not any(ref.get("uid") == owner_uid for ref in references):
                    continue
                if any(ref.get("uid") in live_uids for ref in references):
                    continue
                if metadata.get("finalizers"):
                    if not metadata.get("deletionTimestamp"):
                        obj.patch({"metadata": {"deletionTimestamp": now().isoformat()}})
                        await self._buses[resource_type].put({"type": "MODIFIED", "object": obj.raw})
                else:
                    obj.delete()
                    await self._buses[resource_type].put({"type": "DELETED", "object": obj.last.raw})

    def _admit(self, request: rendering.Request) -> None:
        """Mimic admission: stamp metadata, split status from spec, validate and default custom resources."""
        data = request.data
        assert request.resource is not None
        is_imagesync = request.resource == IMAGESYNCS
        if is_imagesync and request.method != enums.method.PATCH:
            prune_nulls(data)
        if request.action == enums.action.CREATE:
            meta = data.setdefault("metadata", {})
            meta["namespace"] = request.namespace or meta.get("namespace")
            meta.setdefault("uid", str(uuid.uuid4()))
            meta.setdefault("creationTimestamp", timestamp())
            meta["resourceVersion"] = str(next(self._rv))
            if is_imagesync:
                data.pop("status", None)
                apply_defaults(self.schema, data)
                self._validate(data)
        elif request.action == enums.action.UPDATE:
            meta = data.setdefault("metadata", {})
            meta["resourceVersion"] = str(next(self._rv))
            if request.method == enums.method.PUT and request.name is not None:
                # KMock merges updates; a PUT replaces, so explicitly drop what the new body omits.
                old = self._current(request)
                add_markers(data, {k: v for k, v in removals(old, data).items() if k != "metadata"})
            if is_imagesync and request.name is not None:
                current = self._current(request)
                if request.subresource == "status":
                    # Only the status is writable through the status subresource.
                    for key in [k for k in data if k not in ("status", "metadata")]:
                        del data[key]
                    candidate = deep_merge(current, {"status": data.get("status", {})})
                else:
                    data.pop("status", None)
                    candidate = deep_merge(current, data)
                apply_defaults(self.schema, candidate)
                self._validate(candidate)

    def _current(self, request: rendering.Request) -> dict[str, Any]:
        for (res, namespace, name), obj in self.objects.items():
            if res == request.resource and namespace == request.namespace and name == request.name:
                if obj.deleted:
                    break
                return copy.deepcopy(dict(obj.raw))
        raise KubernetesError(status=404, reason="NotFound", message="object not found")

    def _validate(self, obj: dict[str, Any]) -> None:
        errors = sorted(
            jsonschema.Draft7Validator(self.schema).iter_errors(obj),
            key=lambda e: list(e.absolute_path),
        )
        if errors:
            text = "; ".join(f"{'.'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors)
            raise KubernetesError(status=422, reason="Invalid", message=f"ImageSync is invalid: {text}")

    async def _serve_list(self, request: rendering.Request):  # type: ignore[no-untyped-def]
        payload = await super()._serve_list(request)
        selector = parse_selector(request.params.get("labelSelector", ""))
        if selector and isinstance(payload, dict):
            payload["items"] = [
                item
                for item in payload["items"]
                if all(item.get("metadata", {}).get("labels", {}).get(k) == v for k, v in selector.items())
            ]
        return payload

    # ---- test-side helpers --------------------------------------------------------------------------

    def inject_fault(
        self,
        plural: str,
        action: str,
        status: int = 500,
        *,
        name: str | None = None,
        namespace: str | None = None,
        times: int | None = None,
    ) -> Fault:
        """Make matching requests fail with the given HTTP status (``times=None`` means forever)."""
        fault = Fault(plural, action, status, name, namespace, times)
        self.faults.append(fault)
        return fault

    def clear_faults(self) -> None:
        self.faults.clear()

    def calls_for(self, plural: str, action: str | None = None, name: str | None = None) -> list[Call]:
        return [
            c
            for c in self.calls
            if c.plural == plural and (action is None or c.action == action) and (name is None or c.name == name)
        ]

    def stored(self, plural: str, namespace: str, name: str) -> dict[str, Any] | None:
        for (res, ns, obj_name), obj in self.objects.items():
            if res.plural == plural and ns == namespace and obj_name == name and not obj.deleted:
                return copy.deepcopy(dict(obj.raw))
        return None

    def stored_all(self, plural: str, namespace: str | None = None) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(dict(obj.raw))
            for (res, ns, _), obj in self.objects.items()
            if res.plural == plural and (namespace is None or ns == namespace) and not obj.deleted
        ]


class ApiClient:
    """Plain HTTP client the tests use to play the role of ``kubectl`` and of the Kubernetes controllers."""

    def __init__(self, url: str) -> None:
        self._base = url.rstrip("/")
        self._session = aiohttp.ClientSession()

    async def close(self) -> None:
        await self._session.close()

    def _path(self, plural: str, namespace: str, name: str | None = None, sub: str | None = None) -> str:
        path = f"{self._base}{PATHS[plural]}/namespaces/{namespace}/{plural}"
        if name:
            path += f"/{name}"
        if sub:
            path += f"/{sub}"
        return path

    async def create(self, plural: str, namespace: str, obj: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        async with self._session.post(self._path(plural, namespace), json=obj) as resp:
            return resp.status, await resp.json()

    async def patch(
        self, plural: str, namespace: str, name: str, patch: dict[str, Any], sub: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        async with self._session.patch(
            self._path(plural, namespace, name, sub),
            json=patch,
            headers={"Content-Type": "application/merge-patch+json"},
        ) as resp:
            return resp.status, await resp.json()

    async def delete(self, plural: str, namespace: str, name: str) -> int:
        async with self._session.delete(self._path(plural, namespace, name)) as resp:
            return resp.status
