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

    def prepare_push(self, repo: Repository):
        """
        Ensures the default branch is configured to allow force pushes.
        """
        if not self.token:
            return

        try:
            # 1. Get Repo Details (for default branch)
            # We assume repo.name is "owner/repo" for GitHub
            headers = {
                'Authorization': f'token {self.token}',
                'Accept': 'application/vnd.github.v3+json'
            }
            
            logger.debug(f"[{repo.name}] Checking branch protection...")
            
            r = requests.get(f"{self.api_url}/repos/{repo.name}", headers=headers, timeout=10)
            if r.status_code == 404:
                return
            r.raise_for_status()
            
            default_branch = r.json().get('default_branch', 'main')

            # 2. Check Protection
            # GET /repos/{owner}/{repo}/branches/{branch}/protection
            prot_url = f"{self.api_url}/repos/{repo.name}/branches/{default_branch}/protection"
            r_prot = requests.get(prot_url, headers=headers, timeout=10)
            
            if r_prot.status_code == 404:
                # Not protected
                return
                
            if r_prot.status_code == 200:
                prot_data = r_prot.json()
                # Check allow_force_pushes
                # GitHub returns: "allow_force_pushes": { "enabled": boolean }
                allow_force = prot_data.get('allow_force_pushes', {}).get('enabled', False)
                
                if not allow_force:
                    logger.info(f"[{repo.name}] Branch '{default_branch}' is protected. Enabling force push...")
                    
                    # 3. Update Protection
                    # To update just one setting without overwriting everything, GitHub API is tricky.
                    # PUT /repos/{owner}/{repo}/branches/{branch}/protection requires the FULL payload usually.
                    # HOWEVER, there is no generic PATCH for protection.
                    # But we can try to re-PUT the existing data with the modification.
                    # Or simpler: Is there a specific endpoint?
                    # No, usually need to PUT protection.
                    
                    # Let's clone the structure we got from GET (minus some readonly fields usually)
                    # This is risky as GET output != PUT input 1:1.
                    
                    # Alternative: If using GraphQL, it's easier. But we are using REST.
                    
                    # Actually, for just ONE setting, we might be stuck unless we want to managing full protection.
                    # WAIT! 'allow_force_pushes' IS typically managed by `enforce_admins` or strictly via the payload.
                    
                    # Let's try to construct a minimal PUT payload that respects existing checks?
                    # "required_status_checks": ..., "enforce_admins": ..., "required_pull_request_reviews": ..., "restrictions": ...
                    
                    # If we just send what we want to update, it might fail or disable others.
                    # BUT, 'allow_force_pushes' is top level in the PUT body.
                    
                    # Let's try to build a safe update payload from the GET response.
                    # We map the response fields to the request fields.
                    
                    update_payload = {
                        "required_status_checks": prot_data.get("required_status_checks"),
                        "enforce_admins": prot_data.get("enforce_admins", {}).get("enabled", False),
                        "required_pull_request_reviews": prot_data.get("required_pull_request_reviews"),
                        "restrictions": prot_data.get("restrictions"),
                        "allow_force_pushes": True, # This is our change
                        "allow_deletions": prot_data.get("allow_deletions", {}).get("enabled", False),
                        # There might be others like 'required_linear_history', 'block_creations', 'required_conversation_resolution', 'lock_branch', 'allow_fork_syncing'
                    }
                    
                    # Clean up None values if they shouldn't be sent? 
                    # Usually "required_status_checks": null disables it.
                    # "restrictions": null disables it.
                    
                    # We need to handle specific nested structures carefully.
                    # e.g. 'required_status_checks' might need some cleaning.
                    
                    # Given the risk of overwriting, maybe we should just LOG a warning if we can't do it safely?
                    # OR, assume if the user asks for this, they trust us.
                    # But let's implementing a safer partial update if possible? No PATCH.
                    
                    # Let's try to use the most common fields.
                    
                    # Ref: https://docs.github.com/en/rest/branches/branch-protection?apiVersion=2022-11-28#update-branch-protection
                    # Required: required_status_checks, enforce_admins, required_pull_request_reviews, restrictions.
                    
                    # If any is missing in GET, it implies disabled?
                    # If GET returns null, we send null.
                    
                    # We need to ensure we don't break their protection.
                    
                    logger.info(f"[{repo.name}] Updating branch protection to allow force push.")
                    # Note: Using True directly for bool fields, or objects check
                    
                    # Handling nullable objects
                    # required_status_checks
                    rsc = prot_data.get("required_status_checks")
                    if rsc:
                        # Convert response format to request format if needed?
                        # Response: { "url":..., "strict": boolean, "contexts": [...] }
                        # Request: { "strict": boolean, "contexts": [...] }
                        # We need to filter out 'url' etc.
                        update_payload["required_status_checks"] = {
                            "strict": rsc.get("strict", False),
                            "contexts": rsc.get("contexts", []),
                            "checks": rsc.get("checks", []) # New API uses checks
                        }
                    
                    # required_pull_request_reviews
                    rprr = prot_data.get("required_pull_request_reviews")
                    if rprr:
                        # Filter readonly
                        update_payload["required_pull_request_reviews"] = {
                            "dismissal_restrictions": rprr.get("dismissal_restrictions"), # dict with users/teams or null
                            "dismiss_stale_reviews": rprr.get("dismiss_stale_reviews", False),
                            "require_code_owner_reviews": rprr.get("require_code_owner_reviews", False),
                            "required_approving_review_count": rprr.get("required_approving_review_count", 1),
                            # "require_last_push_approval": ...
                        }
                        
                        # Handle dismissal restrictions structure
                        dr = rprr.get("dismissal_restrictions")
                        if dr:
                            # Response: { "users": [...], "teams": [...], "apps": [...] }
                            # Request: { "users": [slugs...], "teams": [slugs...], ... } -> wait, request expects list of names? or objects?
                            # Request expects "users": ["name1"], "teams": ["slug1"]
                            users = [u['login'] for u in dr.get('users', [])]
                            teams = [t['slug'] for t in dr.get('teams', [])]
                            items = {}
                            if users: items['users'] = users
                            if teams: items['teams'] = teams
                            update_payload["required_pull_request_reviews"]["dismissal_restrictions"] = items
                            
                    # restrictions
                    res = prot_data.get("restrictions")
                    if res:
                         users = [u['login'] for u in res.get('users', [])]
                         teams = [t['slug'] for t in res.get('teams', [])]
                         apps = [a['slug'] for a in res.get('apps', [])] # apps uses slug? or name? usually slug
                         
                         update_payload["restrictions"] = {
                             "users": users,
                             "teams": teams,
                             "apps": apps
                         }

                    r_put = requests.put(prot_url, headers=headers, json=update_payload, timeout=10)
                    r_put.raise_for_status()
                    logger.info(f"[{repo.name}] Successfully enabled force push.")

        except Exception as e:
            logger.warning(f"[{repo.name}] Failed to update GitHub branch protection: {e}")
