"""
These functions implement the core functionality of the image-sync-operator.
They monitor the CRDs for changes and trigger the sync Jobs to run.
"""

import tomllib
from typing import Any


class InitError(Exception):
    """
    Raised when the operator fails to initialize.
    """

    pass


class Operator:
    """
    The Operator class is responsible for monitoring the CRDs and triggering the sync Jobs.
    It uses the requests library to make api calls and watch the CRDs for changes.

    Takes no setup arguments, as all config is found in the environment.
    """

    _config: dict[str, Any]
    _cluster_scope: bool
    _namespace: str
    _watched_namespaces: list[str]
    _skopeo_image: str
    _skopeo_pull_policy: str
    _skopeo_ca_bundle: str | None

    def __init__(self):
        """
        Initialize the Operator class.
        """
        try:
            with open('/config/config.toml', 'rb') as f:
                self._config = tomllib.load(f)
            self._cluster_scope = self._config.get('cluster_scope', False)
            with open('/var/run/secrets/kubernetes.io/serviceaccount/namespace', 'r') as f:
                self._namespace = f.read().strip()
            self._watched_namespaces = self._config.get('watched_namespaces', [self._namespace])
            self._skopeo_image = self._config.get('skopeo', {}).get('image', 'quay.io/skopeo/stable:latest')
            self._skopeo_pull_policy = self._config.get('skopeo', {}).get('image_pull_policy', 'IfNotPresent')
            self._skopeo_ca_bundle = self._config.get('skopeo', {}).get('ca_trust_bundle')
        except Exception as e:
            raise InitError('Failed to initialize Operator') from e

    def run(self):
        """
        Run the main loop of the operator.
        This method will watch for changes in the CRDs and trigger sync Jobs as needed.
        """
        pass
        # TODO: Get and maintain a leader lease
        # TODO: Watch for changes in the CRDs
        # TODO: Trigger sync Jobs as needed
