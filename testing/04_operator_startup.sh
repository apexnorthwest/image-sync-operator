#!/bin/bash
set -euo pipefail
export KUBECONFIG=./testing-kubeconfig


# Build the operator image and push it to the local registry. Relies on BUILD_TOOL being set.
# bypasses tls checks for the local registry, which is insecure by default.
if [ "$BUILD_TOOL" == "docker" ]; then
  sudo docker build -t image-sync-operator:latest ../
  sudo kind load docker-image image-sync-operator:latest --name testing
elif [ "$BUILD_TOOL" == "podman" ]; then
  sudo podman build -t localhost/image-sync-operator:latest ../
  sudo podman save image-sync-operator:latest > ../image-sync-operator-latest.tar
  sudo kind load image-archive ../image-sync-operator-latest.tar --name testing
  sudo rm image-sync-operator-latest.tar
elif [ "$BUILD_TOOL" == "buildah" ]; then
  sudo buildah bud -t image-sync-operator:latest ../
  sudo buildah push image-sync-operator:latest docker-archive:"./image-sync-operator-latest.tar"
  sudo kind load image-archive ./image-sync-operator-latest.tar --name testing
  sudo rm -rf image-sync-operator-latest.tar
else
  echo "Unknown BUILD_TOOL: $BUILD_TOOL"
  exit 1
fi

# Installs the operator with helm and waits for it to be ready.
# We check the leader lease directly to ensure that the operator is actually running and has acquired the lease.

helm install image-sync-operator ../helm/image-sync-operator --namespace image-sync-operator --create-namespace \
  --set operator.image.repository=localhost/image-sync-operator \
  --set operator.image.tag=latest \
  --set operator.image.pullPolicy=Never \
  --set operator.debug=true \
  --set skopeo.image.repository=quay.io/skopeo/stable \
  --set skopeo.image.tag=latest \
  --set skopeo.image.pullPolicy=IfNotPresent

# Try for up to 3 minutes to get the leader lease, this method is inexact but simple.
for i in {1..180}; do
  if kubectl -n image-sync-operator logs deployment/image-sync-operator | grep -q "image-sync-operator:Operator has become the leader"; then
    echo "Operator has become the leader"
    break
  fi
  sleep 1
done
if [ $i -eq 180 ]; then
  echo "Operator did not become the leader within 3 minutes"
  exit 1
fi
