import logging
import sys
from logging.handlers import RotatingFileHandler
from utils.paths import LOG_DIR, ensure_runtime_dirs

def setup_logger(name: str, log_file: str = None, level=logging.INFO):
    """Function to setup as many loggers as you want"""

    ensure_runtime_dirs()

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Prevent duplicate handlers if the logger is already initialized
    if logger.handlers:
        return logger

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler
    if log_file:
        file_path = LOG_DIR / log_file
        file_handler = RotatingFileHandler(
            file_path, maxBytes=10*1024*1024, backupCount=5
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger

# Default logger for the project
logger = setup_logger("FlowMind", "flowmind.log")
