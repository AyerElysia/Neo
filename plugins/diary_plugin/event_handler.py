"""自动写日记事件处理器。

订阅对话事件，当消息数量达到阈值时自动总结并写入日记。
"""

from __future__ import annotations

from typing import Any

from src.core.components.base.event_handler import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from .config import DiaryConfig


logger = get_logger("diary_plugin")


class AutoDiaryEventHandler(BaseEventHandler):
    """自动写日记事件处理器。

    订阅 on_chatter_step 事件，计数对话消息数量，
    当达到配置的阈值时自动调用 LLM 总结对话并写入日记。

    工作流程：
    1. 每次 on_chatter_step 事件触发时，计数器 +1
    2. 当计数器达到阈值时，执行自动总结对话并写入日记
    3. 重置计数器，开始下一轮计数
    """

    handler_name: str = "auto_diary_handler"
    handler_description: str = (
        "自动写日记事件处理器 - 当对话达到一定数量时自动总结并写入日记"
    )
    weight: int = 5  # 中等权重，确保在合适时机执行

    init_subscribe: list[EventType | str] = [EventType.ON_CHATTER_STEP]

    # 消息计数器（按 stream_id 隔离）
    _message_counts: dict[str, int] = {}

    def __init__(self, plugin: Any) -> None:
        """初始化自动写日记事件处理器。

        Args:
            plugin: 所属插件实例
        """
        super().__init__(plugin)
        self._message_counts = {}

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行自动写日记检查。

        Args:
            event_name: 触发事件名称
            params: 事件参数（包含 stream_id 等信息）

        Returns:
            tuple[EventDecision, dict[str, Any]]: 决策和参数
        """
        # 检查自动写日记功能是否启用
        if not self._is_enabled():
            return EventDecision.SUCCESS, params

        # 获取 stream_id
        stream_id = params.get("stream_id")
        if not stream_id:
            logger.debug("未找到 stream_id，跳过自动写日记检查")
            return EventDecision.SUCCESS, params

        # 获取配置
        config = self._get_config()
        if config is None:
            return EventDecision.SUCCESS, params

        # 检查聊天类型（群聊是否触发自动写日记）
        if not self._allow_group_chat(params.get("chat_type"), config):
            logger.debug(f"[{stream_id[:8]}] 群聊不触发自动写日记，跳过")
            return EventDecision.SUCCESS, params

        threshold = config.auto_diary.message_threshold

        # 更新计数器
        current_count = self._message_counts.get(stream_id, 0) + 1
        self._message_counts[stream_id] = current_count

        logger.debug(f"[{stream_id[:8]}] 消息计数：{current_count}/{threshold}")

        # 检查是否达到阈值
        if current_count >= threshold:
            logger.info(
                f"[{stream_id[:8]}] 达到写日记阈值 ({current_count}/{threshold})，执行自动总结"
            )

            # 执行自动总结
            await self._auto_summary(stream_id, config.auto_diary.message_threshold)

            # 重置计数器
            self._message_counts[stream_id] = 0

        return EventDecision.SUCCESS, params

    async def _auto_summary(self, stream_id: str, summary_count: int) -> None:
        """执行自动总结并写入日记。

        Args:
            stream_id: 聊天流 ID
            summary_count: 总结最近 N 条对话
        """
        from src.core.managers import get_stream_manager
        from .service import DiaryService
        from src.app.plugin_system.api.service_api import get_service

        logger.info(f"[{stream_id[:8]}] 开始自动总结最近 {summary_count} 条对话")

        try:
            # 获取聊天流上下文
            stream_manager = get_stream_manager()
            chat_stream = stream_manager._streams.get(stream_id)

            if not chat_stream:
                logger.warning(f"无法获取聊天流：{stream_id}")
                return

            # 获取对话历史
            context = chat_stream.context
            all_messages = list(context.history_messages) + list(
                context.unread_messages
            )
            recent_messages = (
                all_messages[-summary_count:]
                if len(all_messages) > summary_count
                else all_messages
            )

            if not recent_messages:
                logger.warning("没有可用的对话历史")
                return

            # 格式化为文本
            history_lines = []
            for msg in recent_messages:
                sender = getattr(msg, "sender_name", "未知")
                content = getattr(
                    msg, "processed_plain_text", str(getattr(msg, "content", ""))
                )
                history_lines.append(f"{sender}: {content}")

            # 获取今天已有日记（传递给 LLM 避免重复）
            service = get_service("diary_plugin:service:diary_service")
            today_events = []
            if isinstance(service, DiaryService):
                today_content = service.read_today()
                if today_content.events:
                    today_events = [event.content for event in today_content.events]

            # 调用 LLM 总结（传入已有事件避免重复）
            summary = await self._llm_summarize(history_lines, today_events)
            if not summary:
                logger.warning("LLM 总结失败")
                return

            # 再次检查重复（双重保险）
            if self._is_duplicate(summary):
                logger.info("检测到重复内容，跳过写入")
                return

            # 写入日记
            success, message = await self._write_diary(summary)
            if success:
                logger.info(f"自动日记已写入：{summary[:30]}...")
            else:
                logger.warning(f"自动日记写入失败：{message}")

        except Exception as e:
            logger.error(f"自动总结失败：{e}", exc_info=True)

    async def _llm_summarize(
        self, chat_history: list[str], today_events: list[str] = None
    ) -> str | None:
        """调用 LLM 总结对话历史为第一人称日记。

        Args:
            chat_history: 对话历史列表
            today_events: 今天已有日记事件列表（用于避免重复）

        Returns:
            str | None: 总结的日记内容
        """
        from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text
        from src.core.config import get_model_config

        history_text = "\n".join(chat_history)

        # 构建已有事件提示（如果有）
        events_hint = ""
        if today_events:
            events_list = "\n".join(f"- {e}" for e in today_events[:5])  # 最多显示 5 条
            events_hint = (
                f"\n\n注意：今天你已经记录过以下内容，不要重复：\n{events_list}"
            )

        try:
            config = self._get_config()
            if config is None:
                logger.warning("无法获取日记插件配置")
                return None
            
            task_name = config.model.task_name
            model_config = get_model_config()
            model_set = model_config.get_task(task_name)
        except KeyError:
            logger.warning(f"未找到模型配置：{task_name}")
            return None

        if not model_set:
            return None

        request = LLMRequest(model_set, "auto_diary_summary")

        # 系统提示：引导 LLM 以第一人称总结，明确告知这是"我的日记"
        system_prompt = f"""你是我的私人助手，正在帮我（日记主人）把最近的对话记录成日记。

