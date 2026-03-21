import functools
import logging
from typing import Any, Callable, TypeVar

# Initialize logger
logger = logging.getLogger("holocron")

F = TypeVar("F", bound=Callable[..., Any])


def setup_logger(verbose: bool) -> None:
    """
    Configures the global logger.
    """
    level = logging.DEBUG if verbose else logging.INFO

    handler = logging.StreamHandler()

    formatter = logging.Formatter("[{asctime}] {message}", style="{", datefmt="%Y-%m-%d %H:%M:%S")
    handler.setFormatter(formatter)

    logger.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = False


def log_execution(func: F) -> F:
    """
    Decorator to log function execution time and arguments when in verbose mode.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if logger.isEnabledFor(logging.DEBUG):
            arg_str = ", ".join([repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()])
            logger.debug(f"Executing {func.__name__}({arg_str})")

        try:
            return func(*args, **kwargs)
        except Exception as e:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Exception in {func.__name__}: {e}")
            raise

    return wrapper  # type: ignore[return-value]
