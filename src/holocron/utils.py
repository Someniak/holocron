import sys

from .config import __author__, __license__
from .logger import logger
from .providers.base import Repository


def handle_credits(show_credits: bool) -> None:
    """Checks for --credits flag and exits if present."""
    if show_credits:
        print("Holocron: The Ultimate Git Mirroring Tool")
        print(f"Author: {__author__}")
        print(f"License: {__license__}")
        sys.exit(0)


def format_size(kb: int) -> str:
    """Formats size in KB to MB or GB."""
    mb = kb / 1024
    gb = mb / 1024
    if gb > 1:
        return f"{gb:.2f} GB"
    return f"{mb:.2f} MB"


def print_storage_estimate(repos: list[Repository], checkout_mode: bool = False) -> None:
    """Calculates and logs the estimated storage size."""
    total_kb = sum(repo.size for repo in repos)
    logger.debug(f"Total remote size (compressed): {format_size(total_kb)}")

    if checkout_mode:
        est_kb = total_kb * 3
        logger.debug(f"Estimated local size (with checkout): ~{format_size(est_kb)}")
    else:
        logger.debug("Note: Local bare repositories may be slightly larger than remote.")
