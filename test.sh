#!/bin/bash

# Initialize variables to store passed and failed test cases
passed_cases=""
failed_cases=""
error_cases=""
# Function to run pytest and process the output
# Function to run pytest and process the output
run_test() {
    # Run pytest with verbose and no capture, redirect stderr to stdout
    pytest_output=$(python3 -m pytest -vv "$1" 2>&1)

    # Print pytest output
    echo "$pytest_output" > pytest_output.txt

    # Check if pytest output contains "failed"
    if grep -q "FAILED test_cases" <<< "$pytest_output"; then
        # Handle failed test case
        echo "Test case $1 failed"
        failed_cases+=$(grep "FAILED test_cases" pytest_output.txt)
        return 1
    fi

    if grep -q "ERROR test_cases" <<< "$pytest_output"; then
        # Handle failed test case
        echo "Test case $1 error"
        error_cases+=$(grep "FAILED test_cases" pytest_output.txt)
        return 1
    fi

    # Handle successful test case
    echo "Test case $1 passed"
    passed_cases+=" $1"
    return 0
}


# Main loop to run tests for each test case
for test_case in "$@"; do
    run_test "$test_case"
done

# Display summary of test results
echo "Summary:"
echo "Passed test cases:${passed_cases}"
echo "Failed test cases:${failed_cases}"
echo "ERROR test cases:${error_cases}"

# Write summary to GITHUB_STEP_SUMMARY
echo "# Test Results Summary" >> $GITHUB_STEP_SUMMARY
echo "Passed test cases: ${passed_cases}" >> $GITHUB_STEP_SUMMARY
echo "Failed test cases: ${failed_cases}" >> $GITHUB_STEP_SUMMARY
echo "ERROR test cases: ${error_cases}" >> $GITHUB_STEP_SUMMARY
# Check if there are any failed cases
if [ -n "$failed_cases" ]; then
    echo "Exist failed cases:${failed_cases//\//_}"
    mv report/report.html "report/${failed_cases//\//_}.html"
    exit 1
fi

# Check if there are any error cases
if [ -n "$error_cases" ]; then
    echo "Exist error cases:${error_cases//\//_}"
    mv report/report.html "report/${error_cases//\//_}.html"
    exit 2
fi
rm -rf report/report.html
echo "No failed or error cases found"