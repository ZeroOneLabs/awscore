"""
AWS Lambda helper module.

Provides methods for managing Lambda functions:
- Upload/download Lambda function code
- Get/update Lambda configuration
- Manage environment variables, layers, and runtime settings
"""

import zipfile
import io
import json
from pathlib import Path
from typing import Any

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    raise ImportError(
        "boto3 is required for AWS Lambda operations. Install with: pip install boto3"
    )

from ..core.config import Config
from ..core.logging import Logger


class LambdaError(Exception):
    """Raised when Lambda operations fail."""

    pass


class LambdaHelper:
    """
    AWS Lambda helper for function management.

    Provides interface for deploying code, managing configuration,
    and retrieving Lambda function information.

    Example:
        >>> lmbda = LambdaHelper()
        >>>
        >>> # Upload code from directory
        >>> lmbda.upload_code('my-function', './src')
        >>>
        >>> # Update environment variables
        >>> lmbda.update_config('my-function', environment={'LOG_LEVEL': 'DEBUG'})
        >>>
        >>> # Get function info
        >>> config = lmbda.get_config('my-function')
    """

    def __init__(self):
        """Initialize Lambda client."""
        self.config = Config()
        self.logger = Logger()

        # Get region from config
        region = self.config.get("lambda.region") or self.config.aws.region

        # Create boto3 client
        self._client = boto3.client("lambda", region_name=region)

        self.logger.debug("LambdaHelper initialized", extra={"region": region})

    def upload_code(
        self, function_name: str, source_path: str | Path, publish: bool = False
    ) -> dict[str, Any]:
        """
        Upload Lambda function code from a directory or zip file.

        If source_path is a directory, it will be zipped automatically.
        If it's a .zip file, it will be uploaded directly.

        Args:
            function_name: Name of Lambda function
            source_path: Path to code directory or zip file
            publish: Whether to publish a new version (default: False)

        Returns:
            Response dict with function metadata

        Raises:
            LambdaError: If upload fails

        Example:
            >>> lmbda = LambdaHelper()
            >>>
            >>> # Upload from directory (auto-zips)
            >>> lmbda.upload_code('my-function', './src')
            >>>
            >>> # Upload existing zip
            >>> lmbda.upload_code('my-function', './deployment.zip', publish=True)
        """
        source_path = Path(source_path)

        self.logger.info(
            f"Uploading code to Lambda function",
            extra={"function_name": function_name, "source": str(source_path)},
        )

        try:
            # Prepare zip file
            if source_path.is_dir():
                self.logger.debug(f"Creating zip from directory: {source_path}")
                zip_data = self._zip_directory(source_path)
            elif source_path.suffix == ".zip":
                self.logger.debug(f"Reading zip file: {source_path}")
                with open(source_path, "rb") as f:
                    zip_data = f.read()
            else:
                raise LambdaError(
                    f"Source must be a directory or .zip file: {source_path}"
                )

            # Upload code
            response = self._client.update_function_code(
                FunctionName=function_name, ZipFile=zip_data, Publish=publish
            )

            result = {
                "FunctionName": response["FunctionName"],
                "FunctionArn": response["FunctionArn"],
                "Version": response["Version"],
                "CodeSize": response["CodeSize"],
                "LastModified": response["LastModified"],
            }

            self.logger.info(
                f"Successfully uploaded code to Lambda",
                extra={
                    "function_name": function_name,
                    "version": result["Version"],
                    "code_size": result["CodeSize"],
                },
            )

            return result

        except ClientError as e:
            error_code = e.response["Error"]["Code"]

            if error_code == "ResourceNotFoundException":
                error = f"Lambda function not found: {function_name}"
            elif error_code == "InvalidParameterValueException":
                error = f"Invalid parameters for Lambda function: {function_name}"
            elif error_code == "RequestEntityTooLargeException":
                error = f"Code package too large for Lambda function: {function_name}"
            else:
                error = f"Failed to upload code to {function_name}: {e.response['Error']['Message']}"

            self.logger.exception(error)
            raise LambdaError(error) from e

        except Exception as e:
            error = f"Unexpected error uploading code to {function_name}: {str(e)}"
            self.logger.exception(error)
            raise LambdaError(error) from e

    def download_code(
        self, function_name: str, output_path: str | Path, version: str | None = None
    ) -> Path:
        """
        Download Lambda function code to a zip file.

        Args:
            function_name: Name of Lambda function
            output_path: Path where zip file should be saved
            version: Specific version to download (default: $LATEST)

        Returns:
            Path to downloaded zip file

        Raises:
            LambdaError: If download fails

        Example:
            >>> lmbda = LambdaHelper()
            >>> lmbda.download_code('my-function', './backup.zip')
            >>> lmbda.download_code('my-function', './v5.zip', version='5')
        """
        output_path = Path(output_path)
        qualifier = version or "$LATEST"

        self.logger.info(
            f"Downloading code from Lambda function",
            extra={"function_name": function_name, "version": qualifier},
        )

        try:
            # Get function code location
            response = self._client.get_function(
                FunctionName=function_name, Qualifier=qualifier
            )

            code_location = response["Code"]["Location"]

            # Download from presigned URL
            import urllib.request

            with urllib.request.urlopen(code_location) as response_data:
                zip_data = response_data.read()

            # Save to file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(zip_data)

            self.logger.info(
                f"Successfully downloaded code from Lambda",
                extra={
                    "function_name": function_name,
                    "output": str(output_path),
                    "size": len(zip_data),
                },
            )

            return output_path

        except ClientError as e:
            error = f"Failed to download code from {function_name}: {e.response['Error']['Message']}"
            self.logger.exception(error)
            raise LambdaError(error) from e

        except Exception as e:
            error = f"Unexpected error downloading code from {function_name}: {str(e)}"
            self.logger.exception(error)
            raise LambdaError(error) from e

    def get_config(self, function_name: str) -> dict[str, Any]:
        """
        Get Lambda function configuration.

        Args:
            function_name: Name of Lambda function

        Returns:
            Dict with function configuration including:
            - Runtime, Memory, Timeout
            - Environment variables
            - Layers
            - Role, Handler
            - VPC configuration

        Raises:
            LambdaError: If function not found or retrieval fails

        Example:
            >>> lmbda = LambdaHelper()
            >>> config = lmbda.get_config('my-function')
            >>> print(config['Runtime'])
            >>> print(config['Environment']['Variables'])
        """
        self.logger.info(
            f"Getting Lambda function configuration",
            extra={"function_name": function_name},
        )

        try:
            response = self._client.get_function_configuration(
                FunctionName=function_name
            )

            config = {
                "FunctionName": response["FunctionName"],
                "FunctionArn": response["FunctionArn"],
                "Runtime": response["Runtime"],
                "Role": response["Role"],
                "Handler": response["Handler"],
                "CodeSize": response["CodeSize"],
                "Description": response.get("Description", ""),
                "Timeout": response["Timeout"],
                "MemorySize": response["MemorySize"],
                "LastModified": response["LastModified"],
                "Version": response["Version"],
                "Environment": response.get("Environment", {}),
                "Layers": response.get("Layers", []),
                "VpcConfig": response.get("VpcConfig", {}),
                "Architectures": response.get("Architectures", []),
                "EphemeralStorage": response.get("EphemeralStorage", {}),
            }

            self.logger.debug(
                f"Retrieved Lambda configuration",
                extra={
                    "function_name": function_name,
                    "runtime": config["Runtime"],
                    "memory": config["MemorySize"],
                },
            )

            return config

        except ClientError as e:
            error_code = e.response["Error"]["Code"]

            if error_code == "ResourceNotFoundException":
                error = f"Lambda function not found: {function_name}"
            else:
                error = f"Failed to get config for {function_name}: {e.response['Error']['Message']}"

            self.logger.exception(error)
            raise LambdaError(error) from e

        except Exception as e:
            error = f"Unexpected error getting config for {function_name}: {str(e)}"
            self.logger.exception(error)
            raise LambdaError(error) from e

    def update_config(
        self,
        function_name: str,
        runtime: str | None = None,
        handler: str | None = None,
        memory_size: int | None = None,
        timeout: int | None = None,
        environment: dict[str, str] | None = None,
        layers: list[str] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """
        Update Lambda function configuration.

        Only provided parameters will be updated; others remain unchanged.

        Args:
            function_name: Name of Lambda function
            runtime: Runtime (e.g., 'python3.12')
            handler: Handler function (e.g., 'lambda_function.lambda_handler')
            memory_size: Memory in MB (128-10240)
            timeout: Timeout in seconds (1-900)
            environment: Environment variables dict
            layers: List of layer ARNs
            description: Function description

        Returns:
            Updated configuration dict

        Raises:
            LambdaError: If update fails

        Example:
            >>> lmbda = LambdaHelper()
            >>>
            >>> # Update environment variables
            >>> lmbda.update_config('my-function',
            ...     environment={'LOG_LEVEL': 'DEBUG', 'ENV': 'dev'})
            >>>
            >>> # Update memory and timeout
            >>> lmbda.update_config('my-function',
            ...     memory_size=512, timeout=60)
            >>>
            >>> # Update layers
            >>> lmbda.update_config('my-function',
            ...     layers=['arn:aws:lambda:us-east-1:123456:layer:my-layer:1'])
        """
        self.logger.info(
            f"Updating Lambda function configuration",
            extra={"function_name": function_name},
        )

        try:
            # Build update parameters
            params = {"FunctionName": function_name}

            if runtime is not None:
                params["Runtime"] = runtime
            if handler is not None:
                params["Handler"] = handler
            if memory_size is not None:
                params["MemorySize"] = memory_size
            if timeout is not None:
                params["Timeout"] = timeout
            if environment is not None:
                params["Environment"] = {"Variables": environment}
            if layers is not None:
                params["Layers"] = layers
            if description is not None:
                params["Description"] = description

            # Update configuration
            response = self._client.update_function_configuration(**params)

            result = {
                "FunctionName": response["FunctionName"],
                "FunctionArn": response["FunctionArn"],
                "Runtime": response["Runtime"],
                "Handler": response["Handler"],
                "MemorySize": response["MemorySize"],
                "Timeout": response["Timeout"],
                "LastModified": response["LastModified"],
            }

            self.logger.info(
                f"Successfully updated Lambda configuration",
                extra={"function_name": function_name},
            )

            return result

        except ClientError as e:
            error = f"Failed to update config for {function_name}: {e.response['Error']['Message']}"
            self.logger.exception(error)
            raise LambdaError(error) from e

        except Exception as e:
            error = f"Unexpected error updating config for {function_name}: {str(e)}"
            self.logger.exception(error)
            raise LambdaError(error) from e

    def add_environment_variable(
        self, function_name: str, key: str, value: str
    ) -> dict[str, Any]:
        """
        Add or update a single environment variable without affecting others.

        Args:
            function_name: Name of Lambda function
            key: Environment variable name
            value: Environment variable value

        Returns:
            Updated configuration dict

        Example:
            >>> lmbda = LambdaHelper()
            >>> lmbda.add_environment_variable('my-function', 'DEBUG', 'true')
        """
        # Get current environment variables
        current_config = self.get_config(function_name)
        env_vars = current_config.get("Environment", {}).get("Variables", {})

        # Add/update the variable
        env_vars[key] = value

        # Update function
        return self.update_config(function_name, environment=env_vars)

    def remove_environment_variable(
        self, function_name: str, key: str
    ) -> dict[str, Any]:
        """
        Remove a single environment variable.

        Args:
            function_name: Name of Lambda function
            key: Environment variable name to remove

        Returns:
            Updated configuration dict

        Example:
            >>> lmbda = LambdaHelper()
            >>> lmbda.remove_environment_variable('my-function', 'OLD_VAR')
        """
        # Get current environment variables
        current_config = self.get_config(function_name)
        env_vars = current_config.get("Environment", {}).get("Variables", {})

        # Remove the variable if it exists
        env_vars.pop(key, None)

        # Update function
        return self.update_config(function_name, environment=env_vars)

    def _zip_directory(self, directory: Path) -> bytes:
        """
        Create a zip file from a directory.

        Args:
            directory: Path to directory to zip

        Returns:
            Zip file contents as bytes
        """
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file_path in directory.rglob("*"):
                if file_path.is_file():
                    # Get relative path for archive
                    arcname = file_path.relative_to(directory)
                    zip_file.write(file_path, arcname)
                    self.logger.debug(f"Added to zip: {arcname}")

        zip_buffer.seek(0)
        return zip_buffer.read()

    def __repr__(self) -> str:
        return f"LambdaHelper(region={self.config.aws.region})"
