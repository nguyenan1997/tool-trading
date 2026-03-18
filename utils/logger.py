"""
utils/logger.py
Cấu hình Logging tập trung.
"""
import logging
import config

def setup_logger():
    # Kiểm tra xem logger đã được cấu hình chưa để tránh bị duplicate handlers
    if len(logging.getLogger().handlers) > 0:
        return

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
