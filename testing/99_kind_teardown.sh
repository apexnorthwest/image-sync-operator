#!/bin/bash
set -e
rm -f ./testing-kubeconfig values.yaml
kind delete cluster --name testing
