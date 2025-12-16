from .base import Provider

class GitLabProvider(Provider):
    def __init__(self, api_url, token):
        self.api_url = api_url
        self.token = token

    def fetch_repos(self, verbose: bool) -> list[dict]:
        """Not implemented for GitLab destination yet."""
        raise NotImplementedError("Fetching repos from GitLab is not yet supported.")
        
    def get_remote_url(self, repo: dict) -> str:
        """
        Constructs the authenticated URL for pushing to GitLab.
        """
        repo_name = repo['name']
        # The logic removes '/api/v4' from the user-provided API URL to get the base URL
        # and injects the OAuth2 token.
        base_url = self.api_url.replace('/api/v4', '')
        return f"{base_url}/{repo_name}.git".replace("http://", f"http://oauth2:{self.token}@")
