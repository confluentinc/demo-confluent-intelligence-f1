"""
Shared logging configuration utilities.
"""

import logging


def setup_logging(verbose: bool = False, default_level: str = "INFO") -> logging.Logger:
    """
    Set up logging configuration with consistent formatting.

    Args:
        verbose: Enable verbose (DEBUG) logging if True
        default_level: Default logging level when verbose=False

    Returns:
        Logger instance for the calling module
    """
    if verbose:
        level = logging.DEBUG
    else:
        level = getattr(logging, default_level.upper(), logging.INFO)

    logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(message)s")

    return logging.getLogger(__name__)
