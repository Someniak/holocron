import pytest
import logging
from unittest.mock import patch, MagicMock
from holocron.logger import setup_logger, log_execution, logger

def test_setup_logger_verbose():
    setup_logger(verbose=True)
    assert logger.level == logging.DEBUG
    # Reset for other tests
    setup_logger(verbose=False)

def test_setup_logger_default():
    setup_logger(verbose=False)
    assert logger.level == logging.INFO

@patch("holocron.logger.logger.debug")
@patch("holocron.logger.logger.isEnabledFor")
def test_log_execution_decorator(mock_is_enabled, mock_debug):
    mock_is_enabled.return_value = True
    
    @log_execution
    def test_func(a, b):
        return a + b
        
    result = test_func(1, 2)
    
    assert result == 3
    assert mock_debug.call_count >= 1
    # Check if arguments were logged
    args_str = str(mock_debug.call_args)
    assert "test_func" in args_str or "Executing" in args_str

@patch("holocron.logger.logger.debug")
@patch("holocron.logger.logger.isEnabledFor")
def test_log_execution_exception(mock_is_enabled, mock_debug):
    mock_is_enabled.return_value = True
    
    @log_execution
    def failing_func():
        raise ValueError("Oops")
        
    with pytest.raises(ValueError):
        failing_func()
        
    # Should have logged the exception
    assert mock_debug.called
    args_str = str(mock_debug.call_args_list)
    assert "Exception" in args_str
