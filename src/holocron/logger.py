import logging
import functools
from datetime import datetime

# Initialize logger
logger = logging.getLogger("holocron")

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

