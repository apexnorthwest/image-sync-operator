#!/bin/bash
set -e
rm -f ./testing-kubeconfig values.yaml
sudo kind delete cluster --name testing
