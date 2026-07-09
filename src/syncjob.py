"""
This class represents a running, completed, or failed sync job. It should exist 1:1 with a Job in kubernetes.
"""

# import requests
from kubedantic.models.io.k8s.api.batch.v1 import Job

job = Job.model_validate_json(
    '{"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "example-job", "namespace": "default"}, "spec": {"template": {"spec": {"containers": [{"name": "example-container", "image": "example-image"}], "restartPolicy": "Never"}}}, "status": {"conditions": [{"type": "Complete", "status": "True", "lastProbeTime": "2023-01-01T00:00:00Z", "lastTransitionTime": "2023-01-01T00:00:00Z"}]}}'
)
