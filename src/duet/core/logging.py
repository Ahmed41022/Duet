"""Console logging configured by Duet entry points."""

import logging

from duet.core.config import Config


def configure_logging(config: Config) -> None:
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
