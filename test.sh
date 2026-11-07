#!/bin/bash

# Initialize variables to store passed and failed test cases
passed_cases=""
failed_cases=""
error_cases=""
# Function to run pytest and process the output
# Function to run pytest and process the output
run_test() {
    # Run pytest with verbose and no capture, stream output in real-time via tee
    python3 -u -m pytest -vv "$1" 2>&1 | tee pytest_output.txt

    # Check if pytest output contains "failed"
    if grep -q "FAILED test_cases" pytest_output.txt; then
        # Handle failed test case
        echo "Test case $1 failed"
        failed_cases+=$(grep "FAILED test_cases" pytest_output.txt)
        return 1
    fi

    if grep -q "ERROR test_cases" pytest_output.txt; then
        # Handle failed test case
        echo "Test case $1 error"
        error_cases+=$(grep "ERROR test_cases" pytest_output.txt)
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

# Write the GitHub summary only when a test failed or errored.
if { [ -n "$failed_cases" ] || [ -n "$error_cases" ]; } && [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
        echo "# Test Results Summary"
        echo "Passed test cases: ${passed_cases}"
        echo "Failed test cases: ${failed_cases}"
        echo "ERROR test cases: ${error_cases}"
    } >> "$GITHUB_STEP_SUMMARY"
fi
# Function to sanitize filename: replace invalid characters for artifact upload
# Invalid chars: " : < > | * ? \r \n /
# Also truncate to 200 chars max to avoid filesystem limits
sanitize_filename() {
    echo "$1" | tr '/:\"<>|*?\r\n' '___________' | cut -c1-200
}

# Check if there are any failed cases
if [ -n "$failed_cases" ]; then
    sanitized=$(sanitize_filename "$failed_cases")
    echo "Exist failed cases:${sanitized}"
    mv report/report.html "report/${sanitized}.html"
    exit 1
fi

# Check if there are any error cases
if [ -n "$error_cases" ]; then
    sanitized=$(sanitize_filename "$error_cases")
    echo "Exist error cases:${sanitized}"
    mv report/report.html "report/${sanitized}.html"
    exit 2
fi
rm -rf report/report.html
echo "No failed or error cases found"
