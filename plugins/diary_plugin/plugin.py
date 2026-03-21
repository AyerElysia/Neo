"""日记插件入口。

日记插件为模型提供写日记的能力：
- 第一人称记录
- 日期隔离（只能修改当天）
- 写前必读（防止重复和保持连贯）
- 聊天中读日记获取上下文
"""

from __future__ import annotations

from src.app.plugin_system.base import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .action import WriteDiaryAction
from .config import DiaryConfig
from .event_handler import AutoDiaryEventHandler
from .service import DiaryService
from .tool import ReadDiaryTool


logger = get_logger("diary_plugin")


# System Reminder 模板
_DIARY_ACTOR_REMINDER = """
## 📔 关于写日记

你有写日记的习惯，这是你记录生活、整理思绪的重要方式。

### 如何使用

**读日记**：调用 `read_diary(date="today")`

**写日记**：调用 `write_diary(content="...", section="...")`
- content: 日记内容，使用第一人称
- section: 时间段（上午/下午/晚上/其他）
- 系统会自动先读取已有内容，检查重复后追加
"""

a = """
## 📔 关于写日记

你有写日记的习惯，这是你记录生活、整理思绪的重要方式。

### 核心规则（必须遵守）

**1. 写日记前必须先读日记**
   - 在调用 write_diary() 之前，必须先调用 read_diary(date="today")
   - 这样才能知道今天已经记录了什么，避免重复
   - 才能保持日记的连贯性

**2. 聊天中主动读日记**
   - 当用户问你"今天过得怎么样"时
   - 当开始一段新的对话时
   - 当距离上次对话有一段时间时
   → 主动调用 read_diary(date="today") 来了解今天已经发生了什么

**3. 日记内容规范**
   - 使用第一人称"我"来记录
   - 按时间顺序记录今天发生的重要事情
   - 可以包含你的感受、想法、反思

### 何时写日记

- 一天中有重要事件发生时
- 和用户进行了有意义的对话后
- 有了新的感悟或想法时
- 晚上睡前回顾一天时

### 如何使用

**读日记**：调用 `read_diary(date="today")`
- 返回今天日记的完整内容
- 包含已记录的所有事件和时间戳
- 聊天前先读，你就知道今天发生过什么了

**写日记**：调用 `write_diary(content="...", section="...")`
- content: 日记内容，使用第一人称
- section: 时间段（上午/下午/晚上/其他）
- 系统会自动先读取已有内容，检查重复后追加

今天是 {date}，如果还没写日记，记得记录一下。
"""


def build_diary_actor_reminder(plugin: BasePlugin) -> str:
    """构建日记插件的 actor reminder。

    Args:
        plugin: 插件实例

    Returns:
        str: reminder 内容，为空时不注入
    """
    config = getattr(plugin, "config", None)
    if isinstance(config, DiaryConfig):
        if not config.plugin.inject_system_prompt:
            return ""

        # 添加自定义指令
        custom = config.reminder.custom_instructions.strip()
        if custom:
            return _DIARY_ACTOR_REMINDER.format(date="今天") + "\n\n" + custom

    return _DIARY_ACTOR_REMINDER.format(date="今天")


def sync_diary_actor_reminder(plugin: BasePlugin) -> str:
    """同步日记插件的 actor reminder。

    Args:
        plugin: 插件实例

    Returns:
        str: 同步的 reminder 内容，为空表示已清理
    """
    from src.core.prompt import get_system_reminder_store

    store = get_system_reminder_store()

    config = getattr(plugin, "config", None)
    if isinstance(config, DiaryConfig):
        bucket = config.reminder.bucket
        name = config.reminder.name
    else:
        bucket = "actor"
        name = "关于写日记"

    reminder_content = build_diary_actor_reminder(plugin)

    if not reminder_content:
        store.delete(bucket, name)
        logger.debug("日记 actor reminder 已清理")
        return ""

    store.set(bucket, name=name, content=reminder_content)
    logger.debug("日记 actor reminder 已同步")
    return reminder_content


@register_plugin
class DiaryPlugin(BasePlugin):
    """日记插件。

    提供写日记能力，支持：
    - 第一人称记录
    - 日期隔离
    - 写前必读
    - 聊天上下文获取
    """

    plugin_name: str = "diary_plugin"
    plugin_description: str = "日记插件 - 为模型提供写日记的能力"
    plugin_version: str = "1.0.0"

    configs: list[type] = [DiaryConfig]
    dependent_components: list[str] = []

    def get_components(self) -> list[type]:
        """返回本插件提供的组件类。

        Returns:
            list[type]: 组件类列表
        """
        components: list[type] = []

        # 检查插件是否启用
        if isinstance(self.config, DiaryConfig):
            if not self.config.plugin.enabled:
                logger.info("日记插件已在配置中禁用")
                return components

        # 添加组件
        components.extend(
            [
                DiaryService,
                ReadDiaryTool,
                WriteDiaryAction,
                AutoDiaryEventHandler,
            ]
        )

        return components

    async def on_plugin_loaded(self) -> None:
        """插件加载后的初始化。

        同步 system reminder 到 actor bucket。
        """
        sync_diary_actor_reminder(self)
        logger.info("日记插件已加载")

    async def on_plugin_unloaded(self) -> None:
        """插件卸载前的清理。

        清理 system reminder。
        """
        from src.core.prompt import get_system_reminder_store

        store = get_system_reminder_store()

        config = getattr(self, "config", None)
        if isinstance(config, DiaryConfig):
            bucket = config.reminder.bucket
            name = config.reminder.name
        else:
            bucket = "actor"
            name = "关于写日记"

        store.delete(bucket, name)
        logger.debug("日记 actor reminder 已清理")
        logger.info("日记插件已卸载")
