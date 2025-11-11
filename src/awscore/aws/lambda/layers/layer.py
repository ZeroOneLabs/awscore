# awscore/aws/lambda_/layers/layer.py
from __future__ import annotations
import zipfile
import io
from pathlib import Path
from typing import IO
from awscore import AutoLogger

class Layer(AutoLogger):
    """
    Manage Lambda Layers.
    """

    def __init__(self, lambda_client, layer_name: str):
        """
        Initialize Layer manager.

        Args:
            lambda_client: boto3 Lambda client.
            layer_name: Name of the layer.
        """
        super().__init__(layer_name=layer_name)
        self.client = lambda_client
        self.name = layer_name

    def publish(self, code_path: Path, description: str = "") -> dict:
        """
        Publish a new layer version from local directory.

        Args:
            code_path: Path to directory to zip.
            description: Optional description.

        Returns:
            Lambda publish_layer_version response.
        """
        zip_buf = self._zip_dir(code_path)
        self.log.info("Publishing layer", extra={"name": self.name, "path": str(code_path)})
        response = self.client.publish_layer_version(
            LayerName=self.name,
            Content={"ZipFile": zip_buf.getvalue()},
            CompatibleRuntimes=["python3.12"],
            Description=description,
        )
        self.log.info("Layer published", extra={"version": response["Version"]})
        return response

    @staticmethod
    def _zip_dir(src: Path) -> io.BytesIO:
        """Zip directory in memory."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in src.walk():
                for f in files:
                    fp = root / f
                    arcname = fp.relative_to(src.parent)
                    z.write(fp, arcname)
        buf.seek(0)
        return buf