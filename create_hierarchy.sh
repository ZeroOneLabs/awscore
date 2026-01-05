#!/bin/bash

# Script to create Python module directory structure for Lambda framework
# Usage: ./create_project_structure.sh [module_name]

MODULE_NAME="aws_core"
BASE_DIR="./src/${MODULE_NAME}"

echo "Creating module structure for '${MODULE_NAME}' in ${BASE_DIR}..."

# Create directory structure
mkdir -p "${BASE_DIR}/core"
mkdir -p "${BASE_DIR}/aws"
mkdir -p "${BASE_DIR}/database"

# Create __init__.py files
touch "${BASE_DIR}/__init__.py"
touch "${BASE_DIR}/aws/__init__.py"
touch "${BASE_DIR}/database/__init__.py"

# Create core module files
touch "${BASE_DIR}/core/logging.py"

# Create AWS helper files
touch "${BASE_DIR}/aws/secrets.py"
touch "${BASE_DIR}/aws/s3.py"
touch "${BASE_DIR}/aws/lambda_helper.py"

# Create database files
touch "${BASE_DIR}/database/postgres.py"

# Create version file
cat > "${BASE_DIR}/version.py" << 'EOF'
"""Version information for the module."""

__version__ = "0.1.0"
__author__ = "Your Organization"
EOF

# Create main package __init__.py with version import
cat > "${BASE_DIR}/__init__.py" << 'EOF'
"""
Lambda Framework - Python utilities for AWS Lambda development.

Core modules for configuration, logging, and AWS service integration.
"""

from .version import __version__

__all__ = ["__version__"]
EOF

# Create core __init__.py with exports
cat > "${BASE_DIR}/core/__init__.py" << 'EOF'
"""Core modules for configuration and logging."""

from .config import Config, ConfigError, DependencyError, AWSConfig

__all__ = [
    "Config",
    "ConfigError", 
    "DependencyError",
    "AWSConfig",
]
EOF