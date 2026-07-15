import os
import sys
import argparse
from dotenv import load_dotenv

# Load env vars from .env file
load_dotenv()

# --- METADATA ---
__version__ = "1.4.1"
__author__ = "Wouter Bloeyaert"
__license__ = "MIT"

# --- CONFIGURATION DEFAULTS ---
# We use Environment Variables for security. 
# Never hardcode passwords in open source code!
GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
GITLAB_API_URL = os.environ.get("GITLAB_API_URL", "http://gitlab.local/api/v4")
GITLAB_NAMESPACE = os.environ.get("GITLAB_NAMESPACE")

def parse_args():
    """
    Sets up the command line arguments.
    This allows the user to run: 'python g2g.py --dry-run'
    """
    parser = argparse.ArgumentParser(
        description="Holocron: GitHub to GitLab/Local Mirroring Tool"
    )
    
    # Helpers for env vars
    def get_bool_env(name):
        return os.environ.get(name, "").lower() in ("true", "1", "yes")

    # Flags (True/False options) -> Default from Env Var
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}", help="Show the version and exit")
    parser.add_argument("--credits", action="store_true", default=get_bool_env("HOLOCRON_CREDITS"), help="Show the credits and exit")
    parser.add_argument("--dry-run", action="store_true", default=get_bool_env("HOLOCRON_DRY_RUN"), help="Simulate execution without making changes")
    parser.add_argument("--watch", action="store_true", default=get_bool_env("HOLOCRON_WATCH"), help="Run continuously in a loop (Daemon mode)")
    parser.add_argument("--verbose", action="store_true", default=get_bool_env("HOLOCRON_VERBOSE"), help="Print detailed logs")
    
    # Provider Selection
    parser.add_argument("--source", type=str, choices=["github", "gitlab"], default=os.environ.get("HOLOCRON_SOURCE", "github"), help="Source provider (default: github)")
    parser.add_argument("--destination", type=str, choices=["github", "gitlab", "local"], default=os.environ.get("HOLOCRON_DESTINATION", "gitlab"), help="Destination provider (default: gitlab)")

    # value options
    parser.add_argument("--interval", type=int, default=int(os.environ.get("HOLOCRON_INTERVAL", 60)), help="Seconds to wait between checks (default: 60)")
    parser.add_argument("--window", type=int, default=int(os.environ.get("HOLOCRON_WINDOW", 10)), help="Only sync repos updated in the last X minutes")
    parser.add_argument("--storage", type=str, default=os.environ.get("HOLOCRON_STORAGE", "./mirror-data"), help="Local path to store git repositories")
    parser.add_argument("--concurrency", type=int, default=int(os.environ.get("HOLOCRON_CONCURRENCY", 5)), help="Number of concurrent sync threads (default: 5)")
    parser.add_argument("--backup-only", action="store_true", default=get_bool_env("HOLOCRON_BACKUP_ONLY"), help="Mirror locally only, skip pushing to destination")
    parser.add_argument("--checkout", action="store_true", default=get_bool_env("HOLOCRON_CHECKOUT"), help="Create a checkout of the repository alongside the mirror")
    parser.add_argument("--gitlab-namespace", type=str, default=GITLAB_NAMESPACE, help="GitLab namespace (User or Group) to push to")

    # Webhook listener (push-triggered syncs). Runs alongside --watch.
    parser.add_argument("--webhook", action="store_true", default=get_bool_env("HOLOCRON_WEBHOOK"), help="Start an HTTP listener that syncs a repo when GitHub POSTs a push event")
    parser.add_argument("--webhook-port", type=int, default=int(os.environ.get("HOLOCRON_WEBHOOK_PORT", 8080)), help="Port for the webhook listener (default: 8080)")
    parser.add_argument("--webhook-path", type=str, default=os.environ.get("HOLOCRON_WEBHOOK_PATH", "/webhook"), help="URL path the webhook listener serves (default: /webhook)")
    parser.add_argument("--webhook-cert", type=str, default=os.environ.get("HOLOCRON_WEBHOOK_CERT"), help="TLS certificate file for the webhook listener (serves HTTPS; must be paired with --webhook-key)")
    parser.add_argument("--webhook-key", type=str, default=os.environ.get("HOLOCRON_WEBHOOK_KEY"), help="TLS private key file for the webhook listener (must be paired with --webhook-cert)")

    # CI bridge: on a GitHub PR, mirror the PR head to a GitLab branch + MR (to
    # trigger the GitLab pipeline) and report the result back as a GitHub commit
    # status. Requires --webhook and BOTH tokens.
    parser.add_argument("--ci-bridge", action="store_true", default=get_bool_env("HOLOCRON_CI_BRIDGE"), help="On a GitHub PR, trigger GitLab CI and report the result back as a GitHub status check (requires --webhook)")
    parser.add_argument("--ci-status-context", type=str, default=os.environ.get("HOLOCRON_CI_STATUS_CONTEXT", "holocron/gitlab-ci"), help="GitHub commit-status context name for the CI gate (default: holocron/gitlab-ci)")
    parser.add_argument("--ci-poll-interval", type=int, default=int(os.environ.get("HOLOCRON_CI_POLL_INTERVAL", 10)), help="Seconds between GitLab pipeline status polls (default: 10)")
    parser.add_argument("--ci-poll-timeout", type=int, default=int(os.environ.get("HOLOCRON_CI_POLL_TIMEOUT", 1800)), help="Give up polling a pipeline after this many seconds (default: 1800)")
    parser.add_argument("--ci-allow-forks", action="store_true", default=get_bool_env("HOLOCRON_CI_ALLOW_FORKS"), help="Run CI for PRs opened from forks (default: off; forks run untrusted code on your runners)")
    parser.add_argument("--ci-branch-prefix", type=str, default=os.environ.get("HOLOCRON_CI_BRANCH_PREFIX", "holocron/pr-"), help="Prefix for the GitLab branch a PR head is mirrored onto (default: holocron/pr-)")

    return parser.parse_args()

def validate_config(source, destination, backup_only=False, ci_bridge=False, webhook=False):
    """
    Validates environment variables and arguments.
    Returns: (gh_token, gl_token)
    """
    gh_token = os.environ.get("GITHUB_TOKEN")
    gl_token = os.environ.get("GITLAB_TOKEN")

    # The CI bridge always reads from GitHub (status write-back) and GitLab (MR +
    # pipeline) regardless of the mirror's --source/--destination direction, and
    # is driven by PR webhooks, so it needs both tokens and the listener.
    if ci_bridge:
        if not webhook:
            print("CRITICAL: --ci-bridge requires --webhook (it is driven by GitHub PR events).")
            sys.exit(1)
        if not gh_token:
            print("CRITICAL: Missing GITHUB_TOKEN (required for --ci-bridge status checks).")
            sys.exit(1)
        if not gl_token:
            print("CRITICAL: Missing GITLAB_TOKEN (required for --ci-bridge merge requests).")
            sys.exit(1)

    # Check source requirements
    if source == "github" and not gh_token:
        print("CRITICAL: Missing GITHUB_TOKEN (required for Source: GitHub).")
        sys.exit(1)
    if source == "gitlab" and not gl_token:
        print("CRITICAL: Missing GITLAB_TOKEN (required for Source: GitLab).")
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
        
    return gh_token, gl_token
