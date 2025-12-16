import requests
from datetime import datetime
from ..logger import logger, log_execution
from ..config import GITHUB_API_URL
from .base import Provider, Repository

class GitHubProvider(Provider):
    def __init__(self, token, api_url=GITHUB_API_URL):
        self.token = token
        self.api_url = api_url

    def get_remote_url(self, repo: Repository) -> str:
        """Constructs the authenticated clone URL."""
        # Dataclass field access
        return repo.clone_url.replace("https://", f"https://oauth2:{self.token}@")

    @log_execution
    def fetch_repos(self) -> list[Repository]:
        """Fetches all repositories from the user AND their organizations."""
        headers = {'Authorization': f'token {self.token}'}
        all_repos = []
        seen_ids = set()

        # 1. Fetch User Repos
        user_repos = self._get_all_pages(
            f"{self.api_url}/user/repos", 
            headers, 
            "user repositories (visibility=all, all affiliations)",
            query_params={
                "visibility": "all",
                "affiliation": "owner,collaborator,organization_member"
            }
        )
        
        for item in user_repos:
            if item['id'] not in seen_ids:
                all_repos.append(self._to_repository(item))
                seen_ids.add(item['id'])

        # 2. Fetch User Organizations
        orgs = self._get_all_pages(
            f"{self.api_url}/user/orgs", 
            headers, 
            "organizations"
        )

        # 3. Fetch Repos for each Org
        for org in orgs:
            org_name = org['login']
            org_repos = self._get_all_pages(
                f"{self.api_url}/orgs/{org_name}/repos",
                headers,
                f"repositories for organization '{org_name}'",
                query_params={"type": "all"}
            )
            for item in org_repos:
                if item['id'] not in seen_ids:
                    all_repos.append(self._to_repository(item))
                    seen_ids.add(item['id'])
                    
        return all_repos

    def _to_repository(self, item: dict) -> Repository:
        """Helper to convert GitHub API dict to Repository object."""
        pushed_at = None
        if item.get('pushed_at'):
            try:
                pushed_at = datetime.strptime(item['pushed_at'], "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                pass
                
        return Repository(
            name=item['name'],
            clone_url=item['clone_url'],
            size=item.get('size', 0),
            pushed_at=pushed_at
        )

    def _get_all_pages(self, base_url, headers, context_name, query_params=None):
        """Helper to fetch all pages from a GitHub endpoint."""
        if query_params is None:
            query_params = {}
            
        items = []
        page = 1
        query_params['per_page'] = 100
        
        logger.debug(f"Fetching {context_name}...")
        
        while True:
            try:
                query_params['page'] = page
                
                logger.debug(f"Requesting page {page} from {base_url} with params {query_params}")

                r = requests.get(base_url, headers=headers, params=query_params, timeout=20)
                r.raise_for_status()
                
                data = r.json()
                if not data:
                    logger.debug(f"Page {page} empty. stopping.")
                    break
                
                count = len(data)
                logger.debug(f"Page {page} returned {count} items.")
                    
                items.extend(data)
                
                if count < query_params['per_page']:
                    break
                    
                page += 1
            except Exception as e:
                logger.error(f"ERROR fetching {context_name}: {e}")
                break
        return items
