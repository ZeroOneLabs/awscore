# awscore/aws/config/aws_config.py
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional
import boto3
import toml
from awscore import AutoLogger

DEFAULT_CONFIG_PATH = Path.cwd() / "config.toml"
HOME_AWS = Path.home() / ".aws"

class AwsConfig(AutoLogger):
    """
    Constructs AWS configuration from multiple sources.
    Priority: explicit args > config.toml > ~/.aws
    """

    def __init__(
        self,
        region: Optional[str] = None,
        profile: Optional[str] = None,
        verify_ssl: Optional[bool] = None,
        config_path: Optional[Path] = None,
    ) -> None:
        """
        Initialize AWS config.

        Args:
            region: AWS region (e.g. "us-east-1").
            profile: AWS profile name.
            verify_ssl: Verify SSL certificates.
            config_path: Path to config.toml.
        """
        super().__init__(region=region or "unknown")
        self._load_toml(config_path or DEFAULT_CONFIG_PATH)
        self.region = region or self._toml.get("region") or self._aws_default("region")
        self.profile = profile or self._toml.get("profile")
        self.verify_ssl = verify_ssl if verify_ssl is not None else self._toml.get("verify_ssl", True)

        self.session = boto3.Session(
            region_name=self.region,
            profile_name=self.profile,
        )
        self.log.info("AWS session created", extra={"profile": self.profile})

    def _load_toml(self, path: Path) -> None:
        """Load config.toml if exists."""
        self._toml = {}
        if path.is_file():
            self._toml = toml.load(path).get("aws", {})
            self.log.debug("Loaded config.toml", extra={"path": str(path)})

    def _aws_default(self, key: str) -> Optional[str]:
        """Fallback to boto3 default logic."""
        return None