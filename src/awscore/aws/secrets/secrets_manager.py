# ***REMOVED***/aws/secrets/secrets_manager.py
from __future__ import annotations
from typing import Dict, Any
import boto3
import json
from ***REMOVED*** import AutoLogger
from ..config.aws_config import AwsConfig

class SecretsManager(AutoLogger):
    """
    Retrieve secrets from AWS Secrets Manager.
    """

    def __init__(self, config: AwsConfig):
        """
        Initialize Secrets Manager client.

        Args:
            config: AwsConfig instance.
        """
        super().__init__(region=config.region)
        self.client = config.session.client("secretsmanager")

    def get_secret(self, secret_name: str) -> Dict[str, Any]:
        """
        Retrieve secret value.

        Args:
            secret_name: Full ARN or name of the secret.

        Returns:
            Dictionary of secret key-values (if JSON) or {"secret": value}.

        Raises:
            ClientError: If secret not found or access denied.
        """
        self.log.info("Fetching secret", extra={"secret_name": secret_name})
        response = self.client.get_secret_value(SecretId=secret_name)
        secret_string = response["SecretString"]

        try:
            return json.loads(secret_string)
        except json.JSONDecodeError:
            return {"secret": secret_string}