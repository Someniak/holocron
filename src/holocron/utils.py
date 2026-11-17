import os
import re
import sys
from urllib.parse import urlparse
from .config import __author__, __license__
# `redact` lives next to the logger so the logger can scrub by default; it is
# re-exported here because that is where callers have always imported it from.
from .logger import logger, redact

# A repository name becomes a single filesystem path component (f"{name}.git")
# and is embedded in destination push paths. Restrict it to the GitHub/GitLab
# slug charset so a crafted name cannot contain a path separator or traversal
# and escape the storage directory.
_SAFE_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Names that are not usable as a path component: `.`/`..` traverse, and `.git`
# would name the mirror directory ".git" and its sidecar checkout the storage
# root itself, so `git pull` would run inside a repository we never cloned.
_RESERVED_REPO_NAMES = {".", "..", ".git"}


def is_safe_repo_name(name):
    """
    True if `name` is safe to use as a single filesystem path component.

    Rejects path separators, the `.`/`..` traversal components, the reserved
    `.git` name, empty/None, and anything outside the slug charset that real
    GitHub/GitLab repos use.
    """
    return (
        isinstance(name, str)
        and name.lower() not in _RESERVED_REPO_NAMES
        and _SAFE_REPO_NAME_RE.match(name) is not None
    )


def split_safe_full_name(full_name):
    """
    Splits a fully-qualified repository name ("owner/repo") into validated path
    segments, or returns None if the name is not safe to build a URL or a
    filesystem path from.

    Each segment must be a plain slug, so a name carrying an extra separator or
    a traversal component is rejected as a whole rather than partially trusted.
    """
    if not isinstance(full_name, str) or not full_name:
        return None
    segments = full_name.split("/")
    for segment in segments:
        if segment.lower() in _RESERVED_REPO_NAMES:
            return None
        if _SAFE_REPO_NAME_RE.match(segment) is None:
            return None
    return segments


def is_safe_full_name(full_name):
    """
    True if `full_name` is usable as a repository identity.

    An absent full name is acceptable (sources may not expose one); a present one
    must be a set of safe path segments.
    """
    if full_name is None:
        return True
    return split_safe_full_name(full_name) is not None


def mirror_dir(storage_path, repo):
    """
    Local mirror directory for `repo`.

    Keyed on the fully-qualified name when there is one ("owner/repo" ->
    `<storage>/owner/repo.git`) so two repositories that share a bare slug but
    live under different owners get distinct mirrors instead of silently
    sharing one. Falls back to the bare slug for sources that expose no
    fully-qualified name.
    """
    segments = split_safe_full_name(repo.full_name)
    if segments:
        return os.path.join(storage_path, *segments) + ".git"
    return os.path.join(storage_path, f"{repo.name}.git")


def checkout_dir_for(repo_dir):
    """
    Sidecar checkout directory for a mirror directory.

    Strips only the trailing `.git` suffix `sync_one_repo` appended. Rewriting
    the first `.git` substring anywhere in the path (the previous behaviour)
    collapsed distinct repositories onto one working tree, and a repository
    named `.git` resolved its checkout to the storage root itself.
    """
    if repo_dir.endswith(".git"):
        return repo_dir[: -len(".git")]
    return repo_dir


def repository_key(repo):
    """
    Stable identity for a repository, used to decide whether a sync is
    redundant and to serialize work across the poll and webhook paths.
    """
    return repo.full_name or repo.name


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
