"""
Core logging module for Lambda application framework.

Provides structured logging with:
- JSON output compatible with AWS Lambda
- Automatic parent class/function name tracking
- @debug decorator for capturing function calls with arguments
- Environment-aware log levels
- Integration with Config module
"""

import logging
import json
import functools
import inspect
from typing import Any, Callable
from datetime import datetime, timezone

from .config import Config


class LoggerError(Exception):
    """Raised when logger initialization or configuration fails."""

    pass


class StructuredFormatter(logging.Formatter):
    """
    Custom formatter for structured JSON logging.

    Outputs log records as JSON for easy parsing by CloudWatch Logs Insights.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON string."""
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add parent context if available
        if hasattr(record, "parent_name"):
            log_data["parent"] = record.parent_name

        # Add function context if available
        if hasattr(record, "function_name"):
            log_data["function"] = record.function_name

        # Add extra fields
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class Logger:
    """
    Structured logger for Lambda applications (Singleton).

    Automatically configures based on Config settings and provides
    enhanced logging with parent context tracking. Each logger instance
    uses the caller's module name for precise log tracking.

    Example:
        >>> # In module: my_module.processors.data_processor
        >>> logger = Logger()
        >>> logger.info("Processing started")
        >>> # Logs with logger="my_module.processors.data_processor"
    """

    _instance: "Logger" | None = None
    _lock = None
    _loggers: dict[str, logging.Logger] = {}

    def __new__(cls):
        """Create or return existing singleton instance."""
        if cls._lock is None:
            import threading

            cls._lock = threading.Lock()

        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance

        return cls._instance

    def __init__(self):
        """Initialize logger (only runs once due to singleton)."""
        if self._initialized:
            return

        self.config = Config()
        self._initialized = True

    def _get_logger_for_caller(self) -> logging.Logger:
        """
        Get or create a logger for the calling module.

        Returns a logger named after the caller's module (__name__),
        allowing fine-grained log filtering in CloudWatch/Splunk.

        Returns:
            Logger instance for the calling module
        """
        # Get caller's module name
        frame = inspect.currentframe()
        caller_module = "__main__"

        try:
            # Walk up the stack to find the actual caller
            for _ in range(10):
                frame = frame.f_back
                if frame is None:
                    break

                # Skip internal logging methods
                function_name = frame.f_code.co_name
                if function_name in [
                    "_log",
                    "_get_logger_for_caller",
                    "debug",
                    "info",
                    "warning",
                    "error",
                    "critical",
                ]:
                    continue

                # Get the module name
                caller_module = frame.f_globals.get("__name__", "__main__")
                break
        finally:
            del frame

        # Return cached logger or create new one
        if caller_module not in self._loggers:
            self._loggers[caller_module] = self._setup_logger(caller_module)

        return self._loggers[caller_module]

    def _setup_logger(self, logger_name: str) -> logging.Logger:
        """
        Configure a Python logger based on Config.

        Args:
            logger_name: Name for the logger (typically module __name__)

        Returns:
            Configured logger instance
        """
        # Get logging configuration
        log_level = self.config.get("logging.level", "INFO").upper()
        log_format = self.config.get("logging.format", "json").lower()

        # Create logger
        logger = logging.getLogger(logger_name)
        logger.setLevel(getattr(logging, log_level, logging.INFO))

        # Remove existing handlers to avoid duplicates
        logger.handlers.clear()

        # Create console handler
        handler = logging.StreamHandler()
        handler.setLevel(getattr(logging, log_level, logging.INFO))

        # Set formatter based on configuration
        if log_format == "json":
            formatter = StructuredFormatter()
        else:
            # Standard text format
            format_string = self.config.get(
                "logging.format_string",
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            )
            formatter = logging.Formatter(format_string)

        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # Prevent propagation to root logger
        logger.propagate = False

        return logger

    def _get_caller_info(self) -> dict[str, str]:
        """
        Extract caller information from the call stack.

        Returns:
            Dict with parent_name and function_name
        """
        frame = inspect.currentframe()
        caller_info = {"parent_name": None, "function_name": None}

        try:
            # Walk up the stack to find the actual caller (skip logging internals)
            for _ in range(10):  # Limit stack traversal
                frame = frame.f_back
                if frame is None:
                    break

                # Get function/method name
                function_name = frame.f_code.co_name

                # Skip internal logging methods
                if function_name in [
                    "_log",
                    "debug",
                    "info",
                    "warning",
                    "error",
                    "critical",
                ]:
                    continue

                caller_info["function_name"] = function_name

                # Try to get class name if this is a method
                if "self" in frame.f_locals:
                    instance = frame.f_locals["self"]
                    caller_info["parent_name"] = instance.__class__.__name__
                elif "cls" in frame.f_locals:
                    cls = frame.f_locals["cls"]
                    caller_info["parent_name"] = cls.__name__

                break
        finally:
            del frame  # Avoid reference cycles

        return caller_info

    def _log(
        self,
        level: int,
        message: str,
        extra: dict[str, Any] | None = None,
        exc_info: bool = False,
    ) -> None:
        """
        Internal logging method with context enrichment.

        Args:
            level: Logging level (logging.DEBUG, logging.INFO, etc.)
            message: Log message
            extra: Additional fields to include in structured logs
            exc_info: Whether to include exception information
        """
        # Get the appropriate logger for the caller
        logger = self._get_logger_for_caller()

        # Get caller context
        caller_info = self._get_caller_info()

        # Create LogRecord with extra context
        record = logger.makeRecord(
            logger.name,
            level,
            "(internal)",
            0,
            message,
            (),
            None if not exc_info else True,
            extra=extra,
        )

        # Add parent and function context
        if caller_info["parent_name"]:
            record.parent_name = caller_info["parent_name"]
        if caller_info["function_name"]:
            record.function_name = caller_info["function_name"]

        # Add extra fields
        if extra:
            record.extra_fields = extra

        logger.handle(record)

    def debug(self, message: str, extra: dict[str, Any] | None = None) -> None:
        """Log debug message."""
        self._log(logging.DEBUG, message, extra)

    def info(self, message: str, extra: dict[str, Any] | None = None) -> None:
        """Log info message."""
        self._log(logging.INFO, message, extra)

    def warning(self, message: str, extra: dict[str, Any] | None = None) -> None:
        """Log warning message."""
        self._log(logging.WARNING, message, extra)

    def error(self, message: str, extra: dict[str, Any] | None = None) -> None:
        """Log error message."""
        self._log(logging.ERROR, message, extra)

    def exception(self, message: str, extra: dict[str, Any] | None = None) -> None:
        """Log exception with automatic traceback capture."""
        self._log(logging.ERROR, message, extra, exc_info=True)

    def critical(self, message: str, extra: dict[str, Any] | None = None) -> None:
        """Log critical message."""
        self._log(logging.CRITICAL, message, extra)

    @classmethod
    def reset(cls) -> None:
        """Reset singleton instance. Primarily for testing."""
        with cls._lock:
            if cls._instance:
                cls._instance._loggers.clear()
            cls._instance = None


