from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from journeydrive_mac_thinclient.config import Config


def configure_logging(config: Config) -> None:
    root = logging.getLogger()
    root.setLevel(config.log_level)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    file_handler = RotatingFileHandler(
        config.log_file, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)
