import requests
from datetime import datetime
from ..logger import log
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

    def fetch_repos(self, verbose: bool) -> list[Repository]:
        """Fetches all repositories from the user AND their organizations."""
        headers = {'Authorization': f'token {self.token}'}
        all_repos = []
        seen_ids = set()

        # 1. Fetch User Repos
        user_repos = self._get_all_pages(
            f"{self.api_url}/user/repos", 
            headers, 
            verbose, 
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
            verbose, 
            "organizations"
        )

        # 3. Fetch Repos for each Org
        for org in orgs:
            org_name = org['login']
            org_repos = self._get_all_pages(
                f"{self.api_url}/orgs/{org_name}/repos",
                headers,
                verbose,
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

    def _get_all_pages(self, base_url, headers, verbose, context_name, query_params=None):
        """Helper to fetch all pages from a GitHub endpoint."""
        if query_params is None:
            query_params = {}
            
        items = []
        page = 1
        query_params['per_page'] = 100
        
        log(f"Fetching {context_name}...", is_verbose_mode=verbose)
        
        while True:
            try:
                query_params['page'] = page
                
                if verbose:
                    log(f"DEBUG: Requesting page {page} from {base_url} with params {query_params}", verbose_only=True, is_verbose_mode=True)

                r = requests.get(base_url, headers=headers, params=query_params, timeout=20)
                r.raise_for_status()
                
                data = r.json()
                if not data:
                    if verbose:
                        log(f"DEBUG: Page {page} empty. stopping.", verbose_only=True, is_verbose_mode=True)
                    break
                
                count = len(data)
                if verbose:
                    log(f"DEBUG: Page {page} returned {count} items.", verbose_only=True, is_verbose_mode=True)
                    
                items.extend(data)
                
                if count < query_params['per_page']:
                    break
                    
                page += 1
            except Exception as e:
                log(f"ERROR fetching {context_name}: {e}")
                break
        return items
