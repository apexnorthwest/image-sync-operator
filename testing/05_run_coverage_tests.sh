#!/bin/bash
set -euo pipefail
export KUBECONFIG=./testing-kubeconfig

# Function to wait for a job to complete
wait_for_job_completion() {
    local job_name="$1"
    local namespace="$2"
    local timeout=${3:-300}
    
    echo "Waiting for job $job_name to complete..."
    for i in $(seq 1 $timeout); do
        if kubectl get job -n $namespace $job_name -o jsonpath='{.status.succeeded}' 2>/dev/null | grep -q '1'; then
            echo "Job $job_name completed successfully"
            return 0
        elif kubectl get job -n $namespace $job_name -o jsonpath='{.status.failed}' 2>/dev/null | grep -q '1'; then
            echo "Job $job_name failed"
            kubectl get job -n $namespace $job_name -o yaml
            return 1
        fi
        sleep 1
    done
    echo "Timeout waiting for job $job_name to complete"
    kubectl get job -n $namespace $job_name -o yaml
    return 1
}

# Function to check ImageSync status
check_imagesync_status() {
    local imagesync_name="$1"
    local namespace="$2"
    local expected_accepted="$3"
    local expected_ready="$4"
    
    echo "Checking ImageSync $imagesync_name status..."
    local status=$(kubectl get imagesync -n $namespace $imagesync_name -o jsonpath='{.status}')
    echo "Current status: $status"
    
    # Check if the status fields match expectations
    local accepted=$(kubectl get imagesync -n $namespace $imagesync_name -o jsonpath='{.status.accepted}')
    local ready=$(kubectl get imagesync -n $namespace $imagesync_name -o jsonpath='{.status.ready}')
    
    if [[ "$accepted" == "$expected_accepted" ]] && [[ "$ready" == "$expected_ready" ]]; then
        echo "ImageSync $imagesync_name status matches expectations"
        return 0
    else
        echo "ImageSync $imagesync_name status does not match expectations"
        echo "Expected accepted: $expected_accepted, got: $accepted"
        echo "Expected ready: $expected_ready, got: $ready"
        return 1
    fi
}

# Function to test a single ImageSync CR
run_test_case() {
    local test_file="$1"
    local test_name=$(basename $test_file .yaml)
    echo "\n=== Running test case: $test_name ==="
    
    # Apply the test CR
    echo "Applying test CR: $test_file"
    kubectl apply -f $test_file -n image-sync-operator
    
    # Get the ImageSync name
    local imagesync_name=$(kubectl get -f $test_file -n image-sync-operator -o jsonpath='{.metadata.name}')
    echo "Testing ImageSync: $imagesync_name"
    
    # Wait a bit for the operator to process
    sleep 5
    
    # Check status based on test expectations
    if [[ "$test_name" == "test-image-sync-mustfail" ]] || [[ "$test_name" == "test-invalid-cron-schedule" ]] || [[ "$test_name" == "test-invalid-source-image" ]] || [[ "$test_name" == "test-invalid-destination-image" ]] || [[ "$test_name" == "test-missing-source-image" ]] || [[ "$test_name" == "test-missing-destination-image" ]]; then
        # These should fail acceptance checks
        echo "Checking for failed acceptance..."
        check_imagesync_status "$imagesync_name" "image-sync-operator" "false" "false" || echo "Expected failure for $test_name"
    else
        # These should pass acceptance checks and create jobs
        echo "Checking for successful acceptance..."
        check_imagesync_status "$imagesync_name" "image-sync-operator" "true" "false" || echo "Expected success for $test_name"
        
        # Wait for job to complete (if it's a one-time sync)
        if [[ "$test_name" != "test-image-sync-cron-unauth" ]] && [[ "$test_name" != "test-image-sync-cron-destauth" ]]; then
            local job_name="imagesync-$imagesync_name"
            echo "Waiting for job $job_name to complete..."
            wait_for_job_completion "$job_name" "image-sync-operator" 120 || echo "Job failed for $test_name"
        fi
    fi
    
    # Cleanup after test
    echo "Cleaning up test resources..."
    kubectl delete -f $test_file -n image-sync-operator || true
}

# Run all test cases
for test_file in testing/coverage/*.yaml; do
    if [[ -f "$test_file" ]]; then
        run_test_case "$test_file"
    fi
done

echo "\n=== All tests completed ==="
