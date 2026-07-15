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

# A GitHub commit-status target is addressed as "owner/repo" and interpolated
# straight into /repos/{full_name}/statuses/{sha}. Accept exactly two slug
# components joined by a single '/', so a forged full_name can't inject extra
# path segments or traversal into the API URL.
_SAFE_REPO_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

# A short git commit SHA is 7-40 hex chars; GitHub may emit up to 64 (SHA-256).
_SAFE_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")

# Characters/patterns git refuses in a ref name, plus anything that could be
# read as a command-line option or a path traversal. Used to vet the PR base
# branch before it becomes an MR target / API path component.
_UNSAFE_REF_RE = re.compile(r"[\x00-\x20~^:?*\[\\]|\.\.|@\{|//")

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

def is_safe_repo_full_name(name):
    """
    True if `name` is a safe "owner/repo" identifier for the GitHub status API.

    Requires exactly two slug components; rejects empty parts, extra slashes,
    and `.`/`..` traversal in either component.
    """
    if not isinstance(name, str) or _SAFE_REPO_FULL_NAME_RE.match(name) is None:
        return False
    return all(part not in (".", "..") for part in name.split("/"))

def is_safe_sha(sha):
    """True if `sha` is a plausible lowercase-hex git commit id (7-64 chars)."""
    return isinstance(sha, str) and _SAFE_SHA_RE.match(sha) is not None

def is_safe_git_ref(ref):
    """
    True if `ref` is safe to use as a git branch name / API path component.

    Rejects control chars and whitespace, git's special chars (`~^:?*[\\`), `..`
    traversal, `@{` reflog syntax, `//`, a leading `-` (which git would read as an
    option), and leading/trailing `/` or `.`.
    """
    if not isinstance(ref, str) or not ref:
        return False
    if ref.startswith("-") or ref.startswith("/") or ref.endswith("/"):
        return False
    if ref.startswith(".") or ref.endswith("."):
        return False
    if ref.endswith(".lock"):
        return False
    return _UNSAFE_REF_RE.search(ref) is None

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
