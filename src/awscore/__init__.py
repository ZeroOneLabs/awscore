# ***REMOVED***/__init__.py
import logging
from typing import Any

class AutoLogger:
    """
    Mixin that automatically injects `cls` and `module` into log `extra`.

    Usage:
        class MyClass(AutoLogger):
            def __init__(self, **ctx):
                super().__init__(**ctx)  # ctx becomes log context
    """
    _module_log = logging.getLogger(__name__)

    def __init__(self, **log_context: Any) -> None:
        """
        Initialize logger with class/module and custom context.

        Args:
            **log_context: Key-value pairs added to every log record.
        """
        extra = {
            "cls": self.__class__.__name__,
            "module": self.__class__.__module__,
            **log_context,
        }
        self.log = logging.LoggerAdapter(self._module_log, extra)