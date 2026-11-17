import logging
import functools
import re

# Initialize logger
logger = logging.getLogger("holocron")

# Matches credentials embedded in a URL, e.g. https://oauth2:TOKEN@host/... or
# https://user:pass@host/... . Used to scrub tokens out of URLs before logging.
_CREDENTIAL_RE = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@")


def redact(text):
    """Removes embedded URL credentials (tokens/passwords) from a string."""
    if not text:
        return text
    return _CREDENTIAL_RE.sub(r"\1***@", str(text))


class _RedactCredentials(logging.Filter):
    """
    Scrubs URL credentials from every log record before it is emitted.

    The scrubbing lives on the logger rather than at each call site because the
    call-site version is only as reliable as the author remembers: a new
    statement that logs a token-bearing URL would otherwise leak it. Filtering
    here means every record -- including ones added later -- is redacted unless
    a caller deliberately bypasses this logger.
    """

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            # Never let logging fail because a record could not be formatted.
            return True
        scrubbed = redact(message)
        if scrubbed != message:
            record.msg = scrubbed
            record.args = ()
        return True


logger.addFilter(_RedactCredentials())


def setup_logger(verbose: bool):
    """
    Configures the global logger.
    """
    level = logging.DEBUG if verbose else logging.INFO
    
    # Create handler
    handler = logging.StreamHandler()
    
    # Create formatter
    # We want format: [2023-10-27 10:00:00] Message
    formatter = logging.Formatter('[{asctime}] {message}', style='{', datefmt='%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)
    
    # Apply settings
    logger.setLevel(level)
    logger.addHandler(handler)
    # Prevent duplicate logs if setup is called multiple times or if root logger is active
    logger.propagate = False


def log_execution(func):
    """
    Decorator to log function execution time and arguments when in verbose mode.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # We check enabledFor(DEBUG) to minimize overhead if not verbose
        if logger.isEnabledFor(logging.DEBUG):
            arg_str = ", ".join([repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()])
            logger.debug(f"Executing {func.__name__}({arg_str})")
        
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if logger.isEnabledFor(logging.DEBUG):
                 logger.debug(f"Exception in {func.__name__}: {e}")
            raise
    return wrapper

