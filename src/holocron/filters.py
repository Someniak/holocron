from fnmatch import fnmatchcase

from .logger import logger


def parse_patterns(value):
    """
    Splits a comma-separated pattern string into a clean list.

    Accepts None/empty (-> []) so callers can pass raw env vars straight in.
    """
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def read_pattern_file(path):
    """
    Reads one pattern per line from a file.

    Blank lines and `#` comments are ignored, so a list can be annotated. A
    missing or unreadable file is fatal: silently mirroring *everything*
    because a `--repo-list` path was mistyped is far worse than stopping.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:
        raise ValueError(f"cannot read repository list {path!r}: {exc}") from exc

    patterns = []
    for line in lines:
        entry = line.split("#", 1)[0].strip()
        if entry:
            patterns.append(entry)
    return patterns


class RepoFilter:
    """
    Selects which of a source's repositories are actually mirrored.

    Patterns are shell-style globs (`*`, `?`, `[seq]`) matched case-insensitively
    against both the repository's mirror name ("widgets") and its fully-qualified
    source path when it has one ("acme/widgets" on GitHub, "Project/Widgets Repo"
    on Azure DevOps) -- so `acme/*` and `widgets` both work, and a whole Azure
    DevOps project can be selected with `MyProject/*`.

    With no include patterns everything is included. Excludes are applied after
    includes and always win.
    """

    def __init__(self, include=None, exclude=None):
        self.include = [p.lower() for p in (include or [])]
        self.exclude = [p.lower() for p in (exclude or [])]

    def __bool__(self):
        """True when the filter would actually remove anything."""
        return bool(self.include or self.exclude)

    @classmethod
    def from_args(cls, args):
        """
        Builds a filter from parsed CLI args.

        `--include` / `--exclude` are repeatable *and* accept comma-separated
        values; `--repo-list` contributes further include patterns from a file.
        """
        include = []
        for value in getattr(args, "include", None) or []:
            include.extend(parse_patterns(value))

        exclude = []
        for value in getattr(args, "exclude", None) or []:
            exclude.extend(parse_patterns(value))

        repo_list = getattr(args, "repo_list", None)
        if repo_list:
            include.extend(read_pattern_file(repo_list))

        return cls(include=include, exclude=exclude)

    def _candidates(self, repo):
        """The strings a pattern may match against, lower-cased."""
        values = [getattr(repo, "name", None), getattr(repo, "full_name", None)]
        return [v.lower() for v in values if v]

    def matches(self, repo):
        """True if `repo` should be mirrored."""
        candidates = self._candidates(repo)
        if not candidates:
            return False

        if self.include and not any(
            fnmatchcase(candidate, pattern)
            for pattern in self.include
            for candidate in candidates
        ):
            return False

        return not any(
            fnmatchcase(candidate, pattern)
            for pattern in self.exclude
            for candidate in candidates
        )

    def apply(self, repos, context="source"):
        """
        Returns the repositories that pass the filter, logging what was dropped.

        A filter that matches nothing is almost always a typo, so it is reported
        as a warning rather than quietly mirroring an empty set.
        """
        if not self:
            return list(repos)

        kept = []
        dropped = 0
        for repo in repos:
            if self.matches(repo):
                kept.append(repo)
            else:
                dropped += 1
                logger.debug(f"Filtered out '{repo.name}'.")

        if dropped:
            logger.info(
                f"Repository filter: {len(kept)} of {len(repos)} repositories "
                f"from the {context} selected."
            )
        if repos and not kept:
            logger.warning(
                f"Repository filter excluded every one of the {len(repos)} repositories "
                f"from the {context}; check the --include/--exclude patterns."
            )
        return kept


def build_repo_filter(args):
    """
    Builds the RepoFilter for this run, or exits with a clear message.

    Pattern-file problems surface here (at startup) rather than mid-cycle.
    """
    try:
        return RepoFilter.from_args(args)
    except ValueError as exc:
        logger.error(f"CRITICAL: {exc}")
        raise SystemExit(1) from exc
