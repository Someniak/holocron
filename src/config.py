import os
import argparse
from dotenv import load_dotenv

# Load env vars from .env file
load_dotenv()

# --- CONFIGURATION DEFAULTS ---
# We use Environment Variables for security. 
# Never hardcode passwords in open source code!
GITHUB_API_URL = "https://api.github.com"
GITLAB_API_URL = os.environ.get("GITLAB_API_URL", "http://gitlab.local/api/v4")

def parse_args():
    """
    Sets up the command line arguments.
    This allows the user to run: 'python g2g.py --dry-run'
    """
    parser = argparse.ArgumentParser(description="G2G-Sync: The GitHub to GitLab Mirroring Tool")
    
    # Flags (True/False options)
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without making changes")
    parser.add_argument("--watch", action="store_true", help="Run continuously in a loop (Daemon mode)")
    parser.add_argument("--verbose", action="store_true", help="Print detailed logs")
    
    # value options
    parser.add_argument("--interval", type=int, default=60, help="Seconds to wait between checks (default: 60)")
    parser.add_argument("--window", type=int, default=10, help="Only sync repos updated in the last X minutes")
    parser.add_argument("--storage", type=str, default="./mirror-data", help="Local path to store git repositories")

    return parser.parse_args()
