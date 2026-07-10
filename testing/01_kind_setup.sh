#!/bin/bash
set -e

# Install kubectl
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
chmod +x kubectl
sudo mv kubectl /usr/local/bin/kubectl

# Install kind and bootstrap a cluster
# For AMD64 / x86_64
[ $(uname -m) = x86_64 ] && curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.32.0/kind-linux-amd64
# For ARM64
[ $(uname -m) = aarch64 ] && curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.32.0/kind-linux-arm64
chmod +x ./kind
sudo mv ./kind /usr/local/bin/kind

# Bootstrap a kind cluster and fetch the kubeconfig
sudo /usr/local/bin/kind create cluster --name testing --wait 10m
sudo HOME=$HOME /usr/local/bin/kind get kubeconfig --name testing > ./testing-kubeconfig

# Check that the node is ready
export KUBECONFIG=./testing-kubeconfig
kubectl get nodes | grep " Ready "
