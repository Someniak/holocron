import os
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


def test_parse_args_source_choices_include_azure():
    test_args = [
        'g2g.py',
        '--source', 'azure',
        '--azure-org-url', 'https://dev.azure.com/acme',
        '--azure-project', 'Proj',
    ]
    with patch.object(sys, 'argv', test_args):
        args = parse_args()
        assert args.source == "azure"
        assert args.azure_org_url == "https://dev.azure.com/acme"
        assert args.azure_project == "Proj"


def test_validate_config_returns_token_map():
    env = {"GITHUB_TOKEN": "gh", "GITLAB_TOKEN": "gl", "AZURE_DEVOPS_TOKEN": "az"}
    with patch.dict(os.environ, env, clear=True):
        tokens = validate_config("azure", "gitlab", azure_org_url="https://dev.azure.com/acme")
    assert tokens == {"github": "gh", "gitlab": "gl", "azure": "az"}


def test_validate_config_azure_source_requires_token():
    with patch.dict(os.environ, {"GITLAB_TOKEN": "gl"}, clear=True):
        with pytest.raises(SystemExit) as excinfo:
            validate_config("azure", "gitlab", azure_org_url="https://dev.azure.com/acme")
    assert excinfo.value.code == 1


def test_validate_config_azure_source_requires_org_url():
    env = {"AZURE_DEVOPS_TOKEN": "az", "GITLAB_TOKEN": "gl"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(SystemExit) as excinfo:
            validate_config("azure", "gitlab", azure_org_url=None)
    assert excinfo.value.code == 1


def test_validate_config_local_destination_needs_no_token():
    with patch.dict(os.environ, {"AZURE_DEVOPS_TOKEN": "az"}, clear=True):
        tokens = validate_config("azure", "local", backup_only=True,
                                 azure_org_url="https://dev.azure.com/acme")
    assert tokens["azure"] == "az"
