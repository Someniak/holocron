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
