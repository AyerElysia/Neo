"""ChatTaskAgent - 聊天任务执行器（MVP）。"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from src.core.components import BaseAgent
from src.core.config import get_model_config
from src.kernel.llm import LLMPayload, LLMRequest, ROLE, Text
from src.kernel.logger import get_logger

from ..services import get_soul_service, get_workspace_service

logger = get_logger("anysoul.chat_task_agent")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


class ChatTaskAgent(BaseAgent):
    """任务态聊天 Agent。

    只负责执行“聊天任务”的内容生成与结果概览，
    发送动作由 default_chatter 的聊天态执行器负责。
    """

    agent_name = "anysoul_chat_task_agent"
    agent_description = "执行聊天任务：生成回复并派发给聊天态执行发送。"

    usables = ["default_chatter:agent:task_chat_executor"]

    async def execute(
        self,
        task_id: Annotated[str, "任务 ID"],
        task_mark: Annotated[str, "任务标记"],
        objective: Annotated[str, "任务目标"],
        event: Annotated[dict[str, Any], "来源事件"],
        recent_events: Annotated[list[dict[str, Any]] | None, "近期事件摘要列表"] = None,
        dry_run: Annotated[bool, "是否只生成不发送"] = False,
    ) -> tuple[bool, dict[str, Any]]:
        recent_events = recent_events or []
        message = event.get("message", {})

        context_segments, expression_summary, mode = await self._generate_reply(
            objective=objective,
            event=event,
            recent_events=recent_events,
        )
        reply_text = "\n".join(context_segments)

        sent = False
        send_detail = ""
        send_via = "none"
        send_call: dict[str, Any] = {}

        if not dry_run and context_segments:
            try:
                source_message = message if isinstance(message, dict) else None
                sent, send_result = await self.execute_local_usable(
                    "task_chat_executor",
                    task_id=task_id,
                    task_mark=task_mark,
                    context=context_segments,
                    reply_to=self._extract_reply_to(event),
                    source_message=source_message,
                )
                if isinstance(send_result, dict):
                    send_call_obj = send_result.get("send_call", {})
                    if isinstance(send_call_obj, dict):
                        send_call = send_call_obj
                    send_detail = str(send_result.get("send_detail", send_result))
                else:
                    send_detail = str(send_result)
                send_via = "default_chatter:agent:task_chat_executor"
            except Exception as exc:
                sent = False
                send_detail = f"send_text 调用异常: {exc}"
                logger.error(
                    f"任务发送失败: task_id={task_id}, task_mark={task_mark}, error={exc}",
                    exc_info=True,
                )
        elif not dry_run:
            send_detail = "context_empty"
        elif dry_run:
            send_detail = "dry_run=true，未实际发送"

        result = {
            "task_id": task_id,
            "task_mark": task_mark,
            "mode": mode,
            "sent": sent,
            "context": context_segments,
            "segment_count": len(context_segments),
            "expression_summary": expression_summary,
            "reply_length": len(reply_text),
            "send_detail": send_detail,
            "send_call": send_call,
            "send_via": send_via,
            "error": "" if sent else send_detail,
        }

        success = True if dry_run else sent
        return success, result

    async def _generate_reply(
        self,
        objective: str,
        event: dict[str, Any],
        recent_events: list[dict[str, Any]],
    ) -> tuple[list[str], str, str]:
        message = event.get("message", {})
        user_text = ""
        sender_name = "对方"
        if isinstance(message, dict):
            user_text = str(message.get("text", "") or "")
            sender_name = str(message.get("sender_name", "") or "对方")

        soul_text = await get_soul_service().read()
        memory_context = await self._load_memory_context()
        recent_block = self._format_recent_events(recent_events)
        memory_block = memory_context or "（暂无）"

        system_prompt = """你是“同一个人”的聊天任务态分身，不是外包工具。
你与主意识身份完全一致：价值观、语气、关系连续性必须保持一致。
你现在只是进入“聊天任务态”，并没有切换人格。

硬性要求：
1. 你的表达必须延续同一人格，不得把自己描述为机器、系统、执行器或无情工具。
2. 回复要自然、有人味、不过度官腔，优先回应对方最新诉求。
3. 你会按内部协议返回 JSON，便于上层执行发送；这是内部格式，不影响你的人格一致性。
4. JSON 字段：
   - context: 要发送给用户的文本段数组（list[string]，每个元素一段消息）
   - expression_summary: 对“表达了什么”的概览（不含原文，20~60字）
5. context 必须是数组；即使只发一段也要返回单元素数组。
6. 只输出 JSON，不要输出 markdown 代码块。

发送工具协议说明（你只需提供 context，上层会自动调用发送工具）：
- 上层会执行：action-send_text(content=context, reply_to=可选)
- context 的每个元素会被分条发送，顺序即发送顺序
- context 里只能写给用户看的正文，不要混入 reason/thought 或其他元信息

