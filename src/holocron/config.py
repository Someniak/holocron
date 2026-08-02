import os
import sys
import argparse
from dotenv import load_dotenv

# Load env vars from .env file
load_dotenv()

# --- METADATA ---
__version__ = "1.6.6"
__author__ = "Wouter Bloeyaert"
__license__ = "MIT"

# --- CONFIGURATION DEFAULTS ---
# We use Environment Variables for security. 
# Never hardcode passwords in open source code!
GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
GITLAB_API_URL = os.environ.get("GITLAB_API_URL", "http://gitlab.local/api/v4")
GITLAB_NAMESPACE = os.environ.get("GITLAB_NAMESPACE")
# Full Azure DevOps organisation URL, e.g. https://dev.azure.com/my-org
# (or the legacy https://my-org.visualstudio.com). No default: it is
# organisation-specific and only required when Azure DevOps is the source.
AZURE_DEVOPS_ORG_URL = os.environ.get("AZURE_DEVOPS_ORG_URL")
AZURE_DEVOPS_PROJECT = os.environ.get("AZURE_DEVOPS_PROJECT")

# Repository selection. Comma-separated glob patterns; HOLOCRON_REPO_LIST points
# at a file holding one include pattern per line.
HOLOCRON_INCLUDE = os.environ.get("HOLOCRON_INCLUDE")
HOLOCRON_EXCLUDE = os.environ.get("HOLOCRON_EXCLUDE")
HOLOCRON_REPO_LIST = os.environ.get("HOLOCRON_REPO_LIST")

# Environment variable holding each provider's access token.
TOKEN_ENV_VARS = {
    "github": "GITHUB_TOKEN",
    "gitlab": "GITLAB_TOKEN",
    "azure": "AZURE_DEVOPS_TOKEN",
}

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
    parser.add_argument("--source", type=str, choices=["github", "gitlab", "azure"], default=os.environ.get("HOLOCRON_SOURCE", "github"), help="Source provider (default: github). 'azure' is Azure DevOps Services (cloud).")
    parser.add_argument("--destination", type=str, choices=["github", "gitlab", "azure", "local"], default=os.environ.get("HOLOCRON_DESTINATION", "gitlab"), help="Destination provider (default: gitlab). 'azure' requires --azure-project: repositories are created inside a project.")

    # value options
    parser.add_argument("--interval", type=int, default=int(os.environ.get("HOLOCRON_INTERVAL", 60)), help="Seconds to wait between checks (default: 60)")
    parser.add_argument("--window", type=int, default=int(os.environ.get("HOLOCRON_WINDOW", 10)), help="Only sync repos updated in the last X minutes")
    parser.add_argument("--storage", type=str, default=os.environ.get("HOLOCRON_STORAGE", "./mirror-data"), help="Local path to store git repositories")
    parser.add_argument("--concurrency", type=int, default=int(os.environ.get("HOLOCRON_CONCURRENCY", 5)), help="Number of concurrent sync threads (default: 5)")
    parser.add_argument("--backup-only", action="store_true", default=get_bool_env("HOLOCRON_BACKUP_ONLY"), help="Mirror locally only, skip pushing to destination")
    parser.add_argument("--checkout", action="store_true", default=get_bool_env("HOLOCRON_CHECKOUT"), help="Create a checkout of the repository alongside the mirror")
    parser.add_argument("--gitlab-namespace", type=str, default=GITLAB_NAMESPACE, help="GitLab namespace (User or Group) to push to")
    # Repository selection. Repeatable and comma-separated, e.g.
    #   --include 'acme/*' --include api,web --exclude '*-archive'
    parser.add_argument("--include", type=str, action="append",
                        default=[HOLOCRON_INCLUDE] if HOLOCRON_INCLUDE else None,
                        help="Only mirror repositories matching these glob patterns (repeatable, comma-separated). Matched against the repo name and its 'owner/repo' path")
    parser.add_argument("--exclude", type=str, action="append",
                        default=[HOLOCRON_EXCLUDE] if HOLOCRON_EXCLUDE else None,
                        help="Skip repositories matching these glob patterns (repeatable, comma-separated). Applied after --include and always wins")
    parser.add_argument("--repo-list", type=str, default=HOLOCRON_REPO_LIST,
                        help="File holding one include pattern per line ('#' comments allowed) -- an explicit fetch list for large organisations")

    parser.add_argument("--azure-org-url", type=str, default=AZURE_DEVOPS_ORG_URL, help="Azure DevOps organisation URL, e.g. https://dev.azure.com/my-org (required for --source/--destination azure)")
    parser.add_argument("--azure-project", type=str, default=AZURE_DEVOPS_PROJECT, help="Azure DevOps project. As a source it narrows the mirror to one project (default: every project in the organisation); as a destination it is required, and is where repositories are created")
    parser.add_argument("--github-status", action="store_true", default=get_bool_env("HOLOCRON_GITHUB_STATUS"), help="Provision a per-project GITHUB_REPO CI/CD variable on GitLab so runners can report CI checks back to the GitHub commit (requires GitHub source, GitLab destination)")

    # Webhook listener (push-triggered syncs). Runs alongside --watch.
    parser.add_argument("--webhook", action="store_true", default=get_bool_env("HOLOCRON_WEBHOOK"), help="Start an HTTP listener that syncs a repo when GitHub POSTs a push event")
    parser.add_argument("--webhook-port", type=int, default=int(os.environ.get("HOLOCRON_WEBHOOK_PORT", 8080)), help="Port for the webhook listener (default: 8080)")
    parser.add_argument("--webhook-path", type=str, default=os.environ.get("HOLOCRON_WEBHOOK_PATH", "/webhook"), help="URL path the webhook listener serves (default: /webhook)")
    parser.add_argument("--webhook-cert", type=str, default=os.environ.get("HOLOCRON_WEBHOOK_CERT"), help="TLS certificate file for the webhook listener (serves HTTPS; must be paired with --webhook-key)")
    parser.add_argument("--webhook-key", type=str, default=os.environ.get("HOLOCRON_WEBHOOK_KEY"), help="TLS private key file for the webhook listener (must be paired with --webhook-cert)")

    return parser.parse_args()

