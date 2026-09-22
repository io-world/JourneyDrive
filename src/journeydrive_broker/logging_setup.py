from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler


def configure_logging(log_file: str = "journeydrive-broker.log", log_level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(log_level)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)
