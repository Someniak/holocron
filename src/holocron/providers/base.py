from abc import ABC, abstractmethod
from typing import Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Repository:
    """Repository object - spec v1"""
    name: str
    clone_url: str
    size: int = 0  # in KB
    pushed_at: Optional[datetime] = None
    # Fully-qualified source path ("owner/repo"). Set by the GitHub source so
    # downstream steps (e.g. provisioning the GITHUB_REPO CI variable on GitLab)
    # know which GitHub repository a mirror came from. None for sources that
    # don't expose one.
    full_name: Optional[str] = None

class Provider(ABC):
    """Abstract base class for all providers (Source or Destination)."""

    @abstractmethod
    def fetch_repos(self) -> list[Repository]:
        """
        Fetches the list of repositories from the provider.
        Returns: A list of Repository objects.
        """
        pass

    @abstractmethod
    def get_remote_url(self, repo: Repository) -> str:
        """
        Returns the authenticated remote URL for a repository.
        If the provider is a source, this is the clone URL.
        If the provider is a destination, this is the push URL.
        """
        pass

    def prepare_push(self, repo: Repository):
        """
        Optional hook to prepare the repository for pushing.
        e.g., Unprotect branches on GitLab to allow force push.
        """
        pass

    def push_refspecs(self) -> Optional[list[str]]:
        """
        Optional hook: which refs to push, if not all of them.

        None (the default) means `git push --mirror`: every local ref is pushed
        and every remote ref without a local counterpart is deleted. Returning a
        list of refspecs restricts the push to those, which a destination needs
        when the server owns refs of its own that must not be pushed over or
        pruned away (see AzureDevOpsDestinationProvider).
        """
        return None
