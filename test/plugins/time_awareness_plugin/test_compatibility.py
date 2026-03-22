"""time_awareness_plugin 兼容层测试。"""

from __future__ import annotations

from plugins.time_awareness_plugin.config import TimeAwarenessConfig
from plugins.time_awareness_plugin.plugin import TimeAwarenessPlugin


def test_time_awareness_plugin_is_compatibility_only() -> None:
    """旧时间插件不应再注册任何组件。"""
    plugin = TimeAwarenessPlugin(TimeAwarenessConfig())
    assert plugin.get_components() == []
