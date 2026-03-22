"""查询时间工具测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugins.proactive_message_plugin.tools.query_time import QueryTimeTool


def test_query_time_tool_returns_chinese_time() -> None:
    """查询时间工具应返回中式时间描述。"""
    plugin = SimpleNamespace(
        config=SimpleNamespace(settings=SimpleNamespace(enabled=True))
    )
    tool = QueryTimeTool(plugin)

    ok, result = asyncio.run(tool.execute())

    assert ok is True
    assert "current_time" in result
    assert "reminder" in result
    assert "年" in result["current_time"]


def test_query_time_tool_respects_enabled_flag() -> None:
    """禁用配置时工具不应激活。"""
    plugin = SimpleNamespace(
        config=SimpleNamespace(settings=SimpleNamespace(enabled=False))
    )
    tool = QueryTimeTool(plugin)

    assert asyncio.run(tool.go_activate()) is False
