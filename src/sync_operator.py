"""
These functions implement the core functionality of the image-sync-operator.
They monitor the CRDs for changes and trigger the sync Jobs to run.
"""

from datetime import datetime, timezone
from kubedantic.models.io.k8s.apimachinery.pkg.apis.meta.v1 import ObjectMeta
from kubedantic.models.io.k8s.api.coordination.v1 import Lease, LeaseSpec
import logging
import os
import requests
import signal
import sys
import threading
import time
import tomllib


class InitError(Exception):
    """
    Raised when the operator fails to initialize.
    """
    pass


class OperationError(Exception):
    """
    Raised when the operator fails to perform an operation.
    """
    pass


class Operator:
    """
    The Operator class is responsible for monitoring the CRDs and triggering the sync Jobs.
    It uses the requests library to make api calls and watch the CRDs for changes.

    Takes no setup arguments, as all config is found in the environment.
    """

    _logger: logging.Logger
    _cluster_scope: bool
    _namespace: str
    _api_token: str
    _token_lock: threading.Lock
    _watched_namespaces: list[str]
    _skopeo_image: str
    _skopeo_pull_policy: str
    _skopeo_ca_bundle: str | None
    _shutdown: threading.Event = threading.Event()

    def __init__(self):
        """
        Initialize the Operator class.
        """
        print('Initializing Operator')
        try:
            self._logger = logging.getLogger('image-sync-operator')
            if 'DEBUG' in os.environ:
                self._logger.setLevel(logging.DEBUG)
            with open('/config/config.toml', 'rb') as f:
                config = tomllib.load(f)
            self._cluster_scope = config.get('cluster_scope', False)
            with open('/var/run/secrets/kubernetes.io/serviceaccount/namespace', 'r') as f:
                self._namespace = f.read().strip()
            self._watched_namespaces = config.get('watched_namespaces', [self._namespace])
            self._skopeo_image = config.get('skopeo', {}).get('image', 'quay.io/skopeo/stable:latest')
            self._skopeo_pull_policy = config.get('skopeo', {}).get('image_pull_policy', 'IfNotPresent')
            self._skopeo_ca_bundle = config.get('skopeo', {}).get('ca_trust_bundle')
            self._token_lock = threading.Lock()
            self._logger.debug(f'Operator initialized with cluster_scope={self._cluster_scope}, namespace={self._namespace}, watched_namespaces={self._watched_namespaces}, skopeo_image={self._skopeo_image}, skopeo_pull_policy={self._skopeo_pull_policy}, skopeo_ca_bundle={self._skopeo_ca_bundle}')
        except Exception as e:
            raise InitError('Failed to initialize Operator') from e

    def run(self):
        """
        Run the main loop of the operator.
        This method will watch for changes in the CRDs and trigger sync Jobs as needed.
        """
        print('Running Operator')
        self._logger.debug('Starting operator main loop')
        self._acquire_leader_lease()
        self._logger.info('Operator has become the leader, starting maintenance loop')
        maint_thread = threading.Thread(target=self._maintenance_loop, daemon=True)
        maint_thread.start()
        # TODO: Watch for changes in the CRDs
        # TODO: Trigger sync Jobs as needed
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)
        while self._shutdown.is_set() is False:
            self._shutdown.wait(1)
        self._cleanup()

    def _shutdown_handler(self, signum, frame):
        """
        Handle shutdown signals (SIGINT, SIGTERM).
        This method will be called when the operator receives a shutdown signal.
        """
        self._shutdown.set()

    def _cleanup(self):
        """
        Cleanup resources before shutting down the operator.
        This method will be called when the operator is shutting down.
        """
        self._logger.info('Shutting down operator')
        self._delete_leader_lease()
        sys.exit(0)

    def _update_token(self):
        """
        Update the service account token used for authentication with the Kubernetes API server.
        This method will read the token from the service account secret and update the headers used for API calls.
        """
        self._token_lock.acquire()
        self._logger.debug('Got token lock, Updating service account token')
        try:
            with open('/var/run/secrets/kubernetes.io/serviceaccount/token', 'r') as f:
                self._api_token = f.read().strip()
            self._logger.debug('Service account token updated successfully')
        except Exception as e:
            raise OperationError('Failed to update service account token') from e
        finally:
            self._logger.debug('Releasing token lock')
            self._token_lock.release()

    def _acquire_leader_lease(self):
        """
        Acquire a leader lease to ensure that only one instance of the operator is running.
        This method will block until the lease is acquired.
        """
        am_leader = False
        while not am_leader:
            self._update_token()
            current_lease = requests.get(f'https://kubernetes.default.svc/apis/coordination.k8s.io/v1/namespaces/{self._namespace}/leases/image-sync-operator', headers={'Authorization': f'Bearer {self._api_token}'}, verify='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')
            self._logger.debug(f'Queried current leader lease, status code: {current_lease.status_code}')
            if current_lease.status_code == 404:
                # Lease does not exist, create it
                self._logger.debug('Leader lease does not exist, creating it')
                lease_body = Lease(
                    apiVersion="coordination.k8s.io/v1",
                    kind="Lease",
                    metadata= ObjectMeta(
                        name="image-sync-operator",
                        namespace=self._namespace
                    ),
                    spec=LeaseSpec(
                        holderIdentity=os.environ['HOSTNAME'], # We want to fail if we don't know who we are, so we don't use a default value here
                        leaseDurationSeconds=120,
                        renewTime=datetime.now(timezone.utc)
                    )
                ).model_dump()
                lease_body['spec']['renewTime'] = lease_body['spec']['renewTime'].isoformat()
                response = requests.post(f'https://kubernetes.default.svc/apis/coordination.k8s.io/v1/namespaces/{self._namespace}/leases', json=lease_body, headers={'Authorization': f'Bearer {self._api_token}'}, verify='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')
                if response.status_code != 201:
                    raise OperationError(f'Failed to create leader lease: {response.text}')
                am_leader = True
                self._logger.info('Became Leader')
            elif current_lease.status_code == 200:
                # Lease exists, check if we are the holder
                lease: Lease = Lease.model_validate_json(current_lease.text)
                if lease.spec is None or lease.metadata is None:
                    raise OperationError('Got malformed lease from API server, spec or metadata is None')
                if lease.spec.holderIdentity is not None and lease.spec.holderIdentity == os.environ['HOSTNAME']:
                    # We are the holder, renew the lease
                    self._logger.debug('We are the holder of the leader lease, renewing it')
                    lease_body = lease.model_dump(include={'apiVersion', 'kind', 'spec'})
                    lease_body['metadata'] = {'name': 'image-sync-operator', 'namespace': self._namespace}
                    lease_body['metadata']['resourceVersion'] = lease.metadata.resourceVersion
                    lease_body['spec']['renewTime'] = datetime.now(timezone.utc).isoformat()
                    response = requests.put(f'https://kubernetes.default.svc/apis/coordination.k8s.io/v1/namespaces/{self._namespace}/leases/image-sync-operator', json=lease_body, headers={'Authorization': f'Bearer {self._api_token}'}, verify='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')
                    if response.status_code != 200:
                        raise OperationError(f'Failed to renew leader lease: {response.text}')
                    am_leader = True
                    self._logger.info('Renewed Leader Lease')
                else:
                    if lease.spec.leaseDurationSeconds is None:
                        raise OperationError('Got malformed lease from API server, leaseDurationSeconds is None')
                    # Check if the lease is expired
                    if lease.spec.renewTime is not None and int((datetime.now(timezone.utc) - lease.spec.renewTime).total_seconds()) > int(lease.spec.leaseDurationSeconds):
                        # Lease is expired, we can take it over
                        self._logger.debug('Leader lease is expired, taking it over')
                        lease.spec.holderIdentity = os.environ['HOSTNAME']
                        lease_body = lease.model_dump(include={'apiVersion', 'kind', 'spec'})
                        lease_body['metadata'] = {'name': 'image-sync-operator', 'namespace': self._namespace}
                        lease_body['metadata']['resourceVersion'] = lease.metadata.resourceVersion
                        lease_body['spec']['renewTime'] = datetime.now(timezone.utc).isoformat()
                        response = requests.put(f'https://kubernetes.default.svc/apis/coordination.k8s.io/v1/namespaces/{self._namespace}/leases/image-sync-operator', json=lease_body, headers={'Authorization': f'Bearer {self._api_token}'}, verify='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')
                        if response.status_code != 200:
                            raise OperationError(f'Failed to take over leader lease: {response.text}')
                        am_leader = True
                        self._logger.info('Took over Leader Lease')
                        break
                    # We are not the holder, wait for the lease to expire
                    am_leader = False
                    self._logger.info(f'Leader lease is held by {lease.spec.holderIdentity}, waiting for it to expire')
                    time.sleep(5)  # Wait for 5 seconds before checking the lease again
    
    def _delete_leader_lease(self):
        """
        Delete the leader lease to relinquish leadership.
        This method will delete the lease from the API server.
        """
        self._update_token()
        response = requests.delete(f'https://kubernetes.default.svc/apis/coordination.k8s.io/v1/namespaces/{self._namespace}/leases/image-sync-operator', headers={'Authorization': f'Bearer {self._api_token}'}, verify='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')
        if response.status_code != 200:
            self._logger.error(f'Failed to delete leader lease: {response.text}')
            # We don't raise an error here in order to allow shutdown to complete
        else:
            self._logger.info('Deleted Leader Lease')
    
    def _maintenance_loop(self):
        """
        Run the maintenance loop of the operator.
        This method will periodically check the leader lease and renew it if we are the holder.
        """
        while True:
            self._acquire_leader_lease()
            self._logger.debug('Leader lease is valid, sleeping for 30 seconds before next check')
            time.sleep(30)  # Sleep for 30 seconds before checking the lease again
