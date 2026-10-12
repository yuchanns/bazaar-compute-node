"""An ordinary installed publisher using only core and standard-library logging."""

from contextlib import asynccontextmanager
lazy import logging

from bazaar_core import Context, Contrib


@asynccontextmanager
async def lifespan(ctx: Context):
    del ctx
    logger = logging.getLogger(__name__)
    logger.info("ordinary publisher started")
    try:
        yield
    finally:
        logger.info("ordinary publisher stopped")


publisher = Contrib(lifespan=lifespan)
