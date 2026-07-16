import re
import sys
from urllib.parse import urlparse
from .config import __author__, __license__
from .logger import logger

# Matches credentials embedded in a URL, e.g. https://oauth2:TOKEN@host/... or
# https://user:pass@host/... . Used to scrub tokens out of git output before logging.
_CREDENTIAL_RE = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@")

# A repository name becomes a single filesystem path component (f"{name}.git")
# and is embedded in destination push paths. Restrict it to the GitHub/GitLab
# slug charset so a crafted name cannot contain a path separator or traversal
# and escape the storage directory.
_SAFE_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

def redact(text):
    """Removes embedded URL credentials (tokens/passwords) from a string."""
    if not text:
        return text
    return _CREDENTIAL_RE.sub(r"\1***@", str(text))

def is_safe_repo_name(name):
    """
    True if `name` is safe to use as a single filesystem path component.

    Rejects path separators, the `.`/`..` traversal components, empty/None, and
    anything outside the slug charset that real GitHub/GitLab repos use.
    """
    return (
        isinstance(name, str)
        and name not in (".", "..")
        and _SAFE_REPO_NAME_RE.match(name) is not None
    )

def is_safe_clone_url(url):
    """
    True if `url` is an http(s) URL safe to hand to `git clone`.

    Only http/https are allowed. This rejects git's `ext::` transport (which
    executes arbitrary commands), `file://`/`ssh://` transports, and any value
    git would otherwise parse as a command-line option (e.g. a leading '-').
    """
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)

def handle_credits(show_credits):
    """Checks for --credits flag and exits if pre sent."""
    if show_credits:
        print("Holocron: The Ultimate Git Mirroring Tool")
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
