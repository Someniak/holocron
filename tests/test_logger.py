import pytest
from unittest.mock import patch
from holocron.logger import log

@patch("builtins.print")
def test_log_basic(mock_print):
    log("Test message")
    assert mock_print.called
    args, _ = mock_print.call_args
    assert "Test message" in args[0]
    # Check timestamp format roughly
    assert args[0].startswith("[")

@patch("builtins.print")
def test_log_verbose_mode_on(mock_print):
    # Should print
    log("Verbose message", verbose_only=True, is_verbose_mode=True)
    assert mock_print.called
    args, _ = mock_print.call_args
    assert "Verbose message" in args[0]

@patch("builtins.print")
def test_log_verbose_mode_off(mock_print):
    # Should NOT print
    log("Verbose message", verbose_only=True, is_verbose_mode=False)
    assert not mock_print.called
