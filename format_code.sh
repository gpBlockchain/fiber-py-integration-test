#!/bin/bash

# Format the entire project using black
black .

## Run pylint on all Python files
#pylint $(find . -name "*.py")
