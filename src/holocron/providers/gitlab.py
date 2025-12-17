import requests
from datetime import datetime
from ..logger import logger, log_execution
from .base import Provider, Repository

class GitLabProvider(Provider):
    def __init__(self, api_url, token, namespace=None):
        self.api_url = api_url
        self.token = token
        self.namespace = namespace

    @log_execution
    def fetch_repos(self) -> list[Repository]:
        """
        Fetches repositories from GitLab (User + Groups).
        """
        headers = {'Private-Token': self.token}
        all_repos = []
        seen_ids = set()

        # 1. Fetch User Repos (and member projects)
        user_repos = self._get_all_pages(
            f"{self.api_url}/projects",
            headers,
            "GitLab projects (membership=true)",
             query_params={
                "membership": "true",
                "simple": "true" 
             }
        )
        
        for item in user_repos:
             if item['id'] not in seen_ids:
                all_repos.append(self._to_repository(item))
                seen_ids.add(item['id'])
        
        # Note: GitLab's /projects?membership=true usually covers everything a user has access to, 
        # including group projects. If strict group separation is needed, we'd query /groups.
        
        return all_repos

    def get_remote_url(self, repo: Repository) -> str:
        """
        Constructs the authenticated URL for pushing to GitLab.
        """
        # The logic removes '/api/v4' from the user-provided API URL to get the base URL
        # and injects the OAuth2 token.
        # The logic removes '/api/v4' from the user-provided API URL to get the base URL
        # and injects the OAuth2 token.
        base_url = self.api_url.rstrip('/')
        if base_url.endswith('/api/v4'):
            base_url = base_url[:-7]
        base_url = base_url.rstrip('/')

        url = f"{base_url}/{repo.name}.git"

        if self.namespace:
            # Inject namespace (group/user) between base_url and repo_name
            url = f"{base_url}/{self.namespace}/{repo.name}.git"

        if self.token:
            if url.startswith("https://"):
                return url.replace("https://", f"https://oauth2:{self.token}@", 1)
            elif url.startswith("http://"):
                return url.replace("http://", f"http://oauth2:{self.token}@", 1)
        
        return url

    def _to_repository(self, item: dict) -> Repository:
        """Helper to convert GitLab API dict to Repository object."""
        pushed_at = None
        if item.get('last_activity_at'):
            try:
                # 2024-01-01T00:00:00.000Z
                pushed_at = datetime.strptime(item['last_activity_at'].split('.')[0], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                # Try without microseconds if it fails
                try:
                    pushed_at = datetime.strptime(item['last_activity_at'], "%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    pass
                
        return Repository(
            name=item['path'], # Use path (slug) as name
            clone_url=item['http_url_to_repo'],
            size=0, # Simple objects might not have stats, default to 0
            pushed_at=pushed_at
        )

    def _get_all_pages(self, base_url, headers, context_name, query_params=None):
        """Helper to fetch all pages from a GitLab endpoint."""
        if query_params is None:
            query_params = {}

        items = []
        page = 1
        query_params['per_page'] = 100
        
        logger.debug(f"Fetching {context_name}...")
        
        while True:
            try:
                query_params['page'] = page
                
                logger.debug(f"Requesting page {page} from {base_url}...")

                r = requests.get(base_url, headers=headers, params=query_params, timeout=20)
                r.raise_for_status()
                
                data = r.json()
                if not data:
                    break
                
                count = len(data)
                items.extend(data)
                
                # Check for pagination headers usually, but length check is robust enough for simple cases
                if count < query_params['per_page']:
                     break
                
                page += 1
            except Exception as e:
                logger.error(f"ERROR fetching {context_name}: {e}")
                break
        return items
