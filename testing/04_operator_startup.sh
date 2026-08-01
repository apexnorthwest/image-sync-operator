#!/bin/bash
set -euo pipefail
export KUBECONFIG=./testing-kubeconfig


docker build -f ../Dockerfile-debug -t image-sync-operator:latest ../
kind load docker-image image-sync-operator:latest --name testing

# Installs the operator with helm and waits for it to be ready.
# We check the leader lease directly to ensure that the operator is actually running and has acquired the lease.
kubectl apply -f ../crds/imagesyncs.yaml

# Create the namespace for the operator and create a CR for the operator to reconcile post install.
kubectl create secret docker-registry private-registry -n image-sync-operator --docker-server=registry-authenticated.registry.svc.cluster.local:5000 \
  --docker-username=testuser --docker-password=testpassword --docker-email=testuser@example.com
kubectl apply -f manifests/imagesyncs.yaml -n image-sync-operator --create-namespace

# Render the values file to add the certificate bundle and then install the operator.
cat values.yaml.template| CERTIFICATE_BUNDLE=$(cat registry.crt | awk '{print "              "$0}') envsubst > values.yaml
helm install image-sync-operator ../helm/image-sync-operator --namespace image-sync-operator -f values.yaml

# Try for up to 3 minutes to get the leader lease, this method is inexact but simple.
for i in {1..180}; do
  if kubectl -n image-sync-operator logs deployment/image-sync-operator | grep -q "Operator has become the leader"; then
    echo "Operator has become the leader"
    break
  fi
  sleep 1
done
if [ $i -eq 180 ]; then
  echo "Operator did not become the leader within 3 minutes"
  exit 1
fi

# TODO: check that the job gets created and succeeds. This could take a few minutes.
