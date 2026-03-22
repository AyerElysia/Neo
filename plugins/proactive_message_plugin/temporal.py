"""主动消息插件的时间感知辅助函数。

这个模块把仓库根部的 `proactive_message_plugin.temporal` 重新导出到
正式插件包里，方便插件内部做相对导入。
"""

from proactive_message_plugin.temporal import *  # noqa: F401,F403
