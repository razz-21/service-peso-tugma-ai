import logging
import sys

from app.core.config import settings


def configure_logging() -> None:
    level = logging.DEBUG if settings.APP_DEBUG else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
