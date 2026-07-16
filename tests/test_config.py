import sys
import pytest
from unittest.mock import patch
from holocron.config import parse_args

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