def debug(func: Callable | None = None, *, capture_args: bool = True) -> Callable:
    """
    Decorator to log function calls with arguments at DEBUG level.

    Captures function name, arguments, and return values.
    Controlled by logging.debug_capture_args config (defaults to True in dev/uat, False in prod).

    Args:
        func: Function to decorate
        capture_args: Whether to capture and log function arguments (can contain sensitive data)

    Example:
        >>> @debug
        >>> def process_data(user_id, data):
        >>>     return {"status": "success"}

        >>> @debug(capture_args=False)
        >>> def process_sensitive(api_key, secret):
        >>>     return "done"
    """

    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            logger = Logger()
            config = Config()

            # Check if argument capture is enabled (environment-aware)
            should_capture = capture_args
            if config.get("logging.debug_capture_args") is not None:
                should_capture = config.get("logging.debug_capture_args")
            else:
                # Default: capture in dev/uat, don't capture in prod
                environment = config.environment
                should_capture = environment in ["dev", "uat"] if environment else True

            # Build log message
            func_name = f.__qualname__

            if should_capture:
                # Capture arguments
                sig = inspect.signature(f)
                bound_args = sig.bind(*args, **kwargs)
                bound_args.apply_defaults()

                args_dict = dict(bound_args.arguments)

                # Sanitize 'self' and 'cls' for cleaner logs
                if "self" in args_dict:
                    args_dict["self"] = (
                        f"<{args_dict['self'].__class__.__name__} instance>"
                    )
                if "cls" in args_dict:
                    args_dict["cls"] = f"<{args_dict['cls'].__name__} class>"

                logger.debug(f"Calling {func_name}", extra={"arguments": args_dict})
            else:
                logger.debug(f"Calling {func_name}")

            try:
                result = f(*args, **kwargs)

                if should_capture:
                    # Log return value (truncate if too large)
                    result_str = str(result)
                    if len(result_str) > 500:
                        result_str = result_str[:500] + "... (truncated)"

                    logger.debug(
                        f"Completed {func_name}", extra={"return_value": result_str}
                    )
                else:
                    logger.debug(f"Completed {func_name}")

                return result

            except Exception as e:
                logger.exception(f"Exception in {func_name}: {str(e)}")
                raise

        return wrapper

    # Handle both @debug and @debug() syntax
    if func is None:
        return decorator
    else:
        return decorator(func)
