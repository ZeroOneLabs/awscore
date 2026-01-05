"""
AWS Secrets Manager helper module.

Provides simple interface for retrieving secrets from AWS Secrets Manager
with automatic caching and JSON parsing support.
"""

import json
from typing import Any

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    raise ImportError(
        "boto3 is required for AWS Secrets Manager. Install with: pip install boto3"
    )

from ..core.config import Config
from ..core.logging import Logger


class SecretsManagerError(Exception):
    """Raised when secret retrieval or parsing fails."""

    pass


class SecretsManager:
    """
    AWS Secrets Manager helper for retrieving secrets.

    Automatically caches retrieved secrets as instance attributes
    to avoid repeated API calls within the same Lambda execution.

    Example:
        >>> secrets = SecretsManager()
        >>> db_creds = secrets._get('myapp/database/credentials')
        >>> # Returns dict if secret is JSON, string otherwise
        >>> print(db_creds['username'])
    """

    def __init__(self):
        """Initialize Secrets Manager client."""
        self.config = Config()
        self.logger = Logger()

        # Get region - check secretsmanager.region first, fallback to aws.region
        region = self.config.get("secretsmanager.region") or self.config.aws.region

        # Create boto3 client
        self._client = boto3.client("secretsmanager", region_name=region)

        # Cache for retrieved secrets
        self._cache: dict[str, Any] = {}

        self.logger.debug(f"SecretsManager initialized", extra={"region": region})

    def _get(self, secret_name: str, parse_json: bool = True) -> Any:
        """
        Retrieve a secret from AWS Secrets Manager.

        Secrets are cached after first retrieval to avoid redundant API calls.
        If the secret value is valid JSON, it will be automatically parsed
        into a dict (unless parse_json=False).

        Args:
            secret_name: Name or ARN of the secret to retrieve
            parse_json: Whether to parse JSON secrets into dict (default: True)

        Returns:
            Secret value as dict (if JSON) or string

        Raises:
            SecretsManagerError: If secret cannot be retrieved or parsed

        Example:
            >>> secrets = SecretsManager()
            >>>
            >>> # Get JSON secret (automatically parsed)
            >>> db_creds = secrets._get('myapp/prod/database')
            >>> print(db_creds['password'])
            >>>
            >>> # Get plain text secret
            >>> api_key = secrets._get('myapp/prod/api-key')
            >>> print(api_key)  # Returns string
            >>>
            >>> # Disable JSON parsing
            >>> raw = secrets._get('myapp/config', parse_json=False)
        """
        # Return cached value if available
        if secret_name in self._cache:
            self.logger.debug(f"Returning cached secret: {secret_name}")
            return self._cache[secret_name]

        self.logger.info(
            f"Retrieving secret from AWS Secrets Manager",
            extra={"secret_name": secret_name},
        )

        try:
            response = self._client.get_secret_value(SecretId=secret_name)

            # Get secret value (could be string or binary)
            if "SecretString" in response:
                secret_value = response["SecretString"]
            else:
                # Binary secrets (less common)
                secret_value = response["SecretBinary"].decode("utf-8")

            # Try to parse as JSON if requested
            if parse_json:
                try:
                    secret_value = json.loads(secret_value)
                    self.logger.debug(f"Parsed secret as JSON: {secret_name}")
                except json.JSONDecodeError:
                    # Not JSON, return as string
                    self.logger.debug(f"Secret is plain text: {secret_name}")

            # Cache the value
            self._cache[secret_name] = secret_value

            self.logger.info(f"Successfully retrieved secret: {secret_name}")
            return secret_value

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_msg = e.response["Error"]["Message"]

            # Handle specific error cases
            if error_code == "ResourceNotFoundException":
                error = f"Secret not found: {secret_name}"
            elif error_code == "InvalidRequestException":
                error = f"Invalid request for secret: {secret_name}"
            elif error_code == "InvalidParameterException":
                error = f"Invalid parameter for secret: {secret_name}"
            elif error_code == "DecryptionFailure":
                error = f"Cannot decrypt secret: {secret_name}"
            elif error_code == "AccessDeniedException":
                error = f"Access denied to secret: {secret_name}"
            else:
                error = f"Failed to retrieve secret {secret_name}: {error_msg}"

            self.logger.error(error, exc_info=True)
            raise SecretsManagerError(error) from e

        except Exception as e:
            error = f"Unexpected error retrieving secret {secret_name}: {str(e)}"
            self.logger.error(error, exc_info=True)
            raise SecretsManagerError(error) from e

    def get_environment_secret(
        self, secret_base_name: str, parse_json: bool = True
    ) -> Any:
        """
        Retrieve a secret using environment-aware naming.

        Automatically appends the current environment to the secret name.
        Useful for secrets that differ by environment (dev/uat/prod).

        Args:
            secret_base_name: Base name of secret (without environment suffix)
            parse_json: Whether to parse JSON secrets into dict

        Returns:
            Secret value

        Example:
            >>> # In dev environment
            >>> secrets = SecretsManager()
            >>> db = secrets.get_environment_secret('myapp/database')
            >>> # Retrieves 'myapp/database/dev'
        """
        environment = self.config.environment
        if not environment:
            raise SecretsManagerError(
                "Cannot use get_environment_secret: environment not detected. "
                "Use _get() with full secret name instead."
            )

        # Build environment-specific secret name
        secret_name = f"{secret_base_name}/{environment}"
        return self._get(secret_name, parse_json)

    def get_secret(self, entity_type: str, parse_json: bool = True) -> Any:
        """
        Retrieve a secret by entity type using config-based naming.

        Looks up the secret name from project.toml configuration:
        [secretsmanager.names.{entity_type}]
        dev = "SECRET-NAME-DEV"
        uat = "SECRET-NAME-UAT"
        prod = "SECRET-NAME-PROD"

        Args:
            entity_type: Type of entity (e.g., 'postgres', 'api', 'redis')
            parse_json: Whether to parse JSON secrets into dict

        Returns:
            Secret value

        Raises:
            SecretsManagerError: If entity type not configured or environment not detected

        Example:
            >>> # In project.toml:
            >>> # [secretsmanager.names.postgres]
            >>> # dev = "PROJ-DEV-DB"
            >>> # prod = "PROJ-PROD-DB"
            >>>
            >>> secrets = SecretsManager()
            >>> db_creds = secrets.get_secret('postgres')
            >>> # In dev: retrieves "PROJ-DEV-DB"
            >>> # In prod: retrieves "PROJ-PROD-DB"
        """
        environment = self.config.environment
        if not environment:
            raise SecretsManagerError(
                f"Cannot retrieve secret for '{entity_type}': environment not detected. "
                "Ensure Lambda name follows <PROJECT>-<ENV>-<function> format."
            )

        # Look up secret name from config
        config_key = f"secretsmanager.names.{entity_type}.{environment}"
        secret_name = self.config.get(config_key)

        if not secret_name:
            raise SecretsManagerError(
                f"Secret name not configured for entity '{entity_type}' in environment '{environment}'. "
                f'Add configuration: [secretsmanager.names.{entity_type}] {environment} = "SECRET-NAME"'
            )

        self.logger.debug(
            f"Resolved secret name from config",
            extra={
                "entity_type": entity_type,
                "environment": environment,
                "secret_name": secret_name,
            },
        )

        return self._get(secret_name, parse_json)

    def clear_cache(self, secret_name: str | None = None) -> None:
        """
        Clear cached secrets.

        Args:
            secret_name: Specific secret to clear, or None to clear all

        Example:
            >>> secrets.clear_cache('myapp/database')  # Clear one
            >>> secrets.clear_cache()  # Clear all
        """
        if secret_name:
            self._cache.pop(secret_name, None)
            self.logger.debug(f"Cleared cache for secret: {secret_name}")
        else:
            self._cache.clear()
            self.logger.debug("Cleared all cached secrets")

    def __repr__(self) -> str:
        return f"SecretsManager(region={self.config.aws.region}, cached_secrets={len(self._cache)})"
