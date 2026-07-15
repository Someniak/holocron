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

@dataclass
class PullRequestEvent:
    """
    A GitHub `pull_request` webhook, distilled to the fields the CI bridge needs.

    Kept separate from Repository because a PR carries data Repository has no room
    for: the PR number, the head/base refs, the head commit SHA (the key both the
    GitLab pipeline and the GitHub commit status hang off), and whether the head
    lives in a fork (untrusted code we must not run on internal runners by default).
    """
    action: str            # opened | synchronize | reopened | closed
    number: int
    repo_full_name: str    # "owner/repo" - target of the GitHub commit-status write
    repo_name: str         # bare/short name - keys the local mirror dir & GitLab path
    clone_url: str         # base repo clone_url (source fetch + host pinning)
    head_sha: str          # PR head commit; GitHub status + GitLab pipeline align on it
    head_ref: str
    base_ref: str          # PR base branch; the GitLab MR target
    is_fork: bool = False  # head repo != base repo
    merged: bool = False   # only meaningful when action == "closed"

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
