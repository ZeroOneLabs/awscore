"""
Core configuration module for Lambda application framework.

Handles loading configuration from multiple sources with precedence:
1. Environment variables (highest priority)
2. AWS Lambda environment context
3. Local configuration files (lowest priority)

Automatically manages AWS credentials and regional configuration.
"""

import os
import tomllib
import yaml
import json
import sys
from pathlib import Path
from typing import Any, Dict
from dataclasses import dataclass, field
import threading
import importlib.util


class ConfigError(Exception):
    """Raised when configuration loading or validation fails."""

    pass


class DependencyError(Exception):
    """Raised when required dependencies are not installed."""

    pass


@dataclass
class AWSConfig:
    """AWS-specific configuration settings."""

    region: str = "us-east-1"
    use_local_credentials: bool = False
    credentials_path: Path | None = None
    lambda_name: str | None = None
    environment: str | None = None

    def __post_init__(self):
        if self.credentials_path and isinstance(self.credentials_path, str):
            self.credentials_path = Path(self.credentials_path).expanduser()


class Config:
    """
    Central configuration management for Lambda applications (Singleton).

    Loads and merges configuration from:
    - project.toml (project metadata and defaults)
    - config.toml/config.yaml (application configuration)
    - .env files (environment-specific overrides)
    - AWS Lambda context (runtime environment detection)
    - Environment variables (highest priority overrides)

    Singleton pattern ensures configuration is loaded once per container lifecycle.

    Example:
        >>> config = Config()  # Returns same instance everywhere
        >>> db_host = config.get('database.host')
        >>> aws_region = config.aws.region
    """

    _instance: "Config" | None = None
    _lock = None  # Will be initialized as threading.Lock on first use

    def __new__(cls, project_root: Path | None = None):
        """
        Create or return existing singleton instance.

        Thread-safe implementation for concurrent Lambda invocations.
        """
        if cls._lock is None:
            import threading

            cls._lock = threading.Lock()

        if cls._instance is None:
            with cls._lock:
                # Double-check locking pattern
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance

        return cls._instance

    def __init__(self, project_root: Path | None = None):
        """
        Initialize configuration loader (only runs once due to singleton).

        Args:
            project_root: Root directory of the project. Defaults to current working directory.
        """
        # Prevent re-initialization
        if self._initialized:
            return

        self.project_root = Path(project_root or os.getcwd())
        self._config: Dict[str, Any] = {}
        self._aws_config: AWSConfig | None = None
        self._loaded = False
        self._initialized = True

    def load(self) -> "Config":
        """
        Load all configuration sources in order of precedence.

        Returns:
            Self for method chaining.

        Raises:
            ConfigError: If critical configuration files are missing or invalid.
        """
        if self._loaded:
            return self

        # Load in order (later sources override earlier)
        self._load_project_toml()
        self._load_config_files()
        self._load_env_file()
        self._detect_lambda_environment()
        self._load_env_vars()
        self._validate_dependencies()
        self._initialize_aws_config()
        self._validate_aws_connection()

        self._loaded = True
        return self

    def _load_project_toml(self) -> None:
        """Load project.toml for project metadata and base configuration."""
        project_file = self.project_root / "project.toml"
        if not project_file.exists():
            raise ConfigError(f"Required project.toml not found at {project_file}")

        try:
            with open(project_file, "rb") as f:
                data = tomllib.load(f)
                self._merge_config(data)

                # Load environment variables from [project.env.vars] into os.environ
                # This enables boto3 to pick up AWS_PROFILE and other AWS settings
                env_vars_config = data.get("project", {}).get("env", {}).get("vars", {})
                if env_vars_config:
                    self._load_environment_variables(env_vars_config)
        except Exception as e:
            raise ConfigError(f"Failed to load project.toml: {e}")

    def _load_environment_variables(self, env_vars_config: dict[str, Any]) -> None:
        """
        Load environment variables from config into os.environ.

        Supports both direct values and environment-specific values:
        [project.env.vars]
        SOME_VAR = "value"  # Direct value

        [project.env.vars.AWS_PROFILE]
        dev = "my-company-dev"
        uat = "my-company-uat"
        prod = "my-company-prod"

        Args:
            env_vars_config: Environment variables configuration dict
        """
        for key, value in env_vars_config.items():
            # Skip if already in environment (shell env vars take precedence)
            if key in os.environ:
                continue

            # Check if value is environment-specific (dict with dev/uat/prod keys)
            if isinstance(value, dict):
                # This is an environment-specific variable
                environment = self._config.get("environment") or self._config.get(
                    "aws", {}
                ).get("environment")

                if environment and environment in value:
                    env_value = str(value[environment])
                    os.environ[key] = env_value
                    self.logger.debug(
                        f"Loaded environment variable: {key}={env_value}",
                        extra={"environment": environment},
                    )
                else:
                    # Environment not detected or not in config, skip
                    self.logger.warning(
                        f"Could not load environment-specific variable '{key}' - environment not detected or not configured"
                    )
            else:
                # Direct value (not environment-specific)
                os.environ[key] = str(value)
                self.logger.debug(f"Loaded environment variable: {key}")

    def _load_config_files(self) -> None:
        """Load config.toml or config.yaml if present."""
        # Try TOML first
        config_toml = self.project_root / "config.toml"
        if config_toml.exists():
            try:
                with open(config_toml, "rb") as f:
                    data = tomllib.load(f)
                    self._merge_config(data)
                return
            except Exception as e:
                raise ConfigError(f"Failed to load config.toml: {e}")

        # Fall back to YAML
        config_yaml = self.project_root / "config.yaml"
        if config_yaml.exists():
            try:
                with open(config_yaml, "r") as f:
                    data = yaml.safe_load(f)
                    if data:
                        self._merge_config(data)
                return
            except Exception as e:
                raise ConfigError(f"Failed to load config.yaml: {e}")

    def _load_env_file(self) -> None:
        """Load .env file for environment-specific variables."""
        env_file = self.project_root / ".env"
        if not env_file.exists():
            return

        try:
            with open(env_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")

                        # Set in os.environ if not already set
                        if key not in os.environ:
                            os.environ[key] = value

                        # Store in config under 'env' namespace
                        self._set_nested(f"env.{key}", value)
        except Exception as e:
            # Non-critical, just log and continue
            self.logger.warning(f"Failed to load .env file: {e}")

    def _load_env_vars(self) -> None:
        """Load environment variables with APP_ prefix into config."""
        prefix = "APP_"
        for key, value in os.environ.items():
            if key.startswith(prefix):
                # Convert APP_DATABASE_HOST to database.host
                config_key = key[len(prefix) :].lower().replace("_", ".")
                self._set_nested(config_key, value)

    def _detect_lambda_environment(self) -> None:
        """Detect if running in AWS Lambda and extract environment info."""
        lambda_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
        if not lambda_name:
            # Not in Lambda - check for LOCAL_ENV or ENVIRONMENT variable for local dev
            local_env = os.environ.get("LOCAL_ENV") or os.environ.get("ENVIRONMENT")
            if local_env:
                self._set_nested("environment", local_env)
                self._set_nested("aws.environment", local_env)
                self.logger.info(
                    f"Local development mode detected", extra={"environment": local_env}
                )
            return

        self._set_nested("aws.lambda_name", lambda_name)

        # Parse environment from function name (second hyphen-separated segment)
        # Format: <PROJECT>-<ENV>-<function-name>
        # Example: myproject-dev-processor -> environment = "dev"
        parts = lambda_name.split("-")
        if len(parts) >= 3:
            project = parts[0]
            environment = parts[1]
            function = "-".join(parts[2:])  # Handle function names with hyphens

            self._set_nested("aws.project", project)
            self._set_nested("aws.environment", environment)
            self._set_nested("aws.function", function)
            self._set_nested("environment", environment)

    def _initialize_aws_config(self) -> None:
        """Initialize AWS configuration from loaded config data."""
        aws_data = self._config.get("aws", {})

        self._aws_config = AWSConfig(
            region=aws_data.get("region", os.environ.get("AWS_REGION", "us-east-1")),
            use_local_credentials=aws_data.get("use_local_credentials", False),
            credentials_path=aws_data.get("credentials_path"),
            lambda_name=aws_data.get("lambda_name"),
            environment=aws_data.get("environment"),
        )

    def _validate_dependencies(self) -> None:
        """
        Validate that all required dependencies are installed.

        Checks for dependencies in order of precedence:
        1. pyproject.toml ([project.dependencies] or [tool.poetry.dependencies])
        2. project.toml ([dependencies])
        3. requirements.txt

        Raises:
            DependencyError: If any required dependencies are missing.
        """
        required_packages = self._load_required_dependencies()

        if not required_packages:
            return  # No dependencies specified

        missing_packages = []

        for package in required_packages:
            # Parse package name from requirement string (handle versions, extras, etc.)
            package_name = self._parse_package_name(package)

            if not self._is_package_installed(package_name):
                missing_packages.append(package)

        if missing_packages:
            error_msg = (
                f"Missing required dependencies: {', '.join(missing_packages)}\n"
                f"Install with: pip install {' '.join(missing_packages)}"
            )
            raise DependencyError(error_msg)

    def _load_required_dependencies(self) -> list[str]:
        """
        Load required dependencies from available sources.

        Returns:
            List of required package specifications.
        """
        # Try pyproject.toml first (PEP 621 standard)
        pyproject_file = self.project_root / "pyproject.toml"
        if pyproject_file.exists():
            try:
                with open(pyproject_file, "rb") as f:
                    data = tomllib.load(f)

                    # Check PEP 621 format
                    if "project" in data and "dependencies" in data["project"]:
                        return data["project"]["dependencies"]

                    # Check Poetry format
                    if "tool" in data and "poetry" in data["tool"]:
                        poetry_deps = data["tool"]["poetry"].get("dependencies", {})
                        # Convert poetry dict format to list (skip python version)
                        return [
                            f"{pkg}{f'=={ver}' if isinstance(ver, str) else ''}"
                            for pkg, ver in poetry_deps.items()
                            if pkg != "python"
                        ]
            except Exception as e:
                self.logger.warning(f"Failed to parse pyproject.toml dependencies: {e}")

        # Try project.toml [dependencies] section
        if "dependencies" in self._config:
            deps = self._config["dependencies"]
            if isinstance(deps, list):
                return deps
            elif isinstance(deps, dict):
                return [
                    f"{pkg}{f'=={ver}' if ver else ''}" for pkg, ver in deps.items()
                ]

        # Fall back to requirements.txt
        requirements_file = self.project_root / "requirements.txt"
        if requirements_file.exists():
            try:
                with open(requirements_file, "r") as f:
                    return [
                        line.strip()
                        for line in f
                        if line.strip() and not line.strip().startswith("#")
                    ]
            except Exception as e:
                self.logger.warning(f"Failed to read requirements.txt: {e}")

        return []

    def _parse_package_name(self, requirement: str) -> str:
        """
        Extract package name from requirement specification.

        Handles formats like:
        - boto3
        - boto3==1.26.0
        - boto3>=1.26.0
        - boto3[extra]
        - git+https://...#egg=package

        Args:
            requirement: Package requirement string

        Returns:
            Base package name
        """
        requirement = requirement.strip()

        # Handle git URLs
        if requirement.startswith("git+") or requirement.startswith("http"):
            if "#egg=" in requirement:
                return requirement.split("#egg=")[-1].split("[")[0]
            return requirement  # Return as-is if can't parse

        # Handle standard package specs
        # Remove extras: package[extra] -> package
        if "[" in requirement:
            requirement = requirement.split("[")[0]

        # Remove version specifiers: package>=1.0 -> package
        for separator in ["==", ">=", "<=", ">", "<", "~=", "!="]:
            if separator in requirement:
                requirement = requirement.split(separator)[0]

        return requirement.strip()

    def _is_package_installed(self, package_name: str) -> bool:
        """
        Check if a package is installed.

        Args:
            package_name: Name of the package to check

        Returns:
            True if package is installed, False otherwise
        """
        # Normalize package name (replace - with _)
        normalized_name = package_name.replace("-", "_")

        # Try direct import check
        spec = importlib.util.find_spec(normalized_name)
        if spec is not None:
            return True

        # Some packages have different import names than package names
        # Try the original name too
        if normalized_name != package_name:
            spec = importlib.util.find_spec(package_name)
            if spec is not None:
                return True

        return False

    def _validate_aws_connection(self) -> None:
        """
        Verify AWS credentials and region connectivity.

        Raises:
            ConfigError: If AWS connection cannot be established.
        """
        try:
            import boto3
            from botocore.exceptions import ClientError, NoCredentialsError

            # Build session based on config
            session_kwargs = {"region_name": self._aws_config.region}

            if self._aws_config.use_local_credentials:
                if self._aws_config.credentials_path:
                    # Use specific credentials file
                    os.environ["AWS_SHARED_CREDENTIALS_FILE"] = str(
                        self._aws_config.credentials_path
                    )
                # Otherwise boto3 will use default ~/.aws/credentials

            session = boto3.Session(**session_kwargs)

            # Test connection with STS get-caller-identity
            sts = session.client("sts")
            identity = sts.get_caller_identity()

            # Store identity info in config for reference
            self._set_nested("aws.account_id", identity.get("Account"))
            self._set_nested("aws.arn", identity.get("Arn"))

        except NoCredentialsError:
            raise ConfigError(
                "AWS credentials not found. Configure credentials or set "
                "use_local_credentials=true in config."
            )
        except ClientError as e:
            raise ConfigError(f"Failed to connect to AWS: {e}")
        except ImportError:
            raise ConfigError("boto3 is required but not installed")

    def _merge_config(self, new_data: Dict[str, Any]) -> None:
        """Deep merge new configuration data into existing config."""
        self._deep_merge(self._config, new_data)

    def _deep_merge(self, base: Dict, update: Dict) -> None:
        """Recursively merge update dict into base dict."""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def _set_nested(self, key: str, value: Any) -> None:
        """Set a nested configuration value using dot notation."""
        parts = key.split(".")
        current = self._config

        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]

        current[parts[-1]] = value

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value using dot notation.

        Args:
            key: Configuration key in dot notation (e.g., 'database.host')
            default: Default value if key not found

        Returns:
            Configuration value or default

        Example:
            >>> config.get('database.host', 'localhost')
        """
        if not self._loaded:
            self.load()

        parts = key.split(".")
        current = self._config

        for part in parts:
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]

        return current

    def get_required(self, key: str) -> Any:
        """
        Get required configuration value.

        Args:
            key: Configuration key in dot notation

        Returns:
            Configuration value

        Raises:
            ConfigError: If key is not found
        """
        value = self.get(key)
        if value is None:
            raise ConfigError(f"Required configuration key not found: {key}")
        return value

    @property
    def aws(self) -> AWSConfig:
        """Get AWS-specific configuration."""
        if not self._loaded:
            self.load()
        return self._aws_config

    def get_for_environment(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value for the current environment.

        Automatically appends the current environment to the key path.

        Args:
            key: Base configuration key (e.g., 'project.buckets')
            default: Default value if key not found

        Returns:
            Configuration value for current environment or default

        Example:
            >>> # With environment="dev" and project.buckets.dev="my-dev-bucket"
            >>> config.get_for_environment('project.buckets')
            'my-dev-bucket'

            >>> # Equivalent to:
            >>> config.get('project.buckets.dev')
        """
        if not self.environment:
            return default

        env_key = f"{key}.{self.environment}"
        return self.get(env_key, default)

    @property
    def environment(self) -> str | None:
        """Get current environment (dev/uat/prod)."""
        return self.get("environment")

    def to_dict(self) -> Dict[str, Any]:
        """Return full configuration as dictionary."""
        if not self._loaded:
            self.load()
        return self._config.copy()

    def __repr__(self) -> str:
        status = "loaded" if self._loaded else "not loaded"
        env = self.environment or "unknown"
        return f"Config(environment={env}, status={status}, singleton=True)"

    @classmethod
    def reset(cls) -> None:
        """
        Reset singleton instance. Primarily for testing purposes.

        Warning: Use with caution in production code.
        """
        with cls._lock:
            cls._instance = None
