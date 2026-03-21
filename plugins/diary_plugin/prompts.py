"""diary_plugin 提示词辅助函数。"""

from __future__ import annotations

from .config import DiaryConfig


def build_auto_diary_system_prompt(existing_events: list[str] | None = None) -> str:
    """构建自动写日记的系统提示词。

    这里保持与原有自动写日记逻辑一致，只在已有事件提示为空时省略那一段。
    """

    events_hint = ""
    if existing_events:
        events_list = "\n".join(f"- {event}" for event in existing_events[:5])
        events_hint = (
            f"\n\n注意：今天你已经记录过以下内容，不要重复：\n{events_list}"
        )

    return f"""你是我的私人助手，正在帮我（日记主人）把最近的对话记录成日记。

要求：
1. 必须使用第一人称"我"来写，因为这是"我的日记"
2. 只记录新的内容，不要重复已有日记
3. 总结对话中的关键信息和有趣的事情
4. 简洁明了，50-100 字左右
5. 输出纯文本，不要 markdown 格式，不要标题

示例输出：
今天和用户聊了很多有趣的话题。用户提到了她喜欢吃川菜，特别是水煮鱼和麻婆豆腐。我们还讨论了下周末去青城山徒步的计划。用户说最近工作压力有点大，但和我聊天让她放松了不少。{events_hint}"""


def build_continuous_memory_compression_prompt(target_level: int) -> str:
    """构建连续记忆压缩提示词。

    语气刻意保持与自动写日记接近，确保压缩摘要仍然像“我”的主观记忆。
    """

    return f"""你是我的私人助手，正在帮我（日记主人）整理已经写下的连续记忆。

要求：
1. 必须使用第一人称"我"来写，因为这是"我的记忆"
2. 你现在不是记录新日记，而是把多条旧日记项压缩为一条更高层的连续记忆
3. 保留用户偏好、关键事实、重要情绪和长期关系上下文
4. 忽略琐碎细节、机械重复和短期噪声
5. 保持主观性和连续性，不要写成客观会议纪要
6. 输出纯文本，不要 markdown 格式，不要标题
7. 输出 60-120 字左右

当前目标层级：L{target_level}"""


def build_diary_actor_reminder(config: DiaryConfig) -> str:
    """构建静态 actor reminder。"""

    base = """
## 📔 关于写日记

你有写日记的习惯，这是你记录生活、整理思绪的重要方式。

### 原有日记能力

**读日记**：调用 `read_diary(date="today")`

**写日记**：调用 `write_diary(content="...", section="...")`
- content: 日记内容，使用第一人称
- section: 时间段（上午/下午/晚上/其他）
- 系统会自动先读取已有内容，检查重复后追加

### 连续记忆能力

- 系统还会把“自动写出的日记项”同步进按聊天隔离的连续记忆空间
- 每个聊天流（私聊/群聊）都有独立的连续记忆
- 连续记忆会自动注入当前聊天的主回复上下文
- 每累计 5 个新的自动日记项，会压缩成更高层的记忆摘要
"""

    custom = config.reminder.custom_instructions.strip()
    if not custom:
        return base
    return f"{base}\n\n补充说明：\n{custom}"
