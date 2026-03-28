"""DefaultChatter 任务态执行 Agent。

负责接收中枢派发的聊天任务并执行最终发送动作。
"""

from __future__ import annotations

from uuid import uuid4
from typing import Annotated, Any

from src.core.components import BaseAgent
from src.core.managers.stream_manager import get_stream_manager
from src.core.models.message import Message, MessageType
from src.kernel.logger import get_logger

logger = get_logger("default_chatter.task_chat_executor")


class TaskChatExecutorAgent(BaseAgent):
    """聊天态任务执行器。

    本组件只负责执行发送，不参与上层决策。
    """

    agent_name = "task_chat_executor"
    agent_description = "聊天态任务执行器：接收任务并发送文本消息。"

    usables = ["default_chatter:action:send_text"]

    async def execute(
        self,
        task_id: Annotated[str, "任务 ID"],
        task_mark: Annotated[str, "任务标识标记"],
        context: Annotated[
            list[str] | str | None,
            "要发送的上下文文本段。推荐 list[str]，每个元素为一段消息。",
        ] = None,
        reply_text: Annotated[str | None, "兼容旧协议：单段回复文本"] = None,
        reply_to: Annotated[str | None, "可选，引用回复目标消息 ID"] = None,
        source_message: Annotated[dict[str, Any] | None, "来源消息快照"] = None,
    ) -> tuple[bool, dict[str, Any]]:
        context_segments = self._normalize_context_segments(context, reply_text)
        if not context_segments:
            return False, {
                "task_id": task_id,
                "task_mark": task_mark,
                "sent": False,
                "error": "context_empty",
            }

        await self._prime_stream_context(source_message)
        await self._log_send_target(task_id, task_mark)

        send_call = {
            "name": "action-send_text",
            "arguments": {
                "content": context_segments,
                "reply_to": reply_to,
            },
        }
        sent, send_result = await self.execute_local_usable(
            "action-send_text",
            content=context_segments,
            reply_to=reply_to,
        )

        logger.info(
            f"[任务追踪] 聊天态执行发送 task_id={task_id}, task_mark={task_mark}, "
            f"segments={len(context_segments)}, sent={bool(sent)}"
        )

        joined_length = sum(len(item) for item in context_segments)
        return bool(sent), {
            "task_id": task_id,
            "task_mark": task_mark,
            "sent": bool(sent),
            "segment_count": len(context_segments),
            "reply_length": joined_length,
            "send_detail": str(send_result),
            "send_call": send_call,
        }

    @staticmethod
    def _normalize_context_segments(
        context: list[str] | str | None,
        reply_text: str | None = None,
    ) -> list[str]:
        if isinstance(context, list):
            segments = [item.strip() for item in context if isinstance(item, str) and item.strip()]
            if segments:
                return segments
        elif isinstance(context, str):
            one = context.strip()
            if one:
                return [one]

        fallback = str(reply_text or "").strip()
        return [fallback] if fallback else []

    async def _prime_stream_context(self, source_message: dict[str, Any] | None) -> None:
        """按原始 DFC 发送逻辑补齐 stream 上下文，确保 send_text 定位到正确目标。"""
        if not isinstance(source_message, dict):
            return

        stream_id = str(source_message.get("stream_id", "") or self.stream_id)
        platform = str(source_message.get("platform", "") or "qq")
        chat_type = str(source_message.get("chat_type", "") or "private")
        sender_id = str(
            source_message.get("sender_id")
            or source_message.get("user_id")
            or ""
        )
        sender_name = str(source_message.get("sender_name", "") or "")
        text = str(source_message.get("text", "") or "")
        reply_to = source_message.get("reply_to")
        reply_to_value = str(reply_to).strip() if isinstance(reply_to, str) else None
        meta = source_message.get("meta", {})
        if not isinstance(meta, dict):
            meta = {}

        group_id = str(
            meta.get("group_id")
            or source_message.get("group_id")
            or ""
        )
        group_name = str(
            meta.get("group_name")
            or source_message.get("group_name")
            or ""
        )

        sm = get_stream_manager()
        stream = await sm.get_or_create_stream(
            stream_id=stream_id,
            platform=platform,
            user_id=sender_id if chat_type != "group" else "",
            group_id=group_id if chat_type == "group" else "",
            chat_type=chat_type,
        )

        stream.context.triggering_user_id = sender_id or stream.context.triggering_user_id

        trigger = Message(
            message_id=f"anysoul_task_trigger_{uuid4().hex}",
            content=text,
            processed_plain_text=text,
            message_type=MessageType.TEXT,
            sender_id=sender_id,
            sender_name=sender_name,
            platform=platform,
            chat_type=chat_type,
            stream_id=stream_id,
            reply_to=reply_to_value,
        )

        if chat_type == "group":
            if group_id:
                trigger.extra["group_id"] = group_id
                trigger.extra["target_group_id"] = group_id
            if group_name:
                trigger.extra["group_name"] = group_name
                trigger.extra["target_group_name"] = group_name
        else:
            if sender_id:
                trigger.extra["target_user_id"] = sender_id
            if sender_name:
                trigger.extra["target_user_name"] = sender_name

        stream.context.current_message = trigger

    async def _log_send_target(self, task_id: str, task_mark: str) -> None:
        sm = get_stream_manager()
        stream = await sm.get_or_create_stream(stream_id=self.stream_id)
        context = stream.context
        target_user_id = (
            context.triggering_user_id
            or (context.current_message.sender_id if context.current_message else "")
            or ""
        )
        chat_type = stream.chat_type or context.chat_type
        logger.info(
            f"[任务追踪] 发送定位 task_id={task_id}, task_mark={task_mark}, "
            f"chat_type={chat_type}, target_user_id={target_user_id or 'UNKNOWN'}"
        )
