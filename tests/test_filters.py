import argparse
from unittest.mock import patch

import pytest

from holocron.filters import RepoFilter, build_repo_filter, parse_patterns, read_pattern_file
from holocron.providers.base import Repository


def repo(name, full_name=None):
    return Repository(name=name, clone_url=f"https://host/{name}.git", full_name=full_name)


def args(**kwargs):
    kwargs.setdefault("include", None)
    kwargs.setdefault("exclude", None)
    kwargs.setdefault("repo_list", None)
    return argparse.Namespace(**kwargs)


# --- pattern parsing ---------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("api,web", ["api", "web"]),
    (" api , web ", ["api", "web"]),
    ("api,,web,", ["api", "web"]),
    ("", []),
    (None, []),
])
def test_parse_patterns(value, expected):
    assert parse_patterns(value) == expected


def test_read_pattern_file_ignores_comments_and_blanks(tmp_path):
    path = tmp_path / "repos.txt"
    path.write_text("# the important ones\napi\n\n  web  # inline comment\nacme/tools\n")
    assert read_pattern_file(str(path)) == ["api", "web", "acme/tools"]


def test_read_pattern_file_missing_is_an_error(tmp_path):
    """Mistyping --repo-list must not silently fall through to 'mirror everything'."""
    with pytest.raises(ValueError, match="cannot read repository list"):
        read_pattern_file(str(tmp_path / "nope.txt"))


def test_build_repo_filter_exits_on_bad_file(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        build_repo_filter(args(repo_list=str(tmp_path / "nope.txt")))
    assert excinfo.value.code == 1


# --- matching ----------------------------------------------------------------

def test_no_patterns_matches_everything():
    f = RepoFilter()
    assert not f  # falsy: nothing would be removed
    assert f.matches(repo("anything"))
    repos = [repo("a"), repo("b")]
    assert f.apply(repos) == repos


def test_include_is_an_allowlist():
    f = RepoFilter(include=["api", "web-*"])
    assert f.matches(repo("api"))
    assert f.matches(repo("web-frontend"))
    assert not f.matches(repo("docs"))


def test_exclude_subtracts():
    f = RepoFilter(exclude=["*-archive"])
    assert f.matches(repo("api"))
    assert not f.matches(repo("api-archive"))


def test_exclude_wins_over_include():
    f = RepoFilter(include=["api*"], exclude=["api-archive"])
    assert f.matches(repo("api"))
    assert not f.matches(repo("api-archive"))


def test_matches_full_name_too():
    """'owner/repo' patterns select whole orgs; Azure uses 'Project/Repo'."""
    f = RepoFilter(include=["acme/*"])
    assert f.matches(repo("api", full_name="acme/api"))
    assert not f.matches(repo("api", full_name="other/api"))


def test_matching_is_case_insensitive():
    f = RepoFilter(include=["API"])
    assert f.matches(repo("api"))
    assert RepoFilter(include=["api"]).matches(repo("API"))


def test_repo_without_name_is_excluded():
    assert not RepoFilter(include=["*"]).matches(Repository(name="", clone_url="x"))


# --- apply -------------------------------------------------------------------

def test_apply_selects_and_preserves_order():
    repos = [repo("api"), repo("docs"), repo("web")]
    assert [r.name for r in RepoFilter(include=["api", "web"]).apply(repos)] == ["api", "web"]


@patch("holocron.filters.logger")
def test_apply_warns_when_everything_is_excluded(mock_logger):
    """An all-excluding filter is almost always a typo -- say so, loudly."""
    assert RepoFilter(include=["nothing-matches"]).apply([repo("api")]) == []
    mock_logger.warning.assert_called_once()
    assert "excluded every one" in mock_logger.warning.call_args[0][0]


# --- from_args ---------------------------------------------------------------

def test_from_args_merges_repeated_and_comma_separated():
    f = RepoFilter.from_args(args(include=["api,web", "docs"], exclude=["*-archive"]))
    assert f.include == ["api", "web", "docs"]
    assert f.exclude == ["*-archive"]


def test_from_args_reads_repo_list_file(tmp_path):
    path = tmp_path / "repos.txt"
    path.write_text("api\nweb\n")
    f = RepoFilter.from_args(args(include=["docs"], repo_list=str(path)))
    assert f.include == ["docs", "api", "web"]


def test_from_args_tolerates_missing_attributes():
    """Hand-built Namespaces in older tests must not break the filter."""
    f = RepoFilter.from_args(argparse.Namespace())
    assert not f
