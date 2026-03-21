import argparse
import os
import sys

from dotenv import load_dotenv

# Load env vars from .env file
load_dotenv()

# --- METADATA ---
__version__ = "1.1.5"
__author__ = "Wouter Bloeyaert"
__license__ = "MIT"

# --- CONFIGURATION DEFAULTS ---
# We use Environment Variables for security.
# Never hardcode passwords in open source code!
GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
GITLAB_API_URL = os.environ.get("GITLAB_API_URL", "https://gitlab.local/api/v4")
GITLAB_NAMESPACE = os.environ.get("GITLAB_NAMESPACE")


def parse_args() -> argparse.Namespace:
    """
    Sets up the command line arguments.
    This allows the user to run: 'python g2g.py --dry-run'
    """
    parser = argparse.ArgumentParser(description="Holocron: GitHub to GitLab/Local Mirroring Tool")

    # Helpers for env vars
    def get_bool_env(name: str) -> bool:
        return os.environ.get(name, "").lower() in ("true", "1", "yes")

    # Flags (True/False options) -> Default from Env Var
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}", help="Show the version and exit"
    )
    parser.add_argument(
        "--credits", action="store_true", default=get_bool_env("HOLOCRON_CREDITS"), help="Show the credits and exit"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=get_bool_env("HOLOCRON_DRY_RUN"),
        help="Simulate execution without making changes",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        default=get_bool_env("HOLOCRON_WATCH"),
        help="Run continuously in a loop (Daemon mode)",
    )
    parser.add_argument(
        "--verbose", action="store_true", default=get_bool_env("HOLOCRON_VERBOSE"), help="Print detailed logs"
    )

    # Provider Selection
    parser.add_argument(
        "--source",
        type=str,
        choices=["github", "gitlab", "bitbucket"],
        default=os.environ.get("HOLOCRON_SOURCE", "github"),
        help="Source provider (default: github)",
    )
    parser.add_argument(
        "--destination",
        type=str,
        choices=["github", "gitlab", "bitbucket", "local"],
        default=os.environ.get("HOLOCRON_DESTINATION", "gitlab"),
        help="Destination provider (default: gitlab)",
    )

    # value options
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("HOLOCRON_INTERVAL", 60)),
        help="Seconds to wait between checks (default: 60)",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=int(os.environ.get("HOLOCRON_WINDOW", 10)),
        help="Only sync repos updated in the last X minutes",
    )
    parser.add_argument(
        "--storage",
        type=str,
        default=os.environ.get("HOLOCRON_STORAGE", "./mirror-data"),
        help="Local path to store git repositories",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.environ.get("HOLOCRON_CONCURRENCY", 5)),
        help="Number of concurrent sync threads (default: 5)",
    )
    parser.add_argument(
        "--backup-only",
        action="store_true",
        default=get_bool_env("HOLOCRON_BACKUP_ONLY"),
        help="Mirror locally only, skip pushing to destination",
    )
    parser.add_argument(
        "--checkout",
        action="store_true",
        default=get_bool_env("HOLOCRON_CHECKOUT"),
        help="Create a checkout of the repository alongside the mirror",
    )
    parser.add_argument(
        "--gitlab-namespace", type=str, default=GITLAB_NAMESPACE, help="GitLab namespace (User or Group) to push to"
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        default=get_bool_env("HOLOCRON_CLEANUP"),
        help="Remove local mirrors that no longer exist on the source",
    )

    # Repo filtering
    parser.add_argument(
        "--include", action="append", default=None, help="Only sync repos matching this glob pattern (can be repeated)"
    )
    parser.add_argument(
        "--exclude", action="append", default=None, help="Skip repos matching this glob pattern (can be repeated)"
    )

    args = parser.parse_args()

    # Load include/exclude from env vars if not set via CLI
    if args.include is None:
        env_include = os.environ.get("HOLOCRON_INCLUDE", "")
        if env_include:
            args.include = [p.strip() for p in env_include.split(",") if p.strip()]

    if args.exclude is None:
        env_exclude = os.environ.get("HOLOCRON_EXCLUDE", "")
        if env_exclude:
            args.exclude = [p.strip() for p in env_exclude.split(",") if p.strip()]

    # Input validation
    if args.interval < 1:
        parser.error("--interval must be at least 1 second")
    if args.window < 1:
        parser.error("--window must be at least 1 minute")
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")

    return args


def validate_config(source: str, destination: str, backup_only: bool = False) -> tuple:
    """
    Validates environment variables and arguments.
    Returns: (gh_token, gl_token, bb_username, bb_app_password)
    """
    gh_token = os.environ.get("GITHUB_TOKEN")
    gl_token = os.environ.get("GITLAB_TOKEN")
    bb_username = os.environ.get("BITBUCKET_USERNAME")
    bb_app_password = os.environ.get("BITBUCKET_APP_PASSWORD")

    # Check source requirements
    if source == "github" and not gh_token:
        print("CRITICAL: Missing GITHUB_TOKEN (required for Source: GitHub).")
        sys.exit(1)
    if source == "gitlab" and not gl_token:
        print("CRITICAL: Missing GITLAB_TOKEN (required for Source: GitLab).")
        sys.exit(1)
    if source == "bitbucket" and (not bb_username or not bb_app_password):
        print("CRITICAL: Missing BITBUCKET_USERNAME and/or BITBUCKET_APP_PASSWORD (required for Source: Bitbucket).")
        sys.exit(1)

    # Check destination requirements
    if not backup_only:
        if destination == "github" and not gh_token:
            print("CRITICAL: Missing GITHUB_TOKEN (required for Destination: GitHub).")
            sys.exit(1)
        if destination == "gitlab" and not gl_token:
            print("CRITICAL: Missing GITLAB_TOKEN (required for Destination: GitLab).")
            print("Please set GITLAB_TOKEN or use --backup-only.")
            sys.exit(1)
        if destination == "bitbucket" and (not bb_username or not bb_app_password):
            print(
                "CRITICAL: Missing BITBUCKET_USERNAME and/or BITBUCKET_APP_PASSWORD (required for Destination: Bitbucket)."
            )
            sys.exit(1)

    return gh_token, gl_token, bb_username, bb_app_password
