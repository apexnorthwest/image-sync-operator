# Fake-cluster test suite

Integration tests that run the real operator binary against a KMock-based fake Kubernetes API
(`fakek8s.py`). No cluster is needed. Jobs and CronJobs are driven by the tests patching their status.
The fake also simulates background collection of same-namespace Jobs and CronJobs when
their ImageSync owner is deleted; it does not emulate all Kubernetes garbage-collection policies.
The KMock integration test workflow runs the full suite, including slow tests, on pull requests
and can also be started manually.

```sh
cd tests
./run_tests.py                       # build the operator, run everything except slow tests
./run_tests.py --slow                # include tests that wait on the operator's 30s/60s timers
./run_tests.py -c job -c retry       # categories (see --list)
./run_tests.py test_job.py::test_job_object_is_rendered_from_spec
./run_tests.py secret                # name substring (pytest -k)
./run_tests.py --no-build -j 1 -x    # reuse binary, serial, stop at first failure
```

Set `IMAGE_SYNC_OPERATOR_BIN` to test a specific binary. The operator reads its config from
`IMAGE_SYNC_OPERATOR_CONFIG` (default `/config/config.yaml`), which the harness sets.

Lint: `uvx ruff check . && uvx ty check` from this directory.
