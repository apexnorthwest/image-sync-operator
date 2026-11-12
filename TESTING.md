# Testing Guide

This document describes the test suites for the image-sync-operator, their requirements, and how to run them.

## Overview

We maintain three test suites, each serving a different purpose:

| Suite | Purpose | Runtime | When to Run |
|-------|---------|---------|-------------|
| **KMock** | Rapid regression analysis | ~2-10 minutes | All PRs, local development |
| **Kind** | Integration with real Kubernetes | ~20-30 minutes | Before releases |
| **KWOK** | Performance regression testing | ~2-5 minutes | Before releases |

---

## KMock Test Suite

**Location:** `tests/kmock/`

A fake-cluster test suite that runs the real operator binary against a KMock-based fake Kubernetes API. No actual cluster is needed.

### Key Features

- Jobs and CronJobs are driven by tests patching their status
- Simulates background collection of same-namespace Jobs/CronJobs when ImageSync owner is deleted
- Does NOT emulate all Kubernetes garbage-collection policies
- Fast execution (suitable for CI and local development)

### System Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- Cargo and Rustc (Probably just use rustup to install the latest stable version)

### Running Tests

```sh
cd tests/kmock

# Install dependencies
uv sync --locked

# Run everything except slow tests
./run_tests.py

# Include tests that wait on the operator's 30s/60s timers
./run_tests.py --slow

# Run specific categories (job, cronjob, retry, etc.)
./run_tests.py -c job -c retry

# Run a specific test
./run_tests.py test_job.py::test_job_object_is_rendered_from_spec

# Run tests by name substring (pytest -k)
./run_tests.py secret

# Reuse built binary, run serially, stop at first failure
./run_tests.py --no-build -j 1 -x

# List available categories
./run_tests.py --list
```

### Configuration

- `IMAGE_SYNC_OPERATOR_BIN` - Path to operator binary (defaults to built binary)
- `IMAGE_SYNC_OPERATOR_CONFIG` - Path to config file (defaults to `/config/config.yaml`)

The test script generally does the right thing when these are left unset.

### Cleanup
Delete the target and tests/kmock/.venv folders

---

## Kind Test Suite

**Location:** `tests/kind/`

Real Kubernetes integration tests using Kind (Kubernetes in Docker). This suite tests against actual Kubernetes API servers and controllers.

### Key Features

- Builds the repository's **release Dockerfile**
- Creates a uniquely named Kind cluster with private temporary kubeconfig
- Loads the operator image into the cluster
- Installs the CRD and operator deployment
- Uses a disposable in-cluster registry and real Skopeo Jobs
- Does NOT modify existing cluster or kubeconfig
- Tests are fully isolated and cleaned up after use (including on failures)

### System Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- Docker with non-sudo access
- `kind` (Kubernetes in Docker)
- `kubectl`

### Running Tests

```sh
cd tests/kind

# Install dependencies
uv sync --locked

# Run entire suite (including ~10 minute retry test)
uv run --locked python run_tests.py

# Exclude slow tests (e.g., the retry test. This is somewhat faster)
uv run --locked python run_tests.py -m 'not retry'

# Run a specific test file
uv run --locked python run_tests.py test_cronjob.py

# List available tests
uv run --locked python run_tests.py --collect-only
```

### Test Characteristics

- Tests use unique namespaces (no interference between test runs)
- Real Job/CronJob controllers are used (not mocked)
- The `retry` test waits for Kubernetes backoff AND at least 10 minutes after Job failure
- No timestamps or statuses are fabricated in tests
- CronJobs run every minute (Kubernetes minimum interval)
- Container tags are configurable in `cluster.py` for registry access
- Note: this test suite can use a fair amount of memory if you run many parallel tests

### Limitations

The Kind suite exercises equivalent observable behavior where possible, but:

- Does NOT simulate API outages
- Does NOT patch Kubernetes-generated status
- Does NOT simulate random API errors for response issues

These remain covered only by KMock.

### Cleanup

