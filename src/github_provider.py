import requests
from logger import log
from config import GITHUB_API_URL

def get_github_repos(token, verbose):
    """Fetches all repositories from the authenticated GitHub user."""
    headers = {'Authorization': f'token {token}'}
    repos = []
    page = 1
    
    log("Fetching repository list from GitHub...", is_verbose_mode=verbose)
    
    while True:
        try:
            url = f"{GITHUB_API_URL}/user/repos?per_page=100&page={page}"
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status() # Raise error if request failed
            
            data = r.json()
            if not data:
                break
            
            repos.extend(data)
            page += 1
        except Exception as e:
            log(f"ERROR fetching GitHub repos: {e}")
            break
            
    return repos
