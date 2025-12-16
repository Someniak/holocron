from abc import ABC, abstractmethod

class Provider(ABC):
    """Abstract base class for all providers (Source or Destination)."""

    @abstractmethod
    def fetch_repos(self, verbose: bool) -> list[dict]:
        """
        Fetches a list of repositories from the provider.
        Returns a list of dicts. Each dict MUST contain at least 'name'.
        """
        pass

    @abstractmethod
    def get_remote_url(self, repo: dict) -> str:
        """
        Returns the authenticated remote URL for a repository.
        If the provider is a source, this is the clone URL.
        If the provider is a destination, this is the push URL.
        """
        pass
