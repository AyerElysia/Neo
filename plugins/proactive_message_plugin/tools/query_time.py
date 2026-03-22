"""查询时间工具。

为主动消息系统提供与时间感知一致的时间查询能力。
"""

from __future__ import annotations

from datetime import datetime

from src.core.components.base import BaseTool

from ..temporal import build_chinese_datetime


class QueryTimeTool(BaseTool):
    """查询当前时间。"""

    tool_name = "query_time"
    tool_description = "查询当前时间。当你需要知道现在是几点、什么时辰时调用此工具。"

    chatter_allow: list[str] = [
        "default_chatter",
        "kokoro_flow_chatter",
        "proactive_message_plugin",
    ]

    async def go_activate(self) -> bool:
        config = getattr(self.plugin, "config", None)
        if config is None:
            return True
        return bool(getattr(config.settings, "enabled", True))

    async def execute(self) -> tuple[bool, dict]:
        time_str = build_chinese_datetime(datetime.now())
        return True, {
            "current_time": time_str,
            "reminder": "时间已查询。现在你可以根据当前时间给出合适的问候或回应了。",
        }
