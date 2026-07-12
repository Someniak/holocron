import re
import sys
from .config import __author__, __license__
from .logger import logger

# Matches credentials embedded in a URL, e.g. https://oauth2:TOKEN@host/... or
# https://user:pass@host/... . Used to scrub tokens out of git output before logging.
_CREDENTIAL_RE = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@")

def redact(text):
    """Removes embedded URL credentials (tokens/passwords) from a string."""
    if not text:
        return text
    return _CREDENTIAL_RE.sub(r"\1***@", str(text))

def handle_credits(show_credits):
    """Checks for --credits flag and exits if pre sent."""
    if show_credits:
        print(f"Holocron: The Ultimate Git Mirroring Tool")
        print(f"Author: {__author__}")
        print(f"License: {__license__}")
        sys.exit(0)

def format_size(kb):
    """Formats size in KB to MB or GB."""
    mb = kb / 1024
    gb = mb / 1024
    if gb > 1:
            return f"{gb:.2f} GB"
    return f"{mb:.2f} MB"

def print_storage_estimate(repos, checkout_mode=False):
    """Calculates and logs the estimated storage size."""
    total_kb = sum(repo.size for repo in repos)
    logger.debug(f"Total remote size (compressed): {format_size(total_kb)}")

    if checkout_mode:
        # Heuristic: 3x for checkout overhead (working dir + metadata)
        est_kb = total_kb * 3
        logger.debug(f"Estimated local size (with checkout): ~{format_size(est_kb)}")
    else:
        logger.debug("Note: Local bare repositories may be slightly larger than remote.")