def validate_config(source, destination, backup_only=False, azure_org_url=None,
                    azure_project=None):
    """
    Validates environment variables and arguments.
    Returns: a {provider_name: token} mapping (values may be None for providers
    that are not in use).
    """
    tokens = {name: os.environ.get(env) for name, env in TOKEN_ENV_VARS.items()}

    # Source and destination Azure DevOps share one organisation URL, so this
    # would mirror an organisation onto itself, repo by repo.
    if source == "azure" and destination == "azure" and not backup_only:
        print("CRITICAL: --source azure and --destination azure both point at "
              "AZURE_DEVOPS_ORG_URL, which would mirror the organisation onto itself.")
        sys.exit(1)

    # Check source requirements
    if not tokens.get(source):
        print(f"CRITICAL: Missing {TOKEN_ENV_VARS[source]} (required for Source: {source}).")
        sys.exit(1)

    if source == "azure" and not (azure_org_url or AZURE_DEVOPS_ORG_URL):
        print("CRITICAL: Missing AZURE_DEVOPS_ORG_URL / --azure-org-url "
              "(required for Source: azure, e.g. https://dev.azure.com/my-org).")
        sys.exit(1)

    # Check destination requirements ('local' needs no token)
    if not backup_only and destination in TOKEN_ENV_VARS:
        if not tokens.get(destination):
            print(f"CRITICAL: Missing {TOKEN_ENV_VARS[destination]} (required for Destination: {destination}).")
            if destination == "gitlab":
                print("Please set GITLAB_TOKEN or use --backup-only.")
            sys.exit(1)

    if not backup_only and destination == "azure":
        if not (azure_org_url or AZURE_DEVOPS_ORG_URL):
            print("CRITICAL: Missing AZURE_DEVOPS_ORG_URL / --azure-org-url "
                  "(required for Destination: azure, e.g. https://dev.azure.com/my-org).")
            sys.exit(1)
        # Azure DevOps repositories live inside a project and are not created on
        # first push, so Holocron has to be told which project to create them in.
        if not (azure_project or AZURE_DEVOPS_PROJECT):
            print("CRITICAL: Missing AZURE_DEVOPS_PROJECT / --azure-project "
                  "(required for Destination: azure -- repositories are created "
                  "inside a project).")
            sys.exit(1)

    return tokens
