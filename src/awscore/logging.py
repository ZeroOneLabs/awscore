# ***REMOVED***/logging.py
import logging
import json
from datetime import datetime

class CloudWatchJsonFormatter(logging.Formatter):
    """
    Formats log records as JSON for CloudWatch Logs Insights.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Convert log record to JSON string.

        Args:
            record: The log record to format.

        Returns:
            JSON string with timestamp, level, message, and all `extra` fields.
        """
        log_entry = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "cls": getattr(record, "cls", None),
        }

        # Add all extra fields
        for key, value in record.__dict__.items():
            if key not in {
                "msg", "args", "levelname", "levelno", "pathname", "filename",
                "module", "exc_info", "exc_text", "stack_info", "lineno",
                "funcName", "created", "msecs", "relativeCreated", "thread",
                "threadName", "processName", "process", "cls"
            }:
                log_entry[key] = value

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)