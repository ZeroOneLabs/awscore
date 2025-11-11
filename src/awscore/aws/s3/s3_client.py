# awscore/aws/s3/s3_client.py
from __future__ import annotations
from typing import IO, Any
import boto3
from botocore.exceptions import ClientError
from awscore import AutoLogger
from ..config.aws_config import AwsConfig

class S3(AutoLogger):
    """
    High-level S3 client with logging.
    """

    def __init__(self, config: AwsConfig):
        """
        Initialize S3 client.

        Args:
            config: AwsConfig instance.
        """
        super().__init__(region=config.region)
        self.client = config.session.client("s3")
        self.config = config

    def upload_fileobj(
        self,
        file_obj: IO[bytes],
        bucket: str,
        key: str,
        **extra_args: Any,
    ) -> None:
        """
        Upload file-like object to S3.

        Args:
            file_obj: File-like object (e.g. BytesIO).
            bucket: S3 bucket name.
            key: S3 object key.
            **extra_args: Extra arguments passed to boto3.

        Example:
            >>> with open("report.csv", "rb") as f:
            ...     s3.upload_fileobj(f, "eia-raw", "reports/2025/report.csv")
        """
        self.log.info("Uploading object", extra={"bucket": bucket, "key": key})
        self.client.upload_fileobj(file_obj, bucket, key, ExtraArgs=extra_args)

    def download_fileobj(
        self,
        bucket: str,
        key: str,
        file_obj: IO[bytes],
    ) -> None:
        """
        Download S3 object to file-like object.

        Args:
            bucket: S3 bucket.
            key: S3 object key.
            file_obj: Writable file-like object.
        """
        self.log.info("Downloading object", extra={"bucket": bucket, "key": key})
        self.client.download_fileobj(bucket, key, file_obj)

    def delete_object(self, bucket: str, key: str) -> None:
        """
        Delete S3 object.

        Args:
            bucket: Bucket name.
            key: Object key.
        """
        self.log.info("Deleting object", extra={"bucket": bucket, "key": key})
        self.client.delete_object(Bucket=bucket, Key=key)