要求：
1. 必须使用第一人称"我"来写，因为这是"我的日记"
2. 只记录新的内容，不要重复已有日记
3. 总结对话中的关键信息和有趣的事情
4. 简洁明了，50-100 字左右
5. 输出纯文本，不要 markdown 格式，不要标题

示例输出：
今天和用户聊了很多有趣的话题。用户提到了她喜欢吃川菜，特别是水煮鱼和麻婆豆腐。我们还讨论了下周末去青城山徒步的计划。用户说最近工作压力有点大，但和我聊天让她放松了不少。{events_hint}"""

        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt)))
        request.add_payload(
            LLMPayload(
                ROLE.USER,
                Text(
                    f"请把以下对话内容写成一篇简短的日记（用'我'的口吻）：\n\n{history_text}"
                ),
            )
        )

        try:
            response = await request.send()
            # LLMResponse 可直接 await 获取完整文本，或访问 .message 属性
            summary = await response if not response.message else response.message
            if summary:
                summary = summary.strip().replace("**", "").replace("*", "")
                return summary if summary else None
        except Exception as e:
            logger.error(f"LLM 总结失败：{e}")

        return None

    def _is_duplicate(self, content: str, threshold: float = 0.5) -> bool:
        """检查总结内容是否与已有日记重复。

        Args:
            content: 总结内容
            threshold: 相似度阈值

        Returns:
            bool: 是否重复
        """
        from .service import DiaryService
        from src.app.plugin_system.api.service_api import get_service

        service = get_service("diary_plugin:service:diary_service")
        if not isinstance(service, DiaryService):
            return False

        today_content = service.read_today()
        if not today_content.events:
            return False

        existing_texts = [event.content for event in today_content.events]
        content_lower = content.lower().strip()

        if len(content_lower) < 5:
            return False

        for existing in existing_texts:
            existing_lower = existing.lower().strip()
            if not existing_lower:
                continue

            similarity = self._calculate_similarity(content_lower, existing_lower)
            if similarity > threshold:
                logger.debug(f"检测到重复内容，相似度：{similarity}")
                return True

        return False

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """计算 Jaccard 相似度。"""
        set1 = set(text1)
        set2 = set(text2)

        if not set1 or not set2:
            return 0.0

        intersection = len(set1 & set2)
        union = len(set1 | set2)

        return intersection / union if union > 0 else 0.0

    async def _write_diary(self, content: str) -> tuple[bool, str]:
        """写入日记。

        Args:
            content: 日记内容

        Returns:
            tuple[bool, str]: (success, message)
        """
        from .service import DiaryService
        from src.app.plugin_system.api.service_api import get_service

        service = get_service("diary_plugin:service:diary_service")
        if not isinstance(service, DiaryService):
            return False, "diary_service 未加载"

        # 自动选择时间段
        section = self._get_current_section()

        return service.append_entry(content=content, section=section)

    def _get_current_section(self) -> str:
        """根据当前时间获取时间段分类。"""
        from datetime import datetime

        hour = datetime.now().hour

        if 5 <= hour < 12:
            return "上午"
        elif 12 <= hour < 18:
            return "下午"
        elif 18 <= hour < 23:
            return "晚上"
        else:
            return "其他"

    def _is_enabled(self) -> bool:
        """检查自动写日记功能是否启用。"""
        config = self._get_config()
        if config is None:
            return False
        return config.auto_diary.enabled

    def _get_config(self) -> DiaryConfig | None:
        """获取插件配置。"""
        if isinstance(self.plugin.config, DiaryConfig):
            return self.plugin.config
        return None

    def reset_count(self, stream_id: str) -> None:
        """重置指定 stream_id 的计数器。

        Args:
            stream_id: 聊天流 ID
        """
        if stream_id in self._message_counts:
            self._message_counts[stream_id] = 0
            logger.debug(f"[{stream_id[:8]}] 计数器已重置")

    def _allow_group_chat(
        self, chat_type: str | None, config: DiaryConfig
    ) -> bool:
        """检查是否允许群聊触发自动写日记。

        Args:
            chat_type: 聊天类型字符串
            config: 插件配置

        Returns:
            bool: 是否允许
        """
        chat_type_raw = str(chat_type or "").lower()
        if chat_type_raw == "group":
            return config.auto_diary.allow_group_chat
        return True
