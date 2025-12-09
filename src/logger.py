from datetime import datetime

def log(message, verbose_only=False, is_verbose_mode=False):
    """Simple logger that checks if we are in verbose mode."""
    if verbose_only and not is_verbose_mode:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")
