# Copyright (c) 2025 sprouee
import logging


def configure_logging() -> logging.Logger:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        level=logging.INFO,
    )
    return logging.getLogger("wizardbot")


log = configure_logging()


