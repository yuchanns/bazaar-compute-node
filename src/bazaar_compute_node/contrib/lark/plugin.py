from __future__ import annotations

import os

from ...core.channel import ChannelContext, IChannel, IChannelBuilder
from .channel import LarkChannel

_REGION_BASE_URLS = {
    "feishu": "https://open.feishu.cn",
    "lark": "https://open.larksuite.com",
}


class LarkBuilder(IChannelBuilder):
    def build(self, context: ChannelContext) -> IChannel:
        app_id = _required_text(context.options.get("app_id"), "app_id")
        app_secret_env = _required_text(
            context.options.get("app_secret_env"), "app_secret_env"
        )
        region = context.options.get("region", "feishu")
        if not isinstance(region, str) or region not in _REGION_BASE_URLS:
            raise ValueError("agent.channel.region must be feishu or lark")
        app_secret = os.environ.get(app_secret_env)
        if app_secret is None or not app_secret.strip():
            raise ValueError(
                f"lark credential environment is missing: {app_secret_env}"
            )
        if context.timer_wheel is None:
            raise ValueError("lark channel requires a timer wheel")
        return LarkChannel(
            context,
            app_id=app_id,
            app_secret=app_secret,
            region=region,
            base_url=_REGION_BASE_URLS[region],
            timer_wheel=context.timer_wheel,
        )


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"agent.channel.{field_name} is required for lark")
    if "\r" in value or "\n" in value:
        raise ValueError(f"agent.channel.{field_name} must not contain line breaks")
    return value


builder = LarkBuilder()


__all__ = ["LarkBuilder", "builder"]
