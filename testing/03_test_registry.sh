#!/bin/bash
set -e
set -o pipefail
export KUBECONFIG=./testing-kubeconfig

# This script verifies the registries are functioning prior to testing the operator

# Check if the registry is running
kubectl get pods -n registry | grep -E "registry-0.*Running"
kubectl get pods -n registry | grep -E "registry-authenticated-0.*Running"

# Run an unauthenticated registry test
cat manifests/testjob_1_unauth.yaml.template | CERTIFICATE_BUNDLE=$(cat registry.crt | awk '{print "              "$0}') envsubst | kubectl apply -f - -n registry
kubectl wait --for=condition=complete --timeout=300s job/test1 -n registry
kubectl logs job/test1 -n registry | grep "Test 1 Passed"

# Run an authenticated destination registry test
cat manifests/testjob_2_destauth.yaml.template | CERTIFICATE_BUNDLE=$(cat registry.crt | awk '{print "              "$0}') envsubst | kubectl apply -f - -n registry
kubectl wait --for=condition=complete --timeout=300s job/test2 -n registry
kubectl logs job/test2 -n registry | grep "Test 2 Passed"

# Run an authenticated source registry test
cat manifests/testjob_3_srcauth.yaml.template | CERTIFICATE_BUNDLE=$(cat registry.crt | awk '{print "              "$0}') envsubst | kubectl apply -f - -n registry
kubectl wait --for=condition=complete --timeout=300s job/test3 -n registry
kubectl logs job/test3 -n registry | grep "Test 3 Passed"

# Run a fully authenticated registry test
cat manifests/testjob_4_fullauth.yaml.template | CERTIFICATE_BUNDLE=$(cat registry.crt | awk '{print "              "$0}') envsubst | kubectl apply -f - -n registry
kubectl wait --for=condition=complete --timeout=300s job/test4 -n registry
kubectl logs job/test4 -n registry | grep "Test 4 Passed"

echo "Registry test environment is ready."

kubectl delete job/test1 -n registry
kubectl delete job/test2 -n registry
kubectl delete job/test3 -n registry
kubectl delete job/test4 -n registry