中枢传来的 objective 只是补充信息，不是成品台词：
- 它可能包含中枢当下的忙碌、疲惫、刚刚在做什么、想顺口分享的小情绪
- 它也可能包含对回复氛围的补充建议
- 你要自己决定具体说什么，不要照抄 objective，也不要把 objective 当作固定模板
"""

        persona_system_prompt = f"""以下是你在当前阶段必须遵循的系统级稳定上下文。

[Soul]
{_truncate(soul_text, 2200)}

[Memory]
{_truncate(memory_block, 2200)}
"""

        user_prompt = f"""中枢补充信息：
{objective}

说明：上面的内容只是中枢补充信息，只作为状态参考，不是必须照着说的固定台词。

发送者：
{sender_name}

最新消息：
{user_text}

近期事件摘要（中枢上下文）：
{recent_block}
"""

        try:
            model_set = get_model_config().get_task("actor")
            request = LLMRequest(model_set=model_set, request_name="anysoul_chat_task_agent")
            request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt)))
            request.add_payload(LLMPayload(ROLE.SYSTEM, Text(persona_system_prompt)))
            request.add_payload(LLMPayload(ROLE.USER, Text(user_prompt)))

            response = await request.send(stream=False)
            await response
            raw = (response.message or "").strip()
            parsed = self._try_parse_json(raw)

            if parsed:
                context_segments = self._extract_context_segments(parsed)
                reply_text = "\n".join(context_segments)
                expression_summary = str(parsed.get("expression_summary", "") or "").strip()
                if context_segments:
                    if not expression_summary:
                        expression_summary = self._build_fallback_summary(reply_text, user_text)
                    return context_segments, _truncate(expression_summary, 120), "llm"
        except Exception as exc:
            logger.warning(f"LLM 生成聊天任务回复失败，降级到 fallback: {exc}")

        fallback_reply = self._build_fallback_reply(user_text)
        fallback_summary = self._build_fallback_summary(fallback_reply, user_text)
        return [fallback_reply], fallback_summary, "fallback"

    async def _load_memory_context(self) -> str:
        workspace = get_workspace_service()
        content = await workspace.read_file("memory.md")
        if not content:
            return ""
        return " ".join(content.split())

    def _format_recent_events(self, recent_events: list[dict[str, Any]]) -> str:
        if not recent_events:
            return "- （暂无）"
        lines = []
        for item in recent_events[-8:]:
            preview = str(item.get("preview", "") or "")
            decision = str(item.get("decision", "") or "")
            summary = str(item.get("expression_summary", "") or "")
            lines.append(
                f"- [{decision}] {_truncate(preview, 50)}"
                f"{' | ' + _truncate(summary, 36) if summary else ''}"
            )
        return "\n".join(lines)

    def _try_parse_json(self, raw: str) -> dict[str, Any] | None:
        raw = raw.strip()
        if not raw:
            return None

        block_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
        candidate = block_match.group(1).strip() if block_match else raw

        try:
            data = json.loads(candidate)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    @staticmethod
    def _extract_context_segments(data: dict[str, Any]) -> list[str]:
        context = data.get("context")
        if isinstance(context, list):
            segments = [item.strip() for item in context if isinstance(item, str) and item.strip()]
            if segments:
                return segments
        elif isinstance(context, str):
            one = context.strip()
            if one:
                return [one]

        # 兼容旧协议 reply_text
        reply_text = data.get("reply_text")
        if isinstance(reply_text, str):
            one = reply_text.strip()
            if one:
                return [one]
        return []

    def _build_fallback_reply(self, user_text: str) -> str:
        text = user_text.strip()
        if not text:
            return "我在。"
        if len(text) <= 24:
            return f"收到，我看到你说的了：{text}"
        return f"我明白你的意思了，核心是：{_truncate(text, 36)}"

    def _build_fallback_summary(self, reply_text: str, user_text: str) -> str:
        return (
            "简要确认并回应了对方的最新消息，"
            f"重点围绕“{_truncate(user_text.strip(), 24)}”进行表达。"
            if user_text.strip()
            else f"给出了一条简短回应，内容长度约 {len(reply_text)} 字。"
        )

    def _extract_reply_to(self, event: dict[str, Any]) -> str | None:
        message = event.get("message", {})
        if not isinstance(message, dict):
            return None

        reply_to = message.get("reply_to")
        if isinstance(reply_to, str) and reply_to.strip():
            value = reply_to.strip()
            lowered = value.lower()
            if lowered in {"0", "none", "null", "nil"}:
                return None
            return value
        if isinstance(reply_to, int) and reply_to > 0:
            return str(reply_to)
        return None