All test resources (namespaces, clusters) are automatically deleted after use, including on test failures.
You can delete tests/kind/.venv if you want.

---

## KWOK Load Harness

**Location:** `tests/kwok/`

Performance regression testing using KWOK (Kubernetes Without Kubelet). This suite tests operator scalability and resource usage under load.

### Key Features

- Starts an isolated KWOK cluster in ocker
- Installs the ImageSync CRD into its real Kubernetes API server
- Runs the operator as a local process against that API
- Does NOT use or alter current kubeconfig or existing clusters
- Measures operator CPU, memory, and API operation counts
- Supports both cluster-scoped and namespace-scoped testing

### System Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- Docker
- **Port 8080 free** (KWOK API server listens here unauthenticated)
  - We do not yet have config to use a different port number

### Important Notes

- KWOK runs control-plane controllers but does NOT execute Skopeo Pods
- KWOK does NOT copy images or guarantee successful Jobs
- The harness measures only the operator, not KWOK, Docker, or Python client
- Results depend on host capacity and API throughput
- Jobs never actually schedule, so this only tests the performance of the watch loops
  and basic reconciler behavior on create/delete/update events

### Running Load Tests

```sh
cd tests/kwok

# Install dependencies
uv sync --locked

# Full default test (100 ImageSyncs, 10 namespaces, 120s, extreme churn)
uv run --locked python run_load.py --max-crs 1000 --namespaces 100 --duration 120 --hold-ms 100 --output report.json

# Namespace-scoped testing
uv run --locked python run_load.py --scope namespace --max-crs 1000 --namespaces 100 --duration 60

# Quick test with custom binary
uv run --locked python run_load.py --operator-bin ../../target/release/image-sync-operator --max-crs 50 --namespaces 5

# Show all options
uv run --locked python run_load.py --help
```

### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `--max-crs` | 100 | Upper bound on live ImageSyncs |
| `--namespaces` | 10 | Number of workload namespaces |
| `--duration` | 60 | Seconds to run churn phase |
| `--hold-ms` | 2000 | Milliseconds to wait after patch |
| `--scope` | cluster | `cluster` or `namespace` watcher mode |
| `--concurrency` | auto | Limit simultaneous operations |
| `--operator-bin` | auto | Path to prebuilt operator binary |
| `--output` | none | JSON report path |

### Metrics Collected

- API operation counts
- Maximum live CRs observed
- Remaining accepted/ready CRs and Jobs
- Average and peak operator CPU (% of one logical CPU, so >100% on multicore)
- Peak RSS (resident memory in bytes)
- Phase timings (population, churn, cleanup)
- Optional: All timestamped process samples in JSON report

### Important Limitations

- Samples cover operator startup and load, but can miss short peaks
- A final interval shorter than half the sampling interval is included in average but excluded from peak (when earlier samples exist)
- No CPU or memory failure thresholds are imposed
- `scrape_operator_metrics()` is an explicit stub: the operator currently has no metrics endpoint
- Each sample records `operator_metrics: null`
- KWOK generally will use more cpu and memory than the operator itself, as the operator code is faster than kube-apiserver. This
  means that all results must be taken with a grain of salt
- Runs in host networking mode, as the docker proxy process bottlenecks at high connection rates

### Cleanup

The harness stops its operator process and deletes its uniquely named Docker container on normal exit, errors, or Ctrl-C. Operator logs are printed on failures.

---

## Running All Tests

### Pre-PR Checklist

```sh
# Build and run KMock tests (fast, runs in CI)
cd tests/kmock
./run_tests.py

# Optional: Include slow tests if touching timer-dependent code
./run_tests.py --slow
```

### Pre-Release Checklist

```sh
# 1. Build and run KMock tests
cd tests/kmock
./run_tests.py --slow

# 2. Run Kind integration tests
cd tests/kind
uv run --locked python run_tests.py

# 3. Run KWOK performance tests
cd tests/kwok
uv run --locked python run_load.py --max-crs 1000 --namespaces 100 --duration 120 --output perf-report.json
```
