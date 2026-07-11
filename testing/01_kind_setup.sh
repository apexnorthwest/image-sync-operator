#!/bin/bash
set -e

# Setup a test environment using KIND.

# This script presumes you have working kind and kubectl binaries in your path.
# It also assumes you can run kind with sudo, as rootless kind is less well supported.
# If you know that your kind installation can run rootless, you can change the two below commands.

# Bootstrap a kind cluster and fetch the kubeconfig
sudo kind create cluster --name testing --wait 10m
sudo HOME=$HOME kind get kubeconfig --name testing > ./testing-kubeconfig

# Check that the node is ready
export KUBECONFIG=./testing-kubeconfig
kubectl get nodes | grep " Ready "
