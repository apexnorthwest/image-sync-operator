#!/bin/bash
set -e

# Setup a test environment using KIND.
# These scripts presume you have working kind, kubectl, helm, and docker that do not require sudo to run.

# Bootstrap a kind cluster and fetch the kubeconfig
if docker --version >/dev/null 2>&1; then
    echo "Docker is installed"
else
    echo "Docker is not installed. Please install Docker to continue."
    exit 1
fi
if kind version >/dev/null 2>&1; then
    echo "Kind is installed"
else
    echo "Kind is not installed. Please install Kind to continue."
    exit 1
fi
if kubectl version --client >/dev/null 2>&1; then
    echo "kubectl is installed"
else
    echo "kubectl is not installed. Please install kubectl to continue."
    exit 1
fi
if helm version >/dev/null 2>&1; then
    echo "Helm is installed"
else
    echo "Helm is not installed. Please install Helm to continue."
    exit 1
fi
kind create cluster --name testing --wait 10m
HOME=$HOME kind get kubeconfig --name testing > ./testing-kubeconfig

# Check that the node is ready
export KUBECONFIG=./testing-kubeconfig
kubectl get nodes | grep " Ready "
