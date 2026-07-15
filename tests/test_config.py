import sys
import pytest
from unittest.mock import patch
from holocron.config import parse_args, validate_config

def test_parse_args_defaults():
    with patch.object(sys, 'argv', ['g2g.py']):
        args = parse_args()
        assert args.concurrency == 5
        assert args.backup_only is False
        assert args.checkout is False
        assert args.watch is False
        assert args.interval == 60

def test_parse_args_overrides():
    test_args = [
        'g2g.py',
        '--concurrency', '10',
        '--backup-only',
        '--checkout',
        '--watch',
        '--interval', '30'
    ]
    with patch.object(sys, 'argv', test_args):
        args = parse_args()
        assert args.concurrency == 10
        assert args.backup_only is True
        assert args.checkout is True
        assert args.watch is True
        assert args.interval == 30


# --- CI bridge flags + validation ---

def test_ci_bridge_flag_defaults_off():
    with patch.object(sys, 'argv', ['g2g.py']):
        args = parse_args()
        assert args.ci_bridge is False
        assert args.ci_status_context == "holocron/gitlab-ci"
        assert args.ci_poll_interval == 10
        assert args.ci_poll_timeout == 1800
        assert args.ci_allow_forks is False
        assert args.ci_branch_prefix == "holocron/pr-"


def test_ci_bridge_flags_parse():
    test_args = ['g2g.py', '--ci-bridge', '--webhook',
                 '--ci-status-context', 'ci/gitlab',
                 '--ci-poll-interval', '5', '--ci-poll-timeout', '600',
                 '--ci-allow-forks']
    with patch.object(sys, 'argv', test_args):
        args = parse_args()
        assert args.ci_bridge is True
        assert args.ci_status_context == "ci/gitlab"
        assert args.ci_poll_interval == 5
        assert args.ci_poll_timeout == 600
        assert args.ci_allow_forks is True


def test_ci_bridge_requires_webhook(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh")
    monkeypatch.setenv("GITLAB_TOKEN", "gl")
    with pytest.raises(SystemExit):
        validate_config("github", "gitlab", ci_bridge=True, webhook=False)


def test_ci_bridge_requires_both_tokens(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh")
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        validate_config("github", "gitlab", ci_bridge=True, webhook=True)


def test_ci_bridge_ok_with_both_tokens(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh")
    monkeypatch.setenv("GITLAB_TOKEN", "gl")
    gh, gl = validate_config("github", "gitlab", ci_bridge=True, webhook=True)
    assert gh == "gh" and gl == "gl"
