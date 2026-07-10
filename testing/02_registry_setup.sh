#!/bin/bash
export KUBECONFIG=./testing-kubeconfig
set -e

# Generate a short-lived, self-signed ssl certificate for the registry
# This is required to use the registry in authenticated mode
openssl req -newkey rsa:4096 -nodes -sha256 -keyout registry.key -x509 -days 7 \
  -out registry.crt -subj "/C=US/ST=California/L=San Francisco/O=My Company/CN=registry.registry.svc.cluster.local" \
  -addext \
"subjectAltName = DNS:registry-authenticated.registry.svc.cluster.local,DNS:registry-authenticated.registry.svc,\
DNS:registry-authenticated.svc,DNS:registry-authenticated,DNS:registry.registry.svc.cluster.local,\
DNS:registry.registry.svc,DNS:registry.svc,DNS:registry"

# Create a namespace for the registry
kubectl create namespace registry || true

# Import cert into a secret
kubectl delete secret registry-tls -n registry || true
kubectl create secret tls registry-tls --cert=registry.crt --key=registry.key -n registry

# Apply the manifest
# We create both an authenticated and unauthenticated registry, as to fully test the operator's ability to handle both scenarios
kubectl apply -f manifests/registry.yaml -n registry

# Create a secret for the authenticated registry
kubectl create secret docker-registry private-registry -n registry --docker-server=registry-authenticated.registry.svc.cluster.local:5000 \
  --docker-username=testuser --docker-password=testpassword --docker-email=testuser@example.com
