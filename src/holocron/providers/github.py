import requests
from datetime import datetime
from urllib.parse import urlparse
from ..logger import logger, log_execution
from ..config import GITHUB_API_URL
from .base import Provider, Repository

class GitHubProvider(Provider):
    def __init__(self, token, api_url=GITHUB_API_URL):
        self.token = token
        self.api_url = api_url

    def _allowed_clone_hosts(self):
        """
        Hosts the GitHub token may be sent to, derived from the configured API
        URL. Accepts the API host and, for github.com SaaS, its api-stripped
        form (api.github.com -> github.com); GitHub Enterprise uses a path-based
        API on the same host, so the host is accepted as-is.
        """
        api_host = (urlparse(self.api_url).hostname or "").lower()
        hosts = {api_host} if api_host else set()
        if api_host.startswith("api."):
            hosts.add(api_host[len("api."):])
        return hosts

    def get_remote_url(self, repo: Repository) -> str:
        """
        Constructs the authenticated clone URL.

        The OAuth token is pinned to the configured GitHub host: if the repo's
        clone URL points anywhere else (e.g. a forged webhook payload with
        clone_url=https://attacker.tld/...), this raises ValueError instead of
        leaking the token to that host.
        """
        parsed = urlparse(repo.clone_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"unsupported clone URL for {repo.name!r}")

        if parsed.hostname.lower() not in self._allowed_clone_hosts():
            raise ValueError(
                f"clone host {parsed.hostname!r} does not match the configured "
                f"GitHub host; refusing to attach credentials"
            )

        if not self.token:
            return repo.clone_url
        if parsed.scheme == "https":
            return repo.clone_url.replace("https://", f"https://oauth2:{self.token}@", 1)
        return repo.clone_url.replace("http://", f"http://oauth2:{self.token}@", 1)

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
                # Do NOT swallow-and-break: returning pages 1..N-1 here would look
                # like a complete result and silently drop repos from the mirror.
                # Fail loud so the caller knows the fetch was incomplete.
                logger.error(f"ERROR fetching {context_name}: {e}")
                raise RuntimeError(
                    f"Incomplete fetch of {context_name}: failed at page {page}: {e}"
                ) from e
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
                # Protection GET returns allow_force_pushes as {"enabled": bool}.
                allow_force = prot_data.get('allow_force_pushes', {}).get('enabled', False)

                if not allow_force:
                    logger.info(f"[{repo.name}] Branch '{default_branch}' is protected. Enabling force push...")

                    # GitHub has no PATCH for branch protection; the only way to flip
                    # allow_force_pushes is a full PUT. To avoid wiping the user's other
                    # rules, we rebuild the PUT payload from the GET response, translating
                    # the (differently-shaped) GET fields back into PUT request format.
                    # Ref: https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection
                    update_payload = {
                        "required_status_checks": prot_data.get("required_status_checks"),
                        "enforce_admins": prot_data.get("enforce_admins", {}).get("enabled", False),
                        "required_pull_request_reviews": prot_data.get("required_pull_request_reviews"),
                        "restrictions": prot_data.get("restrictions"),
                        "allow_force_pushes": True,  # the one setting we are changing
                        "allow_deletions": prot_data.get("allow_deletions", {}).get("enabled", False),
                    }

                    logger.info(f"[{repo.name}] Updating branch protection to allow force push.")

                    # Translate nullable nested objects from GET shape to PUT shape.
                    # required_status_checks: drop read-only 'url', keep strict/contexts/checks.
                    rsc = prot_data.get("required_status_checks")
                    if rsc:
                        update_payload["required_status_checks"] = {
                            "strict": rsc.get("strict", False),
                            "contexts": rsc.get("contexts", []),
                            "checks": rsc.get("checks", [])  # newer API uses 'checks'
                        }

                    # required_pull_request_reviews: keep only PUT-writable fields.
                    rprr = prot_data.get("required_pull_request_reviews")
                    if rprr:
                        update_payload["required_pull_request_reviews"] = {
                            "dismissal_restrictions": rprr.get("dismissal_restrictions"),
                            "dismiss_stale_reviews": rprr.get("dismiss_stale_reviews", False),
                            "require_code_owner_reviews": rprr.get("require_code_owner_reviews", False),
                            "required_approving_review_count": rprr.get("required_approving_review_count", 1),
                        }

                        # PUT expects dismissal_restrictions as lists of login/slug names,
                        # not the user/team objects the GET response returns.
                        dr = rprr.get("dismissal_restrictions")
                        if dr:
                            users = [u['login'] for u in dr.get('users', [])]
                            teams = [t['slug'] for t in dr.get('teams', [])]
                            items = {}
                            if users: items['users'] = users
                            if teams: items['teams'] = teams
                            update_payload["required_pull_request_reviews"]["dismissal_restrictions"] = items

                    # restrictions: same object -> name-list translation.
                    res = prot_data.get("restrictions")
                    if res:
                         users = [u['login'] for u in res.get('users', [])]
                         teams = [t['slug'] for t in res.get('teams', [])]
                         apps = [a['slug'] for a in res.get('apps', [])]

                         update_payload["restrictions"] = {
                             "users": users,
                             "teams": teams,
                             "apps": apps
                         }

                    r_put = requests.put(prot_url, headers=headers, json=update_payload, timeout=10)
                    r_put.raise_for_status()
                    logger.info(f"[{repo.name}] Successfully enabled force push.")

        except requests.exceptions.HTTPError as e:
            # Non-fatal: if we cannot relax protection the subsequent push will
            # fail and be reported separately. Surface the status so the common
            # cause (insufficient token scope) is diagnosable instead of hidden.
            status = e.response.status_code if e.response is not None else "?"
            hint = " (token likely missing 'repo'/'administration' scope)" if status == 403 else ""
            logger.warning(f"[{repo.name}] Failed to update GitHub branch protection: HTTP {status}{hint}: {e}")
        except Exception as e:
            logger.warning(f"[{repo.name}] Failed to update GitHub branch protection: {e}")
