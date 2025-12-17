import os
import sys
import argparse
from dotenv import load_dotenv

# Load env vars from .env file
# Load env vars from .env file
load_dotenv()

# --- METADATA ---
__version__ = "0.1.0"
__author__ = "Wouter Bloeyaert"
__license__ = "MIT"

# --- CONFIGURATION DEFAULTS ---
# We use Environment Variables for security. 
# Never hardcode passwords in open source code!
GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
GITLAB_API_URL = os.environ.get("GITLAB_API_URL", "http://gitlab.local/api/v4")

def parse_args():
    """
    Sets up the command line arguments.
    This allows the user to run: 'python g2g.py --dry-run'
    """
    parser = argparse.ArgumentParser(
        description="Holocron: GitHub to GitLab/Local Mirroring Tool"
    )
    
    # Flags (True/False options)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}", help="Show the version and exit")
    parser.add_argument("--credits", action="store_true", help="Show the credits and exit")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without making changes")
    parser.add_argument("--watch", action="store_true", help="Run continuously in a loop (Daemon mode)")
    parser.add_argument("--verbose", action="store_true", help="Print detailed logs")
    
    # value options
    parser.add_argument("--interval", type=int, default=60, help="Seconds to wait between checks (default: 60)")
    parser.add_argument("--window", type=int, default=10, help="Only sync repos updated in the last X minutes")
    parser.add_argument("--storage", type=str, default="./mirror-data", help="Local path to store git repositories")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent sync threads (default: 5)")
    parser.add_argument("--backup-only", action="store_true", help="Mirror locally only, skip pushing to GitLab")
    parser.add_argument("--checkout", action="store_true", help="Create a checkout of the repository alongside the mirror")

    return parser.parse_args()

def validate_config(backup_only=False):
    """
    Validates environment variables and arguments.
    Returns: (gh_token, gl_token)
    """
    gh_token = os.environ.get("GITHUB_TOKEN")
    gl_token = os.environ.get("GITLAB_TOKEN")

    if not gh_token:
        print("CRITICAL: Missing GITHUB_TOKEN.")
        sys.exit(1)

    if not backup_only and not gl_token:
        print("CRITICAL: Missing GITLAB_TOKEN.")
        print("Please set GITLAB_TOKEN or use --backup-only.")
        sys.exit(1)
        
    return gh_token, gl_token
