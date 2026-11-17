import requests
from datetime import datetime
from urllib.parse import quote, urlparse
from ..logger import logger, log_execution
from ..config import GITHUB_API_URL
from ..utils import split_safe_full_name
from .base import Provider, Repository

# Hard ceiling on paginated listing requests. Real accounts have orders of
# magnitude fewer pages (100 items/page), so hitting this means the endpoint is
# not honouring `page` -- abort loudly instead of looping until memory is gone.
_MAX_PAGES = 1000

# Default ports, used to resolve an omitted port on a URL to its effective one.
_DEFAULT_PORTS = {"http": 80, "https": 443}

def _effective_port(parsed):
    """Returns (scheme, host, port) for a parsed URL, or None if unusable."""
    try:
        port = parsed.port
    except ValueError:
        # e.g. https://host:notaport/ -- malformed, never acceptable.
        return None
    if port is None:
        port = _DEFAULT_PORTS.get(parsed.scheme)
    if not parsed.hostname or port is None:
        return None
    return (parsed.scheme, parsed.hostname.lower(), port)

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

    def _allowed_clone_origins(self):
        """
        `(scheme, host, port)` origins the GitHub token may be sent to, derived
        from the configured API URL.

        The port is part of the identity on purpose: a clone URL on the allowed
        host but a different port addresses a different service, and a clone
        URL on a different scheme (http against an https API) would send the
        token in cleartext. Only the configured origin's own scheme and port are
        accepted, so both cases are refused.
        """
        api = _effective_port(urlparse(self.api_url))
        if api is None:
            return set()
        api_scheme, _, api_port = api
        return {(api_scheme, host, api_port) for host in self._allowed_clone_hosts()}

    def get_remote_url(self, repo: Repository) -> str:
        """
        Constructs the authenticated clone URL.

        The OAuth token is pinned to the configured GitHub origin (scheme, host
        and port): if the repo's clone URL points anywhere else (e.g. a forged
        webhook payload with clone_url=https://attacker.tld/..., or the same
        host on another port or over cleartext), this raises ValueError instead
        of leaking the token there.
        """
        parsed = urlparse(repo.clone_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"unsupported clone URL for {repo.name!r}")

        origin = _effective_port(parsed)
        if origin is None:
            raise ValueError(f"unsupported clone URL for {repo.name!r}")

        if origin not in self._allowed_clone_origins():
            raise ValueError(
                f"clone origin '{origin[1]}':{origin[2]} ({origin[0]}) does not "
                f"match the configured GitHub origin; refusing to attach "
                f"credentials"
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
            pushed_at=pushed_at,
            full_name=item.get('full_name'),
        )

    def _get_all_pages(self, base_url, headers, context_name, query_params=None):
        """Helper to fetch all pages from a GitHub endpoint."""
        if query_params is None:
            query_params = {}
            
        items = []
        page = 1
        query_params['per_page'] = 100
        seen_ids = set()
        
        logger.debug(f"Fetching {context_name}...")
        
        while True:
            if page > _MAX_PAGES:
                # The endpoint keeps returning a full page, so `page` is being
                # ignored (or the result set is absurd). Stop loudly rather than
                # growing the list until the process is killed.
                raise RuntimeError(
                    f"Incomplete fetch of {context_name}: exceeded {_MAX_PAGES} "
                    f"pages; the endpoint is not terminating pagination"
                )
            try:
                query_params['page'] = page
                
                logger.debug(f"Requesting page {page} from {base_url} with params {query_params}")

                r = requests.get(base_url, headers=headers, params=query_params, timeout=20)
                r.raise_for_status()
                
                data = r.json()
                if not isinstance(data, list):
                    # A dict/scalar body would otherwise surface as a confusing
                    # TypeError from the caller's item['id'] access.
                    raise RuntimeError(
                        f"expected a JSON list for {context_name}, got "
                        f"{type(data).__name__}"
                    )
                if not data:
                    logger.debug(f"Page {page} empty. stopping.")
                    break
                
                count = len(data)
                logger.debug(f"Page {page} returned {count} items.")

                # A page that introduces no new identifier means `page` is not
                # being honoured: continuing would loop forever.
                new_ids = {item['id'] for item in data if item['id'] not in seen_ids}
                if not new_ids:
                    raise RuntimeError(
                        f"Incomplete fetch of {context_name}: page {page} returned "
                        f"no new items; the endpoint is not honouring pagination"
                    )
                seen_ids |= new_ids
                    
                items.extend(data)
                
                # Authoritative signal when present: no `rel="next"` means this
                # was the last page, whatever its size.
                response_headers = getattr(r, "headers", None)
                link = (
                    response_headers.get("Link")
                    if response_headers is not None and hasattr(response_headers, "get")
                    else None
                )
                if isinstance(link, str) and 'rel="next"' not in link:
                    logger.debug(f"No next link on page {page}. stopping.")
                    break

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

        # Every GitHub REST path below needs `owner/repo`; `repo.name` is the
        # bare slug on every producer (poll and webhook), which /repos/{slug}
        # cannot route and always 404s -- silently skipping the relaxation.
        #
        # The name is payload-derived, so it is re-validated here rather than
        # trusted: exactly two plain slug segments, re-joined with the real
        # separator the API routes on. A name with an extra `/` would otherwise
        # address a different resource, and percent-encoding the whole thing
        # would make even a legitimate name unroutable.
        segments = split_safe_full_name(repo.full_name or repo.name or "")
        if segments is None or len(segments) != 2:
            logger.debug(
                f"[{repo.name}] Cannot derive an owner/repo path from "
                f"{repo.full_name or repo.name!r}; skipping the branch protection check."
            )
            return
        api_repo_path = f"{quote(segments[0], safe='')}/{quote(segments[1], safe='')}"

        try:
            # 1. Get Repo Details (for default branch)
            headers = {
                'Authorization': f'token {self.token}',
                'Accept': 'application/vnd.github.v3+json'
            }
            
            logger.debug(f"[{repo.name}] Checking branch protection...")
            
            r = requests.get(
                f"{self.api_url}/repos/{api_repo_path}",
                headers=headers,
                timeout=10,
            )
            if r.status_code == 404:
                logger.warning(
                    f"[{repo.name}] Repository '{repo.full_name}' not found on "
                    f"the GitHub destination; branch protection was not inspected."
                )
                return
            r.raise_for_status()
            
            repo_data = r.json()
            default_branch = repo_data.get('default_branch', 'main')

            # Address the repository by its numeric id from here on: no later
            # request path can be steered by a payload-supplied name.
            repo_id = repo_data.get('id')
            repo_ref = str(repo_id) if repo_id is not None else api_repo_path

            # 2. Check Protection
            # GET /repos/{owner}/{repo}/branches/{branch}/protection
            prot_url = f"{self.api_url}/repos/{repo_ref}/branches/{quote(default_branch, safe='')}/protection"
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
                            if users:
                                items['users'] = users
                            if teams:
                                items['teams'] = teams
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
