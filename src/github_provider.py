import requests
from logger import log
from config import GITHUB_API_URL

def get_all_pages(base_url, headers, verbose, context_name, query_params=None):
    """Helper to fetch all pages from a GitHub endpoint."""
    if query_params is None:
        query_params = {}
        
    items = []
    page = 1
    query_params['per_page'] = 100
    
    log(f"Fetching {context_name}...", is_verbose_mode=verbose)
    
    while True:
        try:
            # Update page number in params
            query_params['page'] = page
            
            if verbose:
                # Log the actual request for debugging
                # (Masking token if we were printing headers, but we aren't)
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
            
            # Optimization: If we got fewer than per_page, we are done
            if count < query_params['per_page']:
                break
                
            page += 1
        except Exception as e:
            log(f"ERROR fetching {context_name}: {e}")
            break
    return items

def get_github_repos(token, verbose):
    """Fetches all repositories from the user AND their organizations."""
    headers = {'Authorization': f'token {token}'}
    all_repos = []
    seen_ids = set()

    # 1. Fetch User Repos (includes explicit access to outside org repos)
    # Using specific parameters as per GitHub docs to ensure maximal coverage
    user_repos = get_all_pages(
        f"{GITHUB_API_URL}/user/repos", 
        headers, 
        verbose, 
        "user repositories (visibility=all, all affiliations)",
        query_params={
            "visibility": "all",
            "affiliation": "owner,collaborator,organization_member"
        }
    )
    
    for repo in user_repos:
        if repo['id'] not in seen_ids:
            all_repos.append(repo)
            seen_ids.add(repo['id'])

    # 2. Fetch User Organizations
    orgs = get_all_pages(
        f"{GITHUB_API_URL}/user/orgs", 
        headers, 
        verbose, 
        "organizations"
    )

    # 3. Fetch Repos for each Org
    for org in orgs:
        org_name = org['login']
        # type=all includes forks, private, public
        org_repos = get_all_pages(
            f"{GITHUB_API_URL}/orgs/{org_name}/repos",
            headers,
            verbose,
            f"repositories for organization '{org_name}'",
            query_params={"type": "all"}
        )
        for repo in org_repos:
            if repo['id'] not in seen_ids:
                all_repos.append(repo)
                seen_ids.add(repo['id'])
                
    return all_repos
