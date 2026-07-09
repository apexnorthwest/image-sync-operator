"""
This module contains the string templates for generating Kubernetes Job manifests.
"""

JOB_TEMPLATE_NO_CREDS = """
apiVersion: batch/v1
kind: Job
metadata:
  name: {job_name}
  namespace: {namespace}
spec:
  template:
    spec:
      containers:
      - name: imagecopy
        image: {image}
        command: {command}
      restartPolicy: Never
"""

JOB_TEMPLATE_SRC_CREDS = """
apiVersion: batch/v1
kind: Job
metadata:
  name: {job_name}
  namespace: {namespace}
spec:
  template:
    spec:
      containers:
        - name: imagecopy
          image: {image}
          command: ["skopeo", "copy", "--src-authfile", "/creds/src/.dockerconfig", "{src}", "{dest}"]
          volumeMounts:
            - name: src-creds
              mountPath: /creds/src
      volumes:
        - name: src-creds
          secret:
            secretName: {src_secret}
      restartPolicy: Never
"""

JOB_TEMPLATE_DEST_CREDS = """
apiVersion: batch/v1
kind: Job
metadata:
  name: {job_name}
  namespace: {namespace}
spec:
  template:
    spec:
      containers:
        - name: imagecopy
          image: {image}
          command: ["skopeo", "copy", "--dest-authfile", "/creds/dest/.dockerconfig", "{src}", "{dest}"]
          volumeMounts:
            - name: dest-creds
              mountPath: /creds/dest
      volumes:
        - name: dest-creds
          secret:
            secretName: {dest_secret}
      restartPolicy: Never
"""

JOB_TEMPLATE_BOTH_CREDS = """
apiVersion: batch/v1
kind: Job
metadata:
  name: {job_name}
  namespace: {namespace}
spec:
  template:
    spec:
      containers:
        - name: imagecopy
          image: {image}
          command: ["skopeo", "copy", "--src-authfile", "/creds/src/.dockerconfig", "--dest-authfile", "/creds/dest/.dockerconfig", "{src}", "{dest}"]
          volumeMounts:
            - name: src-creds
              mountPath: /creds/src
            - name: dest-creds
              mountPath: /creds/dest
      volumes:
        - name: src-creds
          secret:
            secretName: {src_secret}
        - name: dest-creds
          secret:
            secretName: {dest_secret}
      restartPolicy: Never
"""
