import sys
from unittest.mock import patch

import pytest

from holocron.config import parse_args


def test_parse_args_defaults():
    with patch.object(sys, "argv", ["g2g.py"]):
        args = parse_args()
        assert args.concurrency == 5
        assert args.backup_only is False
        assert args.checkout is False
        assert args.watch is False
        assert args.interval == 60
        assert args.include is None
        assert args.exclude is None
        assert args.cleanup is False


def test_parse_args_overrides():
    test_args = [
        "g2g.py",
        "--concurrency",
        "10",
        "--backup-only",
        "--checkout",
        "--watch",
        "--interval",
        "30",
        "--include",
        "repo-*",
        "--exclude",
        "test-*",
        "--cleanup",
    ]
    with patch.object(sys, "argv", test_args):
        args = parse_args()
        assert args.concurrency == 10
        assert args.backup_only is True
        assert args.checkout is True
        assert args.watch is True
        assert args.interval == 30
        assert args.include == ["repo-*"]
        assert args.exclude == ["test-*"]
        assert args.cleanup is True


def test_parse_args_multiple_include():
    test_args = ["g2g.py", "--include", "repo-*", "--include", "lib-*"]
    with patch.object(sys, "argv", test_args):
        args = parse_args()
        assert args.include == ["repo-*", "lib-*"]


def test_parse_args_validation_interval():
    test_args = ["g2g.py", "--interval", "0"]
    with patch.object(sys, "argv", test_args), pytest.raises(SystemExit):
        parse_args()


def test_parse_args_validation_concurrency():
    test_args = ["g2g.py", "--concurrency", "-1"]
    with patch.object(sys, "argv", test_args), pytest.raises(SystemExit):
        parse_args()


def test_parse_args_validation_window():
    test_args = ["g2g.py", "--window", "0"]
    with patch.object(sys, "argv", test_args), pytest.raises(SystemExit):
        parse_args()


@patch.dict("os.environ", {"HOLOCRON_INCLUDE": "repo-*,lib-*"})
def test_parse_args_include_from_env():
    with patch.object(sys, "argv", ["g2g.py"]):
        args = parse_args()
        assert args.include == ["repo-*", "lib-*"]


@patch.dict("os.environ", {"HOLOCRON_EXCLUDE": "test-*,tmp-*"})
def test_parse_args_exclude_from_env():
    with patch.object(sys, "argv", ["g2g.py"]):
        args = parse_args()
        assert args.exclude == ["test-*", "tmp-*"]
