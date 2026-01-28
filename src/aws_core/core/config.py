"""
Core configuration module for Lambda application framework.

Handles loading configuration from pyproject.toml and environment variables.
"""

import os
import tomllib
import threading
from pathlib import Path
from typing import Any, Dict
import importlib.util

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    boto3 = None


class ConfigError(Exception):
    """Raised when configuration loading or validation fails."""

    pass


class DependencyError(Exception):
    """Raised when required dependencies are not installed."""

    pass


class AWSConfig:
    """AWS-specific configuration settings."""

    region: str = "us-east-1"
    use_local_credentials: bool = False
    credentials_path: Path | None = None
    lambda_name: str | None = None
    environment: str | None = None
    project: str | None = None
    function: str | None = None

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

        if self.credentials_path and isinstance(self.credentials_path, str):
            self.credentials_path = Path(self.credentials_path).expanduser()


class Config:
    """
    Central configuration management for Lambda applications (Singleton).

    Loads configuration from:
    - pyproject.toml (primary configuration source)
    - Environment variables (override config values)
    - AWS Lambda context (runtime environment detection)

    Singleton pattern ensures configuration is loaded once per container lifecycle.

    Example:
        >>> config = Config()  # Returns same instance everywhere
        >>> config.load()
        >>> db_host = config.get('database.host')
        >>> aws_region = config.aws.region
    """

    _instance: "Config" | None = None
    _lock = None

    def __new__(cls, project_root: Path | None = None):
        """
        Create or return existing singleton instance.

        Thread-safe implementation for concurrent Lambda invocations.
        """
        if cls._lock is None:
            cls._lock = threading.Lock()

        if cls._instance is None:
            with cls._lock:
                # Double-check locking pattern
                if cls._instance is None:
                    instance = super().__new__(cls)
                    # Initialize all attributes here (only runs once)
                    instance.project_root = Path(project_root or os.getcwd())
                    instance._config: Dict[str, Any] = {}
                    instance._aws_config: AWSConfig | None = None
                    instance._loaded = False
                    instance._logger = (
                        None  # Lazy-load logger to avoid circular dependency
                    )
                    cls._instance = instance

        return cls._instance

    def __init__(self, project_root: Path | None = None):
        """
        Initialize configuration loader (no-op after first instantiation).

        Args:
            project_root: Root directory of the project (only used on first instantiation).
        """
        # __init__ is called every time, but __new__ ensures singleton
        # All initialization happens in __new__, so this is effectively a no-op
        pass

    @property
    def logger(self):
        """Lazy-load logger to avoid circular import issues."""
        if self._logger is None:
            # Import here to avoid circular dependency
            from .logging import Logger

            self._logger = Logger()
        return self._logger

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
        self._load_pyproject_toml()
        self._detect_lambda_environment()
        self._load_env_vars()
        self._load_project_env_vars()
        self._validate_dependencies()
        self._initialize_aws_config()
        self._validate_aws_connection()

        self._loaded = True
        return self

    def _load_pyproject_toml(self) -> None:
        """Load pyproject.toml for all configuration."""
        pyproject_file = self.project_root / "pyproject.toml"
        if not pyproject_file.exists():
            raise ConfigError(f"Required pyproject.toml not found at {pyproject_file}")

        try:
            with open(pyproject_file, "rb") as f:
                data = tomllib.load(f)
                self._merge_config(data)

        except Exception as e:
            raise ConfigError(f"Failed to load pyproject.toml: {e}")

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

    def _load_env_vars(self) -> None:
        """Load environment variables with APP_ prefix into config."""
        prefix = "APP_"
        for key, value in os.environ.items():
            if key.startswith(prefix):
                # Convert APP_DATABASE_HOST to database.host
                config_key = key[len(prefix) :].lower().replace("_", ".")
                self._set_nested(config_key, value)

    def _load_project_env_vars(self) -> None:
        """
        Load environment variables from [project.env.vars] into os.environ.

        Supports two formats:

        Format 1 (environment-first):
        [project.env.vars.dev]
        AWS_PROFILE = "profile-dev"
        AWS_BUCKET = "bucket-dev"

        Format 2 (variable-first):
        [project.env.vars.AWS_PROFILE]
        dev = "profile-dev"
        uat = "profile-uat"

        Format 3 (direct values):
        [project.env.vars]
        STATIC_VAR = "value"
        """
        env_vars_config = self._config.get("project", {}).get("env", {}).get("vars", {})
        if not env_vars_config:
            return

        current_environment = self._config.get("environment") or self._config.get(
            "aws", {}
        ).get("environment")

        # Check if using environment-first format (dev/uat/prod as keys)
        if current_environment and current_environment in env_vars_config:
            # Format 1: Environment-first structure
            env_block = env_vars_config[current_environment]
            if isinstance(env_block, dict):
                for key, value in env_block.items():
                    if key not in os.environ:
                        os.environ[key] = str(value)
                        self.logger.debug(
                            f"Loaded environment variable: {key}={value}",
                            extra={"environment": current_environment},
                        )
            return

        # Format 2 & 3: Variable-first or direct values
        for key, value in env_vars_config.items():
            # Skip environment blocks (they would have been handled above)
            if key in ["dev", "uat", "prod"] and isinstance(value, dict):
                continue

            # Skip if already in environment (shell env vars take precedence)
            if key in os.environ:
                continue

            # Check if value is environment-specific (dict with dev/uat/prod keys)
            if isinstance(value, dict):
                # Format 2: Variable-first with environment sub-keys
                if current_environment and current_environment in value:
                    env_value = str(value[current_environment])
                    os.environ[key] = env_value
                    self.logger.debug(
                        f"Loaded environment variable: {key}={env_value}",
                        extra={"environment": current_environment},
                    )
                else:
                    # Environment not detected or not in config
                    self.logger.warning(
                        f"Could not load environment-specific variable '{key}' - environment not detected or not configured"
                    )
            else:
                # Format 3: Direct value (not environment-specific)
                os.environ[key] = str(value)
                self.logger.debug(f"Loaded environment variable: {key}")

    def _initialize_aws_config(self) -> None:
        """Initialize AWS configuration from loaded config data."""
        aws_data = self._config.get("aws", {})

        self._aws_config = AWSConfig(
            region=aws_data.get("region", os.environ.get("AWS_REGION", "us-east-1")),
            use_local_credentials=aws_data.get("use_local_credentials", False),
            credentials_path=aws_data.get("credentials_path"),
            lambda_name=aws_data.get("lambda_name"),
            environment=aws_data.get("environment"),
            project=aws_data.get("project"),
            function=aws_data.get("function"),
        )

    def _validate_aws_connection(self) -> None:
        """
        Verify AWS credentials and region connectivity.

        Raises:
            ConfigError: If AWS connection cannot be established.
        """
        if boto3 is None:
            # boto3 not installed - skip validation
            return

        try:
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

    def _validate_dependencies(self) -> None:
        """
        Validate that all required dependencies are installed.

        Checks for dependencies in [project.dependencies] from pyproject.toml.

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
        Load required dependencies from [project.dependencies].

        Returns:
            List of required package specifications.
        """
        # Check PEP 621 format in pyproject.toml
        if "project" in self._config and "dependencies" in self._config["project"]:
            return self._config["project"]["dependencies"]

        return []

    def _parse_package_name(self, requirement: str) -> str:
        """
        Extract package name from requirement specification.

        Handles formats like:
        - boto3
        - boto3==1.26.0
        - boto3>=1.26.0
        - boto3[extra]

        Args:
            requirement: Package requirement string

        Returns:
            Base package name
        """
        requirement = requirement.strip()

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
        """
        if not self.environment:
            return default

        env_key = f"{key}.{self.environment}"
        return self.get(env_key, default)

    @property
    def aws(self) -> AWSConfig:
        """Get AWS-specific configuration."""
        if not self._loaded:
            self.load()
        return self._aws_config

    @property
    def environment(self) -> str | None:
        """Get current environment (dev/uat/prod)."""
        return self.get("environment")

    def to_dict(self) -> Dict[str, Any]:
        """Return full configuration as dictionary."""
        if not self._loaded:
            self.load()
        return self._config.copy()

    @classmethod
    def reset(cls) -> None:
        """Reset singleton instance. Primarily for testing."""
        with cls._lock:
            cls._instance = None

    def __repr__(self) -> str:
        status = "loaded" if self._loaded else "not loaded"
        env = self.environment or "unknown"
        return f"Config(environment={env}, status={status}, singleton=True)"
