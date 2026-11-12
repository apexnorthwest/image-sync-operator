# Kind integration tests

This standalone Python project builds the repository's **release Dockerfile**, creates a uniquely
named Kind cluster with a private temporary kubeconfig, loads the resulting operator image, and
installs the CRD and an operator deployment. A disposable in-cluster registry and real Skopeo Jobs
copy an Alpine image; no existing cluster or kubeconfig is modified. Namespace-scoped test resources
and the temporary cluster are deleted after use, including on test failures.

Requirements: `uv`, Docker with a working daemon, `kind`, `kubectl`, network access to the Rust
build dependencies and container registries, and enough disk space for a Kind node and release build.

```sh
cd tests/kind
uv sync --locked
uv run --locked python run_tests.py             # entire suite, including 10-minute retry
uv run --locked python run_tests.py -m 'not retry'
uv run --locked python run_tests.py test_cronjob.py
uv run --locked python run_tests.py --collect-only
```

Tests use unique namespaces and real Job/CronJob controllers. The `retry` test waits for Kubernetes
backoff and then at least ten minutes after the actual Job failure; no timestamps or statuses are
fabricated. CronJobs run every minute (the shortest Kubernetes schedule). Container tags are
configurable in `cluster.py` if registry access requires local alternatives.

The KMock suite remains the authoritative coverage for artificial HTTP errors, fabricated Job
statuses, exact client request counts, and other cases that cannot be reproduced safely against
a real API server without fault injection. The Kind suite exercises equivalent observable
behavior where possible; it does not simulate API outages or patch Kubernetes-generated status.
It is not yet a one-for-one replacement for the KMock suite: for example, namespace-scoped
operator configuration and lease failover remain covered only by KMock.
