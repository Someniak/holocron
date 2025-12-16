import requests
from datetime import datetime
from ..logger import log
from .base import Provider, Repository

class GitLabProvider(Provider):
    def __init__(self, api_url, token):
        self.api_url = api_url
        self.token = token

    def fetch_repos(self, verbose: bool) -> list[Repository]:
        """Fetches all repositories from GitLab where the user is a member."""
        headers = {'Authorization': f'Bearer {self.token}'}
        all_repos = []
        page = 1
        per_page = 100
        
        log("Fetching GitLab repositories...", is_verbose_mode=verbose)
        
        while True:
            try:
                # Use simple=true to get lighter objects, membership=true to get all repos user has access to
                params = {
                    'membership': 'true',
                    'simple': 'true',
                    'per_page': per_page,
                    'page': page
                }
                
                if verbose:
                    log(f"DEBUG: Requesting page {page} from {self.api_url}/projects", verbose_only=True, is_verbose_mode=True)

                r = requests.get(f"{self.api_url}/projects", headers=headers, params=params, timeout=20)
                r.raise_for_status()
                
                data = r.json()
                if not data:
                    break
                
                for item in data:
                    repo = self._to_repository(item)
                    all_repos.append(repo)
                
                # Check for next page header
                if 'X-Next-Page' in r.headers and not r.headers['X-Next-Page']:
                     break
                if len(data) < per_page:
                     break
                     
                page += 1
            except Exception as e:
                log(f"ERROR fetching GitLab repos: {e}")
                break
                
        return all_repos
        
    def get_remote_url(self, repo: Repository) -> str:
        """
        Constructs the authenticated URL for pushing to GitLab.
        """
        # The logic removes '/api/v4' from the user-provided API URL to get the base URL
        # and injects the OAuth2 token.
        base_url = self.api_url.replace('/api/v4', '')
        return f"{base_url}/{repo.name}.git".replace("http://", f"http://oauth2:{self.token}@")

    def _to_repository(self, item: dict) -> Repository:
        """Helper to convert GitLab API dict to Repository object."""
        pushed_at = None
        if item.get('last_activity_at'):
            try:
                # 2024-01-01T00:00:00.000Z
                pushed_at = datetime.strptime(item['last_activity_at'].split('.')[0], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                pass
                
        return Repository(
            name=item['path'], # Use path (slug) as name
            clone_url=item['http_url_to_repo'],
            size=0, # Simple objects might not have stats, default to 0
            pushed_at=pushed_at
        )
