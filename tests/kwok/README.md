# KWOK load harness

This standalone Python project starts an isolated
`registry.k8s.io/kwok/cluster:v0.8.0-k8s.v1.36.1` Docker container, installs the repository's
ImageSync CRD into its real Kubernetes API server, and runs the operator as a local process against
that API. It does not use or alter the current kubeconfig or any existing cluster. The container
uses host networking and the unauthenticated API listens on port 8080; ensure that port is free
and access to it is restricted on the host. A remote Docker daemon cannot provide the expected
local endpoint.

Requirements: `uv`, Docker, network access to the KWOK image, and Rust/Cargo when building the
operator. No actual Kubernetes nodes or compute backend are required.

```sh
cd tests/kwok
uv sync --locked
uv run --locked python run_load.py --max-crs 1000 --namespaces 100 --duration 120 --hold-ms 1 --output report.json
uv run --locked python run_load.py --scope namespace --namespaces 100
uv run --locked python run_load.py --operator-bin ../../target/release/image-sync-operator --max-crs 50 --namespaces 5
uv run --locked python run_load.py --help
```

By default, the harness builds the release binary, uses 100 ImageSyncs spread round-robin across
10 namespaces, and runs a 60-second churn phase. It first fills the requested maximum, then
asynchronously patches each object's spec, waits `--hold-ms` milliseconds (default: 2000), deletes it, waits for actual
deletion, and recreates it if time remains. `--concurrency` limits simultaneous operations;
`--max-crs` is an upper bound on live ImageSyncs, including objects awaiting deletion.
Population time is reported separately. An already-started batch may finish after the requested
duration. If there are more namespaces than ImageSyncs, some namespaces remain empty.
Use `--hold-ms 0` for no intentional delay; fractional milliseconds are also accepted, though
actual timing depends on the event loop and API latency. The legacy `--hold-seconds` option
remains available as an alternative. Reports include `hold_ms` and `hold_seconds`.

The default `--scope cluster` runs one cluster-wide watcher. Use `--scope namespace` to run a
separate watcher for each workload namespace. Namespace mode also watches the operator's own
namespace, because the operator currently falls back to watching only its own namespace unless
that namespace appears in its configured list. Thus `--namespaces 100` creates 100 workload
namespaces but 101 namespace watchers. The harness waits for all selected controllers to start
and records the scope and watcher count in the report.

The script waits for operator leadership, reports API operation counts, observed maximum live CRs,
remaining accepted/ready CRs and Jobs, average and peak operator CPU, peak RSS, and phase timings. The optional
JSON report also contains all timestamped process samples. CPU is percentage of one logical CPU,
so multicore use can exceed 100%; RSS is resident memory in bytes. Average CPU is weighted by
each sample's duration over the monitoring period, including the final partial interval. Samples
cover operator startup and load, and can miss short peaks. A final interval shorter than half the
sampling interval is included in the average but excluded from the peak when earlier samples exist.
No CPU or memory failure thresholds are imposed.
`scrape_operator_metrics()` is an explicit stub: the operator currently has no metrics endpoint,
and each sample records `operator_metrics: null` rather than fabricated values.

KWOK runs control-plane controllers but does not execute the generated Skopeo Pods, copy images,
or guarantee successful Jobs. Remaining status counts are snapshots, not completion or throughput
guarantees. Rapid create/modify/delete churn can outpace reconciliation; increase hold time or
duration to explore that behavior. The process samples measure the operator only, not KWOK,
Docker, or the Python client. Results depend on host capacity and API throughput.

On normal exit, errors, or Ctrl-C, the harness stops its operator process and deletes its uniquely
named Docker container. The temporary kubeconfig and operator logs are removed; the tail of the
operator log is printed on failures. Abrupt termination such as SIGKILL cannot run cleanup.

Checks: `uvx ruff format --check . && uvx ruff check . && uvx ty check`.
