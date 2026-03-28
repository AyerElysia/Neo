"""Default Chatter 执行器模块。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import AsyncGenerator, TypeGuard

from src.core.components.base import Wait, Success, Failure, Stop
from src.core.models.message import Message
from src.core.models.stream import ChatStream
from src.core.prompt import get_system_reminder_store
from src.kernel.logger import Logger
from src.kernel.llm import LLMPayload, ROLE, Text
from src.kernel.prompt_snapshot import write_prompt_snapshot

from .type_defs import DefaultChatterRuntime, LLMConversationState, LLMResponseLike
from .tool_flow import append_suspend_payload_if_action_only, process_tool_calls

# LLM 返回纯文本（非 tool call）时的最大重试次数
_MAX_PLAIN_TEXT_RETRIES = 0

# 重试时注入的提醒文本
_PLAIN_TEXT_RETRY_REMINDER = (
    "（系统提示：你刚才返回了纯文本而非工具调用。"
    "请务必通过工具调用来完成任务，不要直接输出文字回复。）"
)


def _build_reminder_text() -> str:
    reminder_text = get_system_reminder_store().get("actor")
    if not reminder_text.strip():
        return ""
    return "<system_reminder>\n" + reminder_text + "\n</system_reminder>"


def _build_prompt_sections(
    *,
    system_prompt: str,
    tool_registry: list[dict[str, object]] | None,
    user_prompt: str,
    reminder_text: str = "",
) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    if reminder_text.strip():
        sections.append(
            {
                "title": "系统提醒",
                "role": "system",
                "content": reminder_text,
            }
        )
    sections.append(
        {
            "title": "系统提示词",
            "role": "system",
            "content": system_prompt,
        }
    )
    if tool_registry:
        sections.append(
            {
                "title": "工具定义",
                "role": "tool",
                "content": json.dumps(tool_registry, ensure_ascii=False, indent=2),
            }
        )
    sections.append(
        {
            "title": "用户提示词",
            "role": "user",
            "content": user_prompt,
        }
    )
    return sections


async def _get_prompt_workspace(chatter: DefaultChatterRuntime):
    cfg = None
    if hasattr(chatter, "_get_plugin_config"):
        cfg = chatter._get_plugin_config()  # type: ignore[attr-defined]
    if cfg is None:
        return None

    from plugins.anysoul_core.services.workspace import get_workspace_service

    return get_workspace_service(cfg.plugin.identity_context.workspace_base_path)


class _ToolCallWorkflowPhase(str, Enum):
    """default_chatter 的 toolcall 工作流相位（简化 FSM）。

    约束目标：强制会话严格遵守
    USER → ASSISTANT(tool_calls) → TOOL_RESULT → ASSISTANT(follow-up) → USER

    - 仅在 WAIT_USER 阶段允许注入新的 USER payload
    - 仅在 MODEL_TURN/FOLLOW_UP 阶段允许向模型发起 send
    - TOOL_EXEC 阶段只执行工具并写回 TOOL_RESULT，不发起新的 USER
    """

    WAIT_USER = "wait_user"
    MODEL_TURN = "model_turn"
    TOOL_EXEC = "tool_exec"
    FOLLOW_UP = "follow_up"


@dataclass
class _EnhancedWorkflowRuntime:
    """enhanced 模式运行时状态。"""

    response: LLMConversationState
    phase: _ToolCallWorkflowPhase
    history_merged: bool
    unreads: list[Message]
    cross_round_seen_signatures: set[str]
    unread_msgs_to_flush: list[Message]
    plain_text_retry_count: int = 0

    def has_tool_result_tail(self) -> bool:
        """当前上下文尾部是否为 TOOL_RESULT。"""
        payloads = getattr(self.response, "payloads", None)
        return bool(payloads and payloads[-1].role == ROLE.TOOL_RESULT)


def _is_response_like(response: LLMConversationState) -> TypeGuard[LLMResponseLike]:
    """判断当前会话状态是否已经进入响应阶段。"""
    return hasattr(response, "call_list") and hasattr(response, "message")


def _require_response(response: LLMConversationState) -> LLMResponseLike:
    """将会话状态收窄为已完成的 LLM 响应。"""
    if _is_response_like(response):
        return response
    raise TypeError("当前会话状态尚未进入响应阶段")


def _transition(
    *,
    rt: _EnhancedWorkflowRuntime,
    to_phase: _ToolCallWorkflowPhase,
    logger: Logger,
    reason: str,
) -> None:
    """执行状态机相位切换，并记录调试日志。"""
    if rt.phase == to_phase:
        return
    debug_fn = getattr(logger, "debug", None)
    if callable(debug_fn):
        debug_fn(f"[FSM] {rt.phase.value} -> {to_phase.value}: {reason}")
    rt.phase = to_phase


async def run_enhanced(
    chatter: DefaultChatterRuntime,
    chat_stream: ChatStream,
    logger: Logger,
    pass_call_name: str,
    stop_call_name: str,
    send_text_call_name: str,
    suspend_text: str,
    enable_cooldown: bool = False,
) -> AsyncGenerator[Wait | Success | Failure | Stop, None]:
    """enhanced 模式执行流程。"""
    try:
        request = chatter.create_request("actor", with_reminder="actor")
    except (ValueError, KeyError) as error:
        logger.error(f"获取模型配置失败: {error}")
        yield Failure(f"模型配置错误: {error}")
        return

    system_prompt_text = await chatter._build_system_prompt(chat_stream)
    request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt_text)))

    history_text = chatter._build_enhanced_history_text(chat_stream)
    usable_map = await chatter.inject_usables(request)
    unread_lines_snapshot = ""
    prompt_workspace = await _get_prompt_workspace(chatter)

    rt = _EnhancedWorkflowRuntime(
        response=request,
        phase=_ToolCallWorkflowPhase.FOLLOW_UP if request.payloads and request.payloads[-1].role == ROLE.TOOL_RESULT else _ToolCallWorkflowPhase.WAIT_USER,
        history_merged=False,
        unreads=[],
        cross_round_seen_signatures=set(),
        unread_msgs_to_flush=[],
    )

    while True:
        _, unread_msgs = await chatter.fetch_unreads()

        # 安全兜底：若上下文尾部为 TOOL_RESULT，必须进入 FOLLOW_UP
        if rt.phase == _ToolCallWorkflowPhase.WAIT_USER and rt.has_tool_result_tail():
            _transition(
                rt=rt,
                to_phase=_ToolCallWorkflowPhase.FOLLOW_UP,
                logger=logger,
                reason="context tail is TOOL_RESULT; must follow-up before new USER",
            )

        # FSM 驱动：每次循环只推进一个相位（或 yield）
        if rt.phase == _ToolCallWorkflowPhase.WAIT_USER:
            if not unread_msgs:
                yield Wait()
                continue

            # 仅在采纳新未读消息时清空跨轮去重状态；FOLLOW_UP 阶段不应清空。
            rt.cross_round_seen_signatures.clear()
            rt.plain_text_retry_count = 0
            rt.unreads = unread_msgs

            unread_lines = "\n".join(
                chatter.format_message_line(msg) for msg in unread_msgs
            )
            unread_user_prompt = await chatter._build_user_prompt(
                chat_stream,
                history_text=history_text if not rt.history_merged else "",
                unread_lines=unread_lines,
                extra=chatter._build_negative_behaviors_extra(),
            )
            rt.history_merged = True
            unread_lines_snapshot = unread_lines

            decision = await chatter.sub_agent(
                unread_lines,
                unread_msgs,
                chat_stream,
            )
            logger.info(
                f"Sub-agent 决策: {decision['reason']} (响应: {decision['should_respond']})"
            )

            if not decision["should_respond"]:
                logger.info("Sub-agent 决定不响应，继续等待...")
                yield Wait()
                continue

            chatter._upsert_pending_unread_payload(
                response=rt.response,
                formatted_text=unread_user_prompt,
            )
            reminder_text = _build_reminder_text()
            if prompt_workspace is not None:
                await write_prompt_snapshot(
                    prompt_workspace,
                    scope="default_chatter",
                    title="DFC 聊天态完整提示词",
                    sections=_build_prompt_sections(
                        system_prompt=system_prompt_text,
                        tool_registry=usable_map.list_all() if hasattr(usable_map, "list_all") else None,
                        user_prompt=unread_user_prompt,
                        reminder_text=reminder_text,
                    ),
                    metadata={
                        "source": "default_chatter.runners.run_enhanced",
                        "mode": "enhanced",
                        "stream_id": chat_stream.stream_id,
                        "chat_type": chat_stream.chat_type,
                        "request_name": "default_chatter",
                        "history_length": len(history_text.splitlines()) if history_text else 0,
                        "unread_length": len(unread_lines_snapshot.splitlines()) if unread_lines_snapshot else 0,
                        "tool_count": len(usable_map.list_all()) if hasattr(usable_map, "list_all") else 0,
                    },
                )
            _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.MODEL_TURN, logger=logger, reason="accepted unread batch")

            # MODEL_TURN 阶段发送后才 flush 本轮采纳的 unread
            rt.unread_msgs_to_flush = unread_msgs
            continue

        if rt.phase in (_ToolCallWorkflowPhase.MODEL_TURN, _ToolCallWorkflowPhase.FOLLOW_UP):
            # FOLLOW_UP 阶段严禁 flush 新未读；MODEL_TURN 才 flush 本轮采纳的 unread。
            try:
                rt.response = await rt.response.send(stream=False)
                await rt.response
                if rt.phase == _ToolCallWorkflowPhase.MODEL_TURN:
                    if rt.unread_msgs_to_flush:
                        await chatter.flush_unreads(rt.unread_msgs_to_flush)
                    rt.unread_msgs_to_flush = []
            except Exception as error:
                logger.error(f"LLM 请求失败: {error}", exc_info=True)
                yield Failure("LLM 请求失败", error)
                _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.WAIT_USER, logger=logger, reason="request failed")
                continue

            _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.TOOL_EXEC, logger=logger, reason="model responded")
            continue

        if rt.phase == _ToolCallWorkflowPhase.TOOL_EXEC:
            llm_response = _require_response(rt.response)

            if not llm_response.call_list:
                if llm_response.message and llm_response.message.strip():
                    logger.warning(
                        f"LLM 返回了纯文本而非 tool call: "
                        f"{llm_response.message[:100]}"
                    )
                    yield Stop(0)
                    return
                yield Wait()
                _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.WAIT_USER, logger=logger, reason="no call_list")
                continue

            logger.info(f"本轮调用列表：{[call.name for call in llm_response.call_list or []]}")
            for call in llm_response.call_list or []:
                args = call.args if isinstance(call.args, dict) else {}
                reason = args.pop("reason", "未提供原因")
                logger.info(f"LLM 调用 {call.name}，原因: {reason}，参数: {args}")

            call_outcome = await process_tool_calls(
                stream_id=chat_stream.stream_id,
                calls=llm_response.call_list or [],
                response=llm_response,
                run_tool_call=chatter.run_tool_call,
                usable_map=usable_map,
                trigger_msg=rt.unreads[-1] if rt.unreads else None,
                pass_call_name=pass_call_name,
                stop_call_name=stop_call_name,
                send_text_call_name=send_text_call_name,
                break_on_send_text=False,
                cross_round_seen_signatures=rt.cross_round_seen_signatures,
            )

            if call_outcome.should_stop:
                cooldown_seconds = call_outcome.stop_minutes * 60 if enable_cooldown else 0
                logger.info(f"对话已结束，冷却 {call_outcome.stop_minutes} 分钟（{'已启用' if enable_cooldown else '已禁用，实际不冷却'}）")
                yield Stop(cooldown_seconds)
                return

            if call_outcome.has_pending_tool_results:
                _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.FOLLOW_UP, logger=logger, reason="pending tool results")
                continue

            append_suspend_payload_if_action_only(
                calls=llm_response.call_list or [],
                response=llm_response,
                suspend_text=suspend_text,
                logger=logger,
            )

            # 工具链已闭合，可以进入等待或接受新 user。
            if call_outcome.should_wait:
                yield Wait()
            _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.WAIT_USER, logger=logger, reason="tool exec done")
            continue


async def run_classical(
    chatter: DefaultChatterRuntime,
    chat_stream: ChatStream,
    logger: Logger,
    pass_call_name: str,
    stop_call_name: str,
    send_text_call_name: str,
    suspend_text: str,
    enable_cooldown: bool = False,
) -> AsyncGenerator[Wait | Success | Failure | Stop, None]:
    """classical 模式执行流程。"""
    try:
        base_request = chatter.create_request("actor", with_reminder="actor")
    except (ValueError, KeyError) as error:
        logger.error(f"获取模型配置失败: {error}")
        yield Failure(f"模型配置错误: {error}")
        return

    usable_map = await chatter.inject_usables(base_request)
    prompt_workspace = await _get_prompt_workspace(chatter)
    system_prompt_text = await chatter._build_system_prompt(chat_stream)

    while True:
        _, unread_msgs = await chatter.fetch_unreads()
        unreads = unread_msgs

        if not unread_msgs:
            yield Wait()
            continue

        classical_user_text = await chatter._build_classical_user_text(
            chat_stream,
            unread_msgs,
        )
        unread_lines = "\n".join(
            chatter.format_message_line(msg) for msg in unread_msgs
        )
        decision = await chatter.sub_agent(
            unread_lines,
            unread_msgs,
            chat_stream,
        )
        logger.info(
            f"Sub-agent 决策: {decision['reason']} (响应: {decision['should_respond']})"
        )

        if not decision["should_respond"]:
            logger.info("Sub-agent 决定不响应，继续等待...")
            yield Wait()
            continue

        request = chatter.create_request("actor", with_reminder="actor")
        request.add_payload(
            LLMPayload(
                ROLE.SYSTEM,
                Text(system_prompt_text),
            )
        )
        request.add_payload(LLMPayload(ROLE.USER, Text(classical_user_text)))
        if usable_map.get_all():
            request.add_payload(LLMPayload(ROLE.TOOL, usable_map.get_all()))  # type: ignore[arg-type]
        reminder_text = _build_reminder_text()
        if prompt_workspace is not None:
            await write_prompt_snapshot(
                prompt_workspace,
                scope="default_chatter",
                title="DFC 聊天态完整提示词",
                sections=_build_prompt_sections(
                    system_prompt=system_prompt_text,
                    tool_registry=usable_map.list_all() if hasattr(usable_map, "list_all") else None,
                    user_prompt=classical_user_text,
                    reminder_text=reminder_text,
                ),
                metadata={
                    "source": "default_chatter.runners.run_classical",
                    "mode": "classical",
                    "stream_id": chat_stream.stream_id,
                    "chat_type": chat_stream.chat_type,
                    "request_name": "default_chatter",
                    "history_length": len(chat_stream.context.history_messages),
                    "unread_length": len(unread_msgs),
                    "tool_count": len(usable_map.list_all()) if hasattr(usable_map, "list_all") else 0,
                },
            )

        response = request
        cross_round_seen_signatures: set[str] = set()
        has_pending_tool_results = False
        plain_text_retry_count = 0

        while True:
            try:
                response = await response.send(stream=False)
                await response
            except Exception as error:
                logger.error(f"LLM 请求失败: {error}", exc_info=True)
                yield Failure("LLM 请求失败", error)
                break

            if not response.call_list:
                if response.message and response.message.strip():
                    logger.warning(
                        f"LLM 返回了纯文本而非 tool call: "
                        f"{response.message[:100]}"
                    )
                await chatter.flush_unreads(unread_msgs)
                yield Stop(0)
                return

            for call in response.call_list or []:
                args = call.args if isinstance(call.args, dict) else {}
                reason = args.pop("reason", "未提供原因")
                logger.info(f"LLM 调用 {call.name}，原因: {reason}，参数: {args}")

            call_outcome = await process_tool_calls(
                stream_id=chat_stream.stream_id,
                calls=response.call_list or [],
                response=response,
                run_tool_call=chatter.run_tool_call,
                usable_map=usable_map,
                trigger_msg=unreads[-1] if unreads else None,
                pass_call_name=pass_call_name,
                stop_call_name=stop_call_name,
                send_text_call_name=send_text_call_name,
                break_on_send_text=True,
                cross_round_seen_signatures=cross_round_seen_signatures,
            )
            has_pending_tool_results = call_outcome.has_pending_tool_results
            if not call_outcome.has_pending_tool_results:
                append_suspend_payload_if_action_only(
                    calls=response.call_list or [],
                    response=response,
                    suspend_text=suspend_text,
                    logger=logger,
                )

            if call_outcome.sent_once:
                logger.info("classical 模式已发送一次消息，强制结束当前对话")
                await chatter.flush_unreads(unread_msgs)
                yield Stop(0)
                return

            if call_outcome.should_stop:
                cooldown_seconds = call_outcome.stop_minutes * 60 if enable_cooldown else 0
                logger.info(f"对话已结束，冷却 {call_outcome.stop_minutes} 分钟（{'已启用' if enable_cooldown else '已禁用，实际不冷却'}）")
                await chatter.flush_unreads(unread_msgs)
                yield Stop(cooldown_seconds)
                return

            if call_outcome.should_wait:
                # 同 enhanced：若同时存在 pending 工具结果，优先 follow-up。
                if has_pending_tool_results:
                    continue
                await chatter.flush_unreads(unread_msgs)
                yield Wait()
                break
