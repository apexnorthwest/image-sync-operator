#!/bin/bash
set -e

# Setup a test environment using KIND.

# This script presumes you have working kind, kubectl, and helm binaries in your path.
# It also assumes you can run kind with sudo, as rootless kind is less well supported.
# If you know that your kind installation can run rootless, you can change the two below commands.

# Bootstrap a kind cluster and fetch the kubeconfig
if docker --version >/dev/null 2>&1; then
    echo "Docker is installed"
else
    echo "Docker is not installed. Please install Docker to continue."
    exit 1
fi
kind create cluster --name testing --wait 10m
HOME=$HOME kind get kubeconfig --name testing > ./testing-kubeconfig

# Check that the node is ready
export KUBECONFIG=./testing-kubeconfig
kubectl get nodes | grep " Ready "
