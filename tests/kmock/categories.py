"""Test categories; each lives in tests/test_<category>.py and is selectable as a pytest marker."""

CATEGORIES = {
    "crd": "CRD schema validation performed by the API server",
    "acceptance": "ImageSync acceptance checks (secrets, cron schedule, image references)",
    "job": "One-shot sync Job creation and lifecycle",
    "cronjob": "Scheduled sync CronJob creation and drift repair",
    "retry": "Failure handling and retry of failed Jobs",
    "lifecycle": "Spec changes, deletion and restarts",
    "operator": "Operator startup, configuration, leader election and scoping",
    "apifailure": "External failures of the Kubernetes API itself",
}
