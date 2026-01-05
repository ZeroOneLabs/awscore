"""
AWS S3 helper module.

Provides methods for S3 operations (get, put, copy, move, delete, head)
and a FileFinder class for searching S3 paths with pattern matching.
"""

import re
from typing import Any, BinaryIO
from pathlib import Path
from io import BytesIO

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    raise ImportError(
        "boto3 is required for AWS S3 operations. Install with: pip install boto3"
    )

from ..core.config import Config
from ..core.logging import Logger


class S3Error(Exception):
    """Raised when S3 operations fail."""

    pass


class S3Helper:
    """
    AWS S3 helper for file operations.

    Provides simple interface for common S3 operations with automatic
    error handling and logging.

    Example:
        >>> s3 = S3Helper()
        >>>
        >>> # Download file
        >>> data = s3.get('my-bucket', 'path/to/file.txt')
        >>>
        >>> # Upload file
        >>> s3.put('my-bucket', 'path/to/file.txt', b'content')
        >>>
        >>> # Find files
        >>> finder = s3.FileFinder('my-bucket', 'data/2024/')
        >>> files = finder.find('*.csv')
    """

    def __init__(self):
        """Initialize S3 client."""
        self.config = Config()
        self.logger = Logger()

        # Get region from config
        region = self.config.get("s3.region") or self.config.aws.region

        # Create boto3 client
        self._client = boto3.client("s3", region_name=region)

        self.logger.debug("S3Helper initialized", extra={"region": region})

    def get(self, bucket: str, key: str, as_bytes: bool = False) -> bytes | str:
        """
        Download an object from S3.

        Args:
            bucket: S3 bucket name
            key: S3 object key (path)
            as_bytes: Return as bytes (True) or string (False, default)

        Returns:
            Object content as string (default) or bytes

        Raises:
            S3Error: If object cannot be retrieved

        Example:
            >>> s3 = S3Helper()
            >>> content = s3.get('my-bucket', 'data/file.txt')  # Returns string
            >>> binary = s3.get('my-bucket', 'data/image.png', as_bytes=True)
        """
        self.logger.info(
            f"Getting object from S3", extra={"bucket": bucket, "key": key}
        )

        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            content = response["Body"].read()

            if as_bytes:
                return content
            else:
                return content.decode("utf-8")

        except ClientError as e:
            error_code = e.response["Error"]["Code"]

            if error_code == "NoSuchKey":
                error = f"Object not found: s3://{bucket}/{key}"
            elif error_code == "NoSuchBucket":
                error = f"Bucket not found: {bucket}"
            elif error_code == "AccessDenied":
                error = f"Access denied to s3://{bucket}/{key}"
            else:
                error = f"Failed to get s3://{bucket}/{key}: {e.response['Error']['Message']}"

            self.logger.exception(error)
            raise S3Error(error) from e

        except Exception as e:
            error = f"Unexpected error getting s3://{bucket}/{key}: {str(e)}"
            self.logger.exception(error)
            raise S3Error(error) from e

    def put(
        self,
        bucket: str,
        key: str,
        data: bytes | str | BinaryIO,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Upload an object to S3.

        Args:
            bucket: S3 bucket name
            key: S3 object key (path)
            data: Content to upload (bytes, string, or file-like object)
            content_type: MIME type (e.g., 'text/csv', 'application/json')
            metadata: Custom metadata dict to attach to object

        Returns:
            Response dict with ETag and VersionId (if versioning enabled)

        Raises:
            S3Error: If upload fails

        Example:
            >>> s3 = S3Helper()
            >>> s3.put('my-bucket', 'data/file.txt', b'content')
            >>> s3.put('my-bucket', 'data.json', '{"key": "value"}',
            ...        content_type='application/json',
            ...        metadata={'source': 'lambda'})
        """
        self.logger.info(f"Putting object to S3", extra={"bucket": bucket, "key": key})

        try:
            # Convert string to bytes if necessary
            if isinstance(data, str):
                data = data.encode("utf-8")

            # Build put_object parameters
            params = {"Bucket": bucket, "Key": key, "Body": data}

            if content_type:
                params["ContentType"] = content_type

            if metadata:
                params["Metadata"] = metadata

            response = self._client.put_object(**params)

            result = {
                "ETag": response.get("ETag", "").strip('"'),
                "VersionId": response.get("VersionId"),
            }

            self.logger.info(
                f"Successfully put object to S3",
                extra={"bucket": bucket, "key": key, "etag": result["ETag"]},
            )

            return result

        except ClientError as e:
            error = (
                f"Failed to put s3://{bucket}/{key}: {e.response['Error']['Message']}"
            )
            self.logger.exception(error)
            raise S3Error(error) from e

        except Exception as e:
            error = f"Unexpected error putting s3://{bucket}/{key}: {str(e)}"
            self.logger.exception(error)
            raise S3Error(error) from e

    def copy(
        self,
        source_bucket: str,
        source_key: str,
        dest_bucket: str,
        dest_key: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Copy an object within S3.

        Args:
            source_bucket: Source bucket name
            source_key: Source object key
            dest_bucket: Destination bucket name
            dest_key: Destination object key
            metadata: New metadata (if None, copies existing metadata)

        Returns:
            Response dict with ETag and VersionId

        Raises:
            S3Error: If copy fails

        Example:
            >>> s3 = S3Helper()
            >>> s3.copy('bucket-a', 'file.txt', 'bucket-b', 'backup/file.txt')
        """
        source = f"{source_bucket}/{source_key}"
        self.logger.info(
            f"Copying S3 object",
            extra={
                "source": f"s3://{source}",
                "destination": f"s3://{dest_bucket}/{dest_key}",
            },
        )

        try:
            params = {"Bucket": dest_bucket, "Key": dest_key, "CopySource": source}

            if metadata:
                params["Metadata"] = metadata
                params["MetadataDirective"] = "REPLACE"
            else:
                params["MetadataDirective"] = "COPY"

            response = self._client.copy_object(**params)

            result = {
                "ETag": response.get("CopyObjectResult", {}).get("ETag", "").strip('"'),
                "VersionId": response.get("VersionId"),
            }

            self.logger.info(f"Successfully copied S3 object")
            return result

        except ClientError as e:
            error = f"Failed to copy s3://{source} to s3://{dest_bucket}/{dest_key}: {e.response['Error']['Message']}"
            self.logger.exception(error)
            raise S3Error(error) from e

        except Exception as e:
            error = f"Unexpected error copying S3 object: {str(e)}"
            self.logger.exception(error)
            raise S3Error(error) from e

    def move(
        self, source_bucket: str, source_key: str, dest_bucket: str, dest_key: str
    ) -> dict[str, Any]:
        """
        Move an object within S3 (copy then delete source).

        Args:
            source_bucket: Source bucket name
            source_key: Source object key
            dest_bucket: Destination bucket name
            dest_key: Destination object key

        Returns:
            Response dict with ETag and VersionId from copy operation

        Raises:
            S3Error: If move fails

        Example:
            >>> s3 = S3Helper()
            >>> s3.move('bucket', 'temp/file.txt', 'bucket', 'archive/file.txt')
        """
        self.logger.info(
            f"Moving S3 object",
            extra={
                "source": f"s3://{source_bucket}/{source_key}",
                "destination": f"s3://{dest_bucket}/{dest_key}",
            },
        )

        try:
            # Copy to destination
            result = self.copy(source_bucket, source_key, dest_bucket, dest_key)

            # Delete source
            self.delete(source_bucket, source_key)

            self.logger.info(f"Successfully moved S3 object")
            return result

        except S3Error:
            # Re-raise S3Error as-is (already logged)
            raise
        except Exception as e:
            error = f"Unexpected error moving S3 object: {str(e)}"
            self.logger.exception(error)
            raise S3Error(error) from e

    def delete(self, bucket: str, key: str) -> bool:
        """
        Delete an object from S3.

        Args:
            bucket: S3 bucket name
            key: S3 object key

        Returns:
            True if deleted successfully

        Raises:
            S3Error: If delete fails

        Example:
            >>> s3 = S3Helper()
            >>> s3.delete('my-bucket', 'old-file.txt')
        """
        self.logger.info(f"Deleting S3 object", extra={"bucket": bucket, "key": key})

        try:
            self._client.delete_object(Bucket=bucket, Key=key)
            self.logger.info(f"Successfully deleted s3://{bucket}/{key}")
            return True

        except ClientError as e:
            error = f"Failed to delete s3://{bucket}/{key}: {e.response['Error']['Message']}"
            self.logger.exception(error)
            raise S3Error(error) from e

        except Exception as e:
            error = f"Unexpected error deleting s3://{bucket}/{key}: {str(e)}"
            self.logger.exception(error)
            raise S3Error(error) from e

    def head(self, bucket: str, key: str) -> dict[str, Any]:
        """
        Get metadata about an S3 object without downloading it.

        Args:
            bucket: S3 bucket name
            key: S3 object key

        Returns:
            Dict with metadata including ContentType, ContentLength, Metadata, ETag, LastModified

        Raises:
            S3Error: If object doesn't exist or head operation fails

        Example:
            >>> s3 = S3Helper()
            >>> meta = s3.head('my-bucket', 'data/file.txt')
            >>> print(f"Size: {meta['ContentLength']} bytes")
            >>> print(f"Custom metadata: {meta['Metadata']}")
        """
        self.logger.debug(
            f"Getting head for S3 object", extra={"bucket": bucket, "key": key}
        )

        try:
            response = self._client.head_object(Bucket=bucket, Key=key)

            metadata = {
                "ContentType": response.get("ContentType"),
                "ContentLength": response.get("ContentLength"),
                "ETag": response.get("ETag", "").strip('"'),
                "LastModified": response.get("LastModified"),
                "Metadata": response.get("Metadata", {}),
                "VersionId": response.get("VersionId"),
                "StorageClass": response.get("StorageClass"),
            }

            return metadata

        except ClientError as e:
            error_code = e.response["Error"]["Code"]

            if error_code == "404":
                error = f"Object not found: s3://{bucket}/{key}"
            else:
                error = f"Failed to get head for s3://{bucket}/{key}: {e.response['Error']['Message']}"

            self.logger.exception(error)
            raise S3Error(error) from e

        except Exception as e:
            error = f"Unexpected error getting head for s3://{bucket}/{key}: {str(e)}"
            self.logger.exception(error)
            raise S3Error(error) from e

    def exists(self, bucket: str, key: str) -> bool:
        """
        Check if an object exists in S3.

        Args:
            bucket: S3 bucket name
            key: S3 object key

        Returns:
            True if object exists, False otherwise

        Example:
            >>> s3 = S3Helper()
            >>> if s3.exists('my-bucket', 'data/file.txt'):
            ...     print("File exists!")
        """
        try:
            self.head(bucket, key)
            return True
        except S3Error:
            return False

    class FileFinder:
        """
        Find files in S3 using pattern matching.

        Recursively lists all files under a given S3 path and matches
        them against whole, partial, or regex patterns.

        Example:
            >>> s3 = S3Helper()
            >>> finder = s3.FileFinder('my-bucket', 'data/2024/')
            >>>
            >>> # Find exact match
            >>> files = finder.find('report.csv', match_type='exact')
            >>>
            >>> # Find partial match
            >>> files = finder.find('report', match_type='partial')
            >>>
            >>> # Find with wildcard
            >>> files = finder.find('*.csv', match_type='wildcard')
            >>>
            >>> # Find with regex
            >>> files = finder.find(r'report_\d{4}\.csv', match_type='regex')
        """

        def __init__(self, bucket: str, prefix: str = ""):
            """
            Initialize FileFinder.

            Args:
                bucket: S3 bucket name
                prefix: S3 prefix (path) to search under
            """
            self.bucket = bucket
            self.prefix = (
                prefix.rstrip("/") + "/"
                if prefix and not prefix.endswith("/")
                else prefix
            )
            self.logger = Logger()
            self._client = boto3.client("s3")
            self._file_list: list[str] | None = None

        def _list_all_files(self) -> list[str]:
            """
            Recursively list all files under the prefix.

            Returns:
                List of S3 keys (file paths)
            """
            if self._file_list is not None:
                return self._file_list

            self.logger.info(
                f"Listing files in S3",
                extra={"bucket": self.bucket, "prefix": self.prefix},
            )

            files = []
            paginator = self._client.get_paginator("list_objects_v2")

            try:
                for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
                    if "Contents" in page:
                        for obj in page["Contents"]:
                            key = obj["Key"]
                            # Skip directories (keys ending with /)
                            if not key.endswith("/"):
                                files.append(key)

                self._file_list = files
                self.logger.info(
                    f"Found {len(files)} files in S3",
                    extra={"bucket": self.bucket, "prefix": self.prefix},
                )

                return files

            except ClientError as e:
                error = f"Failed to list files in s3://{self.bucket}/{self.prefix}: {e.response['Error']['Message']}"
                self.logger.exception(error)
                raise S3Error(error) from e

        def find(self, pattern: str, match_type: str = "wildcard") -> list[str]:
            """
            Find files matching a pattern.

            Args:
                pattern: Pattern to match against
                match_type: Type of matching:
                    - 'exact': Exact filename match
                    - 'partial': Substring match anywhere in path
                    - 'wildcard': Shell-style wildcards (* and ?)
                    - 'regex': Regular expression pattern

            Returns:
                List of matching S3 keys

            Example:
                >>> finder = S3Helper.FileFinder('bucket', 'data/')
                >>> finder.find('report.csv', 'exact')
                >>> finder.find('2024', 'partial')
                >>> finder.find('*.csv', 'wildcard')
                >>> finder.find(r'report_\d{8}\.csv', 'regex')
            """
            files = self._list_all_files()

            if match_type == "exact":
                # Exact match - check if full key or just filename matches
                matches = [
                    f for f in files if f == pattern or f.endswith("/" + pattern)
                ]

            elif match_type == "partial":
                # Substring match
                matches = [f for f in files if pattern in f]

            elif match_type == "wildcard":
                # Convert wildcard to regex
                regex_pattern = (
                    re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
                )
                regex_pattern = f"^.*{regex_pattern}$"
                matches = [f for f in files if re.match(regex_pattern, f)]

            elif match_type == "regex":
                # Direct regex match
                try:
                    compiled = re.compile(pattern)
                    matches = [f for f in files if compiled.search(f)]
                except re.error as e:
                    raise S3Error(f"Invalid regex pattern: {pattern} - {str(e)}")

            else:
                raise S3Error(
                    f"Invalid match_type: {match_type}. Must be 'exact', 'partial', 'wildcard', or 'regex'"
                )

            self.logger.info(
                f"Pattern matching complete",
                extra={
                    "pattern": pattern,
                    "match_type": match_type,
                    "matches": len(matches),
                },
            )

            return matches

        def find_latest(self, pattern: str, match_type: str = "wildcard") -> str | None:
            """
            Find the most recently modified file matching a pattern.

            Args:
                pattern: Pattern to match against
                match_type: Type of matching (see find() method)

            Returns:
                S3 key of latest file, or None if no matches

            Example:
                >>> finder = S3Helper.FileFinder('bucket', 'reports/')
                >>> latest = finder.find_latest('report_*.csv', 'wildcard')
            """
            matches = self.find(pattern, match_type)

            if not matches:
                return None

            # Get LastModified for all matches
            latest_file = None
            latest_time = None

            for key in matches:
                try:
                    response = self._client.head_object(Bucket=self.bucket, Key=key)
                    last_modified = response["LastModified"]

                    if latest_time is None or last_modified > latest_time:
                        latest_time = last_modified
                        latest_file = key

                except ClientError:
                    # Skip files we can't access
                    continue

            if latest_file:
                self.logger.info(
                    f"Found latest file",
                    extra={"file": latest_file, "last_modified": str(latest_time)},
                )

            return latest_file

    def __repr__(self) -> str:
        return f"S3Helper(region={self.config.aws.region})"
