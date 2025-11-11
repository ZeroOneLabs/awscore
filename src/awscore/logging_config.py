# awscore/logging_config.py
import logging
import sys
from .logging import CloudWatchJsonFormatter

def setup_json_logging(level: int = logging.INFO) -> None:
    """
    Configure root logger to emit JSON to stdout.

    Args:
        level: Logging level (default INFO).
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CloudWatchJsonFormatter())

    root = logging.getLogger("awscore")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False

    # Silence noisy libs
    for lib in ("boto3", "botocore", "urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)