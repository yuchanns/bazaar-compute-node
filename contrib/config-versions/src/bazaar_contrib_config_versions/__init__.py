"""Five ordinary entry points providing the published version chain."""

from contextlib import asynccontextmanager

from bazaar_contrib_config import CONFIG_V1, CONFIG_V2, CONFIG_V3, CONFIG_V4, CONFIG_V5
from bazaar_core import Contrib, ContribSpec
lazy from bazaar_contrib_config import ConfigConverter
lazy from bazaar_core import Context, ServiceKey

lazy from .converters import Converter

__all__ = ["VERSIONS", "Converter", "v1", "v2", "v3", "v4", "v5"]


def _version(
    number: str,
    key: ServiceKey[ConfigConverter],
    previous: ServiceKey[ConfigConverter] | None = None,
) -> Contrib:
    @asynccontextmanager
    async def lifespan(ctx: Context):
        ctx.offer(key, Converter(number, ctx.accept(previous) if previous else None))
        yield

    return Contrib(
        offers=[key], accepts=[previous] if previous else [], lifespan=lifespan
    )


v1 = _version("1", CONFIG_V1)
v2 = _version("2", CONFIG_V2, CONFIG_V1)
v3 = _version("3", CONFIG_V3, CONFIG_V2)
v4 = _version("4", CONFIG_V4, CONFIG_V3)
v5 = _version("5", CONFIG_V5, CONFIG_V4)

VERSIONS = tuple(
    ContribSpec(
        contrib=f"config-v{number}",
        location="bazaar-contrib-config-versions",
        required_offers=[key],
    )
    for number, key in (
        ("1", CONFIG_V1),
        ("2", CONFIG_V2),
        ("3", CONFIG_V3),
        ("4", CONFIG_V4),
        ("5", CONFIG_V5),
    )
)
