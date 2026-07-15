"""
This class represents a running, completed, or failed sync job. It should exist 1:1 with a Job in kubernetes.
"""

from .imagesync import ImageSyncSpec
import jinja2
import requests


job_template = """
---
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ job_name }}
  namespace: {{ namespace }}
  labels:
    imagesync.apexnw.dev/controller: {{ operator_namespace }}/{{ operator_name }}
    imagesync.apexnw.dev/imagesync: {{ parent_name }}
spec:
  template:
    spec:
      containers:
        - name: imagecopy
          image: {{ image }}
          imagePullPolicy: {{ image_pull_policy }}
          command: 
            - /bin/bash
            - "-c"
            - |
              set -e
              {{ if ca_bundle }}
              echo >> /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem <<EOF
              {{ ca_bundle | indent(14) }}
              EOF
              {{ endif }}
              skopeo copy {{ if src_secret }}--src-authfile /creds/src/.dockerconfigjson{{ endif }} {{ if dest_secret }}--dest-authfile /creds/dest/.dockerconfigjson{{ endif }} {{ src }} {{ dest }}
          volumeMounts:
            {{ if src_secret }}
            - name: src-creds
              mountPath: /creds/src
              subPath: .dockerconfigjson
            {{ endif }}
            {{ if dest_secret }}
            - name: dest-creds
              mountPath: /creds/dest
              subPath: .dockerconfigjson
            {{ endif }}
      {{ if src_secret or dest_secret }}
      volumes:
      {{ endif }}
        {{ if src_secret }}
        - name: src-creds
          secret:
            secretName: {{ src_secret }}
        {{ endif }}
        {{ if dest_secret }}
        - name: dest-creds
          secret:
            secretName: {{ dest_secret }}
        {{ endif }}
      restartPolicy: Never
"""


class SyncJob:
    """Represents a sync job in Kubernetes. This class provides methods to check the status of the job, start it, and delete it."""

    _name: str
    _namespace: str
    _spec: ImageSyncSpec

    def __init__(
        self,
        parent_name: str,
        namespace: str,
        controller_namespace: str,
        controller_name: str,
        spec: ImageSyncSpec,
        ca_bundle: str | None = None,
        skopeo_image: str = 'quay.io/skopeo/stable:latest',
        skopeo_pull_policy: str = 'IfNotPresent',
    ):
        """
        Initialize the SyncJob.

        Args:
            parent_name (str): The name of the parent ImageSync object.
            namespace (str): The namespace in which the job will run.
            spec (ImageSyncSpec): The specification of the ImageSync object.
            ca_bundle (str | None): The CA bundle to use for TLS verification. Defaults to None.
            skopeo_image (str): The Skopeo image to use for the job.
            skopeo_pull_policy (str): The image pull policy for the Skopeo image.

        """
        self._name = parent_name
        self._namespace = namespace
        self._spec = spec
        self._ca_bundle = ca_bundle
        self._skopeo_image = skopeo_image
        self._controller_namespace = controller_namespace
        self._controller_name = controller_name
        self._skopeo_pull_policy = skopeo_pull_policy

    def is_complete(self) -> bool:
        """
        Check if the job has completed successfully.

        Returns:
            bool: True if the job is complete, False otherwise.
        """
        job_status = requests.get(
            f'https://kubernetes.default.svc/apis/batch/v1/namespaces/{self._namespace}/jobs/{self._name}/status'
        )
        if job_status.status_code == 200:
            status_json = job_status.json()
            if 'conditions' in status_json.get('status', {}):
                for condition in status_json['status']['conditions']:
                    if condition['type'] == 'Complete' and condition['status'] == 'True':
                        return True
        else:
            raise Exception(f'Failed to get job status: {job_status.status_code} - {job_status.text}')
        return False

    def is_failed(self) -> bool:
        """
        Check if the job has failed.

        Returns:
            bool: True if the job has failed, False otherwise.
        """
        job_status = requests.get(
            f'https://kubernetes.default.svc/apis/batch/v1/namespaces/{self._namespace}/jobs/{self._name}/status'
        )
        if job_status.status_code == 200:
            status_json = job_status.json()
            if 'conditions' in status_json.get('status', {}):
                for condition in status_json['status']['conditions']:
                    if condition['type'] == 'Failed' and condition['status'] == 'True':
                        return True
        else:
            raise Exception(f'Failed to get job status: {job_status.status_code} - {job_status.text}')
        return False

    def start_job(self):
        """
        Submit the job to the kubernetes api.
        """
        template = jinja2.Template(job_template)
        job_manifest = template.render(
            job_name=self._name[:58] + '-sync',
            namespace=self._namespace,
            controller_namespace=self._controller_namespace,
            controller_name=self._controller_name,
            image=self._skopeo_image,
            image_pull_policy=self._skopeo_pull_policy,
            src=self._spec.source.image,
            dest=self._spec.destination.image,
            src_secret=self._spec.source.registryLoginSecret if self._spec.source.registryLoginSecret else None,
            dest_secret=self._spec.destination.registryLoginSecret
            if self._spec.destination.registryLoginSecret
            else None,
            ca_bundle=self._ca_bundle if self._ca_bundle else None,
        )
        resp = requests.post(
            f'https://kubernetes.default.svc/apis/batch/v1/namespaces/{self._namespace}/jobs',
            data=job_manifest,
            headers={'Content-Type': 'application/yaml'},
        )
        if resp.status_code not in [200, 201]:
            raise Exception(f'Failed to create job: {resp.status_code} - {resp.text}')

    def delete_job(self):
        """
        Delete the job from the kubernetes api.
        """
        resp = requests.delete(
            f'https://kubernetes.default.svc/apis/batch/v1/namespaces/{self._namespace}/jobs/{self._name}'
        )
        if resp.status_code not in [200, 202]:
            raise Exception(f'Failed to delete job: {resp.status_code} - {resp.text}')
