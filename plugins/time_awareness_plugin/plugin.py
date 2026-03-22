"""Time Awareness Plugin - 兼容占位模块。

原有时间感知能力已迁移到 proactive_message_plugin。
此插件保留仅用于兼容旧配置，不再注册任何 active 组件。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.components.base import BasePlugin
from src.core.components.loader import register_plugin

from .config import TimeAwarenessConfig

if TYPE_CHECKING:
    from src.core.models.stream import ChatStream


@register_plugin
class TimeAwarenessPlugin(BasePlugin):
    """时间感知插件兼容层。"""

    plugin_name = "time_awareness_plugin"
    plugin_version = "2.1.0"
    plugin_author = "Neo-MoFox Team"
    plugin_description = "兼容插件 - 时间感知能力已迁移到 proactive_message_plugin"
    configs = [TimeAwarenessConfig]

    def get_components(self) -> list[type]:
        """兼容层不再注册任何主动组件。"""
        return []

    async def on_plugin_loaded(self) -> None:
        """兼容层加载时只输出迁移提醒。"""
        config = self.config
        if not getattr(getattr(config, "settings", None), "enabled", True):
            return

        from src.app.plugin_system.api.log_api import get_logger

        logger = get_logger("time_awareness_plugin", display="时间感知插件")
        logger.warning(
            "time_awareness_plugin 已迁移到 proactive_message_plugin，当前仅保留兼容占位。"
        )

    async def on_plugin_unloaded(self) -> None:
        return None


async def on_plugin_loaded(plugin: TimeAwarenessPlugin) -> None:
    """插件加载入口。"""
    await plugin.on_plugin_loaded()
