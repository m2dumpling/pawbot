"""Runtime log visibility controls shared by CLI commands."""

from loguru import logger

__all__ = ["_set_pawbot_logs"]


def _set_pawbot_logs(enabled: bool) -> None:
    if enabled:
        logger.enable("pawbot")
    else:
        logger.disable("pawbot")
