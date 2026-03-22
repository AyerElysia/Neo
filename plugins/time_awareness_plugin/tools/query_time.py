"""兼容导出：查询时间工具。"""

from plugins.proactive_message_plugin.tools.query_time import (  # noqa: F401
    QueryTimeTool,
    build_chinese_datetime,
)

__all__ = ["QueryTimeTool", "build_chinese_datetime"]
