# awscore/meta/metadata.py
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any
import json
import urllib.parse
import boto3
from awscore import AutoLogger

class MetaData(AutoLogger):
    """
    Load respondent metadata from local or S3.
    """

    def __init__(self, source: str | Path):
        """
        Initialize metadata loader.

        Args:
            source: Path or s3:// URL.
        """
        super().__init__(source=str(source))
        self.source = Path(source) if isinstance(source, str) and not source.startswith(("s3://", "http")) else source

    def load(self) -> List[Dict[str, Any]]:
        """
        Load metadata.

        Returns:
            List of respondent dicts.
        """
        if isinstance(self.source, Path):
            data = json.loads(self.source.read_text())
        elif str(self.source).startswith("s3://"):
            bucket, key = urllib.parse.urlparse(str(self.source)).netloc, urllib.parse.urlparse(str(self.source)).path.lstrip("/")
            s3 = boto3.client("s3")
            obj = s3.get_object(Bucket=bucket, Key=key)
            data = json.loads(obj["Body"].read())
        else:
            raise ValueError("Unsupported source")
        self.log.info("Metadata loaded", extra={"count": len(data)})
        return data