"""Decision Hub Service - AnySoul 中枢决策与任务派发。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import json_repair

from src.kernel.logger import get_logger
from src.kernel.prompt_snapshot import write_prompt_snapshot

from .workspace import WorkspaceService, get_workspace_service

if TYPE_CHECKING:
    from src.core.components.base import BasePlugin
    from src.core.models.message import Message

logger = get_logger("anysoul.decision_hub")


def _now_iso() -> str:
    return datetime.now().isoformat()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


class DecisionHubService:
    """长期驻留的中枢决策服务（MVP）。

    当前阶段只做一个决策：是否进入聊天任务（chat task）。
    """

    def __init__(
        self,
        plugin: BasePlugin | None = None,
        workspace: WorkspaceService | None = None,
    ) -> None:
        self.plugin = plugin
        self.workspace = workspace or get_workspace_service()
        self._state_path = "state/decision_hub.json"
        self._initialized = False
        self._process_lock = asyncio.Lock()

    def bind_plugin(self, plugin: BasePlugin) -> None:
        """绑定插件实例（用于创建 Agent）。"""
        self.plugin = plugin

    async def initialize(self) -> None:
        """初始化中枢状态。"""
        if self._initialized:
            return

        await self.workspace.initialize()
        state = await self._load_state()
        await self._save_state(state)
        await self._write_prompt_preview_snapshot(state)
        self._initialized = True
        logger.info("DecisionHub 初始化完成")

    async def enqueue_message_event(self, message: Message) -> str:
        """将收到的消息写入待处理事件队列。"""
        await self.initialize()

        event_id = (
            f"evt_msg_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        )
        message_text = (
            message.processed_plain_text
            if message.processed_plain_text
            else str(message.content or "")
        )
        extra_data = message.extra if isinstance(message.extra, dict) else {}

        event_data: dict[str, Any] = {
            "event_id": event_id,
            "event_type": "message_received",
            "timestamp": _now_iso(),
            "message": {
                "message_id": message.message_id,
                "time": message.time,
                "stream_id": message.stream_id,
                "platform": message.platform,
                "chat_type": message.chat_type,
                "sender_id": message.sender_id,
                "sender_name": message.sender_name,
                "sender_role": message.sender_role,
                "text": message_text,
                "reply_to": message.reply_to,
                "meta": {
                    "group_id": extra_data.get("group_id"),
                    "group_name": extra_data.get("group_name"),
                    "is_self": extra_data.get("is_self"),
                },
            },
        }

        await self.workspace.write_json(f"events/pending/{event_id}.json", event_data)
        logger.info(
            f"[事件流] 收到消息并入队 event_id={event_id}, "
            f"stream_id={message.stream_id}, sender={message.sender_name or message.sender_id}"
        )
        return event_id

    async def enqueue_user_profile_event(
        self,
        *,
        platform: str,
        user_id: str,
        nickname: str | None = None,
        cardname: str | None = None,
        stream_id: str = "",
        source: str = "message_receiver",
    ) -> str:
        """将用户信息更新写入待处理事件队列。"""
        await self.initialize()

        event_id = (
            f"evt_user_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        )
        event_data: dict[str, Any] = {
            "event_id": event_id,
            "event_type": "user_profile_updated",
            "timestamp": _now_iso(),
            "source": source,
            "profile": {
                "platform": str(platform or ""),
                "user_id": str(user_id or ""),
                "nickname": str(nickname or ""),
                "cardname": str(cardname or ""),
                "stream_id": str(stream_id or ""),
            },
        }

        await self.workspace.write_json(f"events/pending/{event_id}.json", event_data)
        logger.info(
            f"[事件流] 用户信息更新并入队 event_id={event_id}, "
            f"user_id={user_id}, nickname={nickname or ''}"
        )
        return event_id

    async def process_pending_once(
        self,
        max_events: int = 1,
        trigger: str = "manual",
    ) -> dict[str, Any]:
        """处理待处理事件（单轮）。"""
        await self.initialize()

        async with self._process_lock:
            pending_items = await self._load_pending_events()
            if not pending_items:
                return {
                    "trigger": trigger,
                    "processed": 0,
                    "dispatched": 0,
                    "failed": 0,
                }

            state = await self._load_state()
            stats = state.setdefault("stats", {})
            stats.setdefault("total_events", 0)
            stats.setdefault("chat_dispatched", 0)
            stats.setdefault("chat_completed", 0)
            stats.setdefault("chat_failed", 0)

            processed = 0
            dispatched = 0
            failed = 0

            for rel_path, event in pending_items[: max(1, max_events)]:
                task_result: dict[str, Any] | None = None
                event_type = str(event.get("event_type", "") or "message_received")
                if event_type == "message_received":
                    decision = await self._should_chat(event)
                elif event_type == "user_profile_updated":
                    profile = event.get("profile", {})
                    if not isinstance(profile, dict):
                        profile = {}
                    decision = {
                        "should_chat": False,
                        "reason": "user_profile_updated",
                        "objective": "",
                        "tool_calls": [],
                        "thoughts": [],
                        "profile": profile,
                    }
                else:
                    decision = {
                        "should_chat": False,
                        "reason": f"unsupported_event_type:{event_type}",
                        "objective": "",
                        "tool_calls": [],
                        "thoughts": [],
                    }
                event_id = str(event.get("event_id", "") or "")
                message_data = event.get("message", {})
                if not isinstance(message_data, dict):
                    message_data = {}
                stream_id = str(message_data.get("stream_id", "") or "")
                if not stream_id:
                    profile = event.get("profile", {})
                    if isinstance(profile, dict):
                        stream_id = str(profile.get("stream_id", "") or "")
                thoughts = decision.get("thoughts", [])
                if not isinstance(thoughts, list):
                    thoughts = []

                if thoughts:
                    await self._record_hub_thoughts(
                        source_event_id=event_id,
                        stream_id=stream_id,
                        thoughts=thoughts,
                    )

                await self._record_timeline_event(
                    event_type="decision_made",
                    payload={
                        "source_event_id": event_id,
                        "stream_id": stream_id,
                        "should_chat": bool(decision.get("should_chat", False)),
                        "reason": str(decision.get("reason", "")),
                        "thought_count": len(thoughts),
                        "tool_calls": decision.get("tool_calls", []),
                        "trigger": trigger,
                    },
                )
                logger.info(
                    f"[事件流] 决策完成 event_id={event_id}, stream_id={stream_id}, "
                    f"should_chat={bool(decision.get('should_chat', False))}, "
                    f"reason={decision.get('reason', '')}, thoughts={len(thoughts)}"
                )

                if decision.get("should_chat", False):
                    dispatched += 1
                    stats["chat_dispatched"] += 1
                    task_id = self._new_task_id()
                    task_mark = self._build_task_mark(task_id=task_id, event=event)
                    task_result = await self._dispatch_chat_task(
                        event=event,
                        objective=str(decision.get("objective", "")),
                        task_id=task_id,
                        task_mark=task_mark,
                    )
                    if task_result.get("success"):
                        stats["chat_completed"] += 1
                    else:
                        stats["chat_failed"] += 1
                        failed += 1

                await self._finalize_event(rel_path, event, decision, task_result)
                self._append_recent_event(state, event, decision, task_result)

                stats["total_events"] += 1
                processed += 1

            await self._save_state(state)

            return {
                "trigger": trigger,
                "processed": processed,
                "dispatched": dispatched,
                "failed": failed,
            }

    async def on_heartbeat(self, _: dict[str, Any]) -> dict[str, Any]:
        """心跳回调：尝试消费 backlog。"""
        return await self.process_pending_once(max_events=3, trigger="heartbeat")

    async def get_recent_events(self, limit: int | None = None) -> list[dict[str, Any]]:
        """获取中枢近期事件摘要。"""
        state = await self._load_state()
        recent = state.get("recent_events", [])
        if not isinstance(recent, list):
            return []

        max_window = state.get("event_window_size", 20)
        if not isinstance(max_window, int) or max_window <= 0:
            max_window = 20

        if limit is None or limit <= 0:
            limit = max_window

        return recent[-limit:]

    async def _load_pending_events(self) -> list[tuple[str, dict[str, Any]]]:
        files = await self.workspace.list_files("events/pending", "*.json")
        items: list[tuple[str, dict[str, Any]]] = []

        for rel_path in files:
            try:
                data = await self.workspace.read_json(rel_path)
            except Exception as exc:
                logger.warning(f"读取 pending 事件失败，跳过: {rel_path} ({exc})")
                continue

            if isinstance(data, dict):
                items.append((rel_path, data))

        items.sort(key=lambda item: str(item[1].get("timestamp", "")))
        return items

    async def _should_chat(self, event: dict[str, Any]) -> dict[str, Any]:
        """中枢决策器：由 LLM 决定是否调用任务派发工具。"""
        message = event.get("message", {})
        if not isinstance(message, dict):
            return {"should_chat": False, "reason": "invalid_message", "objective": ""}

        text = str(message.get("text", "") or "").strip()
        sender_role = str(message.get("sender_role", "") or "").lower()
        meta = message.get("meta", {})
        is_self = bool(meta.get("is_self")) if isinstance(meta, dict) else False

        if not text:
            return {"should_chat": False, "reason": "empty_text", "objective": ""}

        if sender_role == "bot" or is_self:
            return {"should_chat": False, "reason": "self_or_bot_message", "objective": ""}

        llm_decision = await self._decide_with_llm(event=event, message=message, text=text)
        if llm_decision is not None:
            return llm_decision

        fallback_objective = self._build_state_supplement(state=await self._load_state())
        return {
            "should_chat": True,
            "reason": "fallback_default_chat_rule",
            "objective": fallback_objective,
            "tool_call": {
                "name": "dispatch_chat_task",
                "arguments": {
                    "objective": fallback_objective,
                },
            },
        }

    def _build_decision_system_prompt(self) -> str:
        """构建中枢决策器系统提示词。"""
        return """你是长期驻留的“中枢决策器”，负责决定是否派发聊天任务。

你可调用两个工具：
1. think(thought: str)
   - 仅用于内部思考与记录，不会直接发送消息
   - 只允许一个参数：thought
2. dispatch_chat_task(objective: str)
   - 派发聊天任务给聊天态执行

决策规则：
1. 若本条消息值得回复，则 should_chat=true，并在 tool_calls 中给出一次 dispatch_chat_task 调用。
2. 说“该不该回”由你决定，但“具体说什么”不由你决定，交给聊天态生成。
3. dispatch_chat_task 的 objective 只能写中枢补充信息，例如：
   - 当前中枢状态：忙、累、刚刚在做什么、现在想不想顺口分享
   - 对回复氛围的补充建议：轻松、关心、陪伴、克制、活跃等
   - 不要写成成品台词，不要替聊天态规定具体句子
   - 不要根据消息正文推导具体回复，不要把“要说什么”塞进 objective
4. 若不需要回复，should_chat=false；可只保留 think 调用或不调用任何工具。
5. 仅输出 JSON，不要 markdown，不要额外文本。

JSON 输出格式：
{
  "should_chat": true,
  "reason": "简短理由",
  "tool_calls": [
    {"name": "think", "arguments": {"thought": "..."}},
    {"name": "dispatch_chat_task", "arguments": {"objective": "..."}}
  ]
}
"""

    def _build_decision_user_prompt(
        self,
        *,
        sender_name: str,
        chat_type: str,
        text: str,
        recent_block: str,
        soul_text: str,
        memory_text: str,
    ) -> str:
        """构建中枢决策器用户提示词。"""
        return f"""[消息]
发送者: {sender_name}
聊天类型: {chat_type}
文本: {text}

[近期事件]
{recent_block}

[Soul]
{_truncate(soul_text, 1800)}

[Memory]
{_truncate(memory_text, 1800)}
"""

    async def _write_prompt_preview_snapshot(self, state: dict[str, Any]) -> None:
        """在启动时写入中枢提示词预览，避免看板首次打开时为空。"""
        try:
            current_snapshot = await self.workspace.read_json("prompts/current/decision_hub.json")
            if (
                isinstance(current_snapshot, dict)
                and not bool(current_snapshot.get("metadata", {}).get("is_startup_preview"))
            ):
                logger.info("检测到已有真实中枢提示词快照，跳过启动预览覆盖")
                return
            recent_block = self._format_recent_events_for_hub_prompt(
                await self.get_recent_events(limit=12)
            )
            soul_text = (await self.workspace.read_file("soul.md")) or ""
            memory_text = (await self.workspace.read_file("memory.md")) or ""
            await write_prompt_snapshot(
                self.workspace,
                scope="decision_hub",
                title="AnySoul 中枢完整提示词",
                sections=[
                    {
                        "title": "系统提示词",
                        "role": "system",
                        "content": self._build_decision_system_prompt(),
                    },
                    {
                        "title": "用户提示词",
                        "role": "user",
                        "content": self._build_decision_user_prompt(
                            sender_name="（尚未收到消息）",
                            chat_type="unknown",
                            text="（等待消息触发）",
                            recent_block=recent_block,
                            soul_text=soul_text,
                            memory_text=memory_text,
                        ),
                    },
                ],
                metadata={
                    "source": "anysoul_core.decision_hub.initialize",
                    "request_name": "anysoul_decision_hub",
                    "model_task": self._resolve_decision_hub_model_task(),
                    "stream_id": "",
                    "sender_name": "",
                    "chat_type": "unknown",
                    "source_event_id": "",
                    "recent_event_count": len(state.get("recent_events", []))
                    if isinstance(state.get("recent_events", []), list)
                    else 0,
                    "is_startup_preview": True,
                },
            )
            logger.info("中枢启动预览提示词已写入")
        except Exception as exc:
            logger.warning(f"中枢启动提示词快照写入失败: {exc}")

    async def _decide_with_llm(
        self,
        event: dict[str, Any],
        message: dict[str, Any],
        text: str,
    ) -> dict[str, Any] | None:
        """使用 LLM 生成中枢决策（tool call 语义）。"""
        try:
            from src.core.config import get_model_config
            from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text
        except Exception as exc:
            logger.warning(f"中枢无法加载 LLM 依赖，改用 fallback: {exc}")
            return None

        sender_name = str(message.get("sender_name", "") or "对方")
        chat_type = str(message.get("chat_type", "") or "private")
        state = await self._load_state()
        recent_block = self._format_recent_events_for_hub_prompt(await self.get_recent_events(limit=12))
        soul_text = (await self.workspace.read_file("soul.md")) or ""
        memory_text = (await self.workspace.read_file("memory.md")) or ""
        system_prompt = self._build_decision_system_prompt()
        user_prompt = self._build_decision_user_prompt(
            sender_name=sender_name,
            chat_type=chat_type,
            text=text,
            recent_block=recent_block,
            soul_text=soul_text,
            memory_text=memory_text,
        )

        await write_prompt_snapshot(
            self.workspace,
            scope="decision_hub",
            title="AnySoul 中枢完整提示词",
            sections=[
                {
                    "title": "系统提示词",
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "title": "用户提示词",
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            metadata={
                "source": "anysoul_core.decision_hub",
                "request_name": "anysoul_decision_hub",
                "model_task": self._resolve_decision_hub_model_task(),
                "stream_id": str(message.get("stream_id", "") or ""),
                "sender_name": sender_name,
                "chat_type": chat_type,
                "source_event_id": str(event.get("event_id", "") or ""),
                "recent_event_count": len(await self.get_recent_events(limit=12)),
            },
        )

        try:
            model_task = self._resolve_decision_hub_model_task()
            model_set = get_model_config().get_task(model_task)
            request = LLMRequest(model_set=model_set, request_name="anysoul_decision_hub")
            request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt)))
            request.add_payload(LLMPayload(ROLE.USER, Text(user_prompt)))

            response = await request.send(stream=False)
            await response
            raw = (response.message or "").strip()
            parsed = json_repair.loads(raw) if raw else {}
            return self._normalize_llm_decision(
                parsed,
                message=message,
                text=text,
                state=state,
            )
        except Exception as exc:
            logger.warning(f"中枢 LLM 决策失败，改用 fallback: {exc}")
            return None

    def _resolve_decision_hub_model_task(self) -> str:
        """读取中枢决策器模型任务名。"""
        try:
            from ..config import AnySoulCoreConfig

            plugin_config = getattr(self.plugin, "config", None)
            if isinstance(plugin_config, AnySoulCoreConfig):
                task_name = str(plugin_config.agent.decision_hub_model_task or "").strip()
                if task_name:
                    return task_name
        except Exception:
            pass
        return "actor"

    def _normalize_llm_decision(
        self,
        parsed: Any,
        message: dict[str, Any],
        text: str,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """归一化 LLM 输出到中枢决策结构。"""
        sender_name = str(message.get("sender_name", "") or "对方")
        fallback_objective = self._build_state_supplement(state=state)

        if not isinstance(parsed, dict):
            return {
                "should_chat": True,
                "reason": "llm_invalid_payload_fallback",
                "objective": fallback_objective,
                "thoughts": [],
                "tool_calls": [
                    {
                        "name": "dispatch_chat_task",
                        "arguments": {"objective": fallback_objective},
                    }
                ],
                "tool_call": {
                    "name": "dispatch_chat_task",
                    "arguments": {"objective": fallback_objective},
                },
            }

        reason = str(parsed.get("reason", "") or "llm_decision").strip()
        should_chat_raw = parsed.get("should_chat")
        should_chat = bool(should_chat_raw) if isinstance(should_chat_raw, bool) else None

        tool_calls = parsed.get("tool_calls", [])
        objective = str(parsed.get("objective", "") or "").strip()
        found_dispatch = False
        thoughts: list[str] = []
        normalized_calls: list[dict[str, Any]] = []

        if isinstance(tool_calls, list):
            for item in tool_calls:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "") or "")
                args = item.get("arguments", {})
                if not isinstance(args, dict):
                    args = {}

                if name == "think":
                    thought = str(args.get("thought", "") or "").strip()
                    if thought:
                        thought = _truncate(thought, 280)
                        thoughts.append(thought)
                        normalized_calls.append(
                            {"name": "think", "arguments": {"thought": thought}}
                        )
                    continue

                if name == "dispatch_chat_task":
                    found_dispatch = True
                    normalized_calls.append(
                        {
                            "name": "dispatch_chat_task",
                            "arguments": {"objective": _truncate(fallback_objective, 360)},
                        }
                    )
                    continue

        if should_chat is None:
            should_chat = bool(found_dispatch or objective)

        if not should_chat:
            return {
                "should_chat": False,
                "reason": reason or "llm_no_dispatch",
                "objective": "",
                "thoughts": thoughts,
                "tool_calls": normalized_calls,
            }

        objective = _truncate(fallback_objective, 360)
        dispatch_call = {
            "name": "dispatch_chat_task",
            "arguments": {"objective": objective},
        }
        has_dispatch = any(
            isinstance(call, dict) and str(call.get("name", "")) == "dispatch_chat_task"
            for call in normalized_calls
        )
        if not has_dispatch:
            normalized_calls.append(dispatch_call)

        return {
            "should_chat": True,
            "reason": reason or "llm_dispatch_chat_task",
            "objective": objective,
            "thoughts": thoughts,
            "tool_calls": normalized_calls,
            "tool_call": dispatch_call,
            "target": sender_name,
        }

    def _build_state_supplement(
        self,
        *,
        state: dict[str, Any] | None,
    ) -> str:
        stats: dict[str, Any] = {}
        recent_events: list[dict[str, Any]] = []
        if isinstance(state, dict):
            maybe_stats = state.get("stats", {})
            if isinstance(maybe_stats, dict):
                stats = maybe_stats
            maybe_recent = state.get("recent_events", [])
            if isinstance(maybe_recent, list):
                recent_events = [item for item in maybe_recent if isinstance(item, dict)]

        total_events = int(stats.get("total_events", 0) or 0)
        chat_dispatched = int(stats.get("chat_dispatched", 0) or 0)
        chat_completed = int(stats.get("chat_completed", 0) or 0)
        last_decision = ""
        if recent_events:
            last_decision = str(recent_events[-1].get("decision", "") or "")

        parts = [
            "中枢补充信息：",
            "这是一条只给聊天态看的状态备注，不决定具体台词。",
        ]
        if total_events > 0:
            parts.append(f"中枢刚刚累计处理了 {total_events} 条事件。")
        if chat_dispatched > 0:
            parts.append(f"其中有 {chat_dispatched} 次聊天任务派发。")
        if chat_completed > 0:
            parts.append(f"当前已有 {chat_completed} 个聊天任务完成回流。")
        if last_decision:
            parts.append(f"最近一轮中枢的决策是 {last_decision}。")
        parts.append("如果需要，可以自然带一点刚忙完、略累、或者想顺口分享一下近况的状态感。")
        return "".join(parts)

    def _format_recent_events_for_hub_prompt(self, recent_events: list[dict[str, Any]]) -> str:
        if not recent_events:
            return "- （暂无）"
        lines: list[str] = []
        for item in recent_events[-8:]:
            decision = str(item.get("decision", "") or "")
            preview = str(item.get("preview", "") or "")
            expression_summary = str(item.get("expression_summary", "") or "")
            send_preview = str(item.get("send_preview", "") or "")
            segment_count = int(item.get("segment_count", 0) or 0)
            thought_preview = str(item.get("thought_preview", "") or "")
            lines.append(
                f"- [{decision}] {_truncate(preview, 48)}"
                f"{' | ' + _truncate(expression_summary, 36) if expression_summary else ''}"
                f"{' | 发送:' + _truncate(send_preview, 30) if send_preview else ''}"
                f"{' | 分段=' + str(segment_count) if segment_count > 0 else ''}"
                f"{' | 思考:' + _truncate(thought_preview, 24) if thought_preview else ''}"
            )
        return "\n".join(lines)

    async def _record_hub_thoughts(
        self,
        source_event_id: str,
        stream_id: str,
        thoughts: list[str],
    ) -> None:
        for index, thought in enumerate(thoughts, start=1):
            note = str(thought or "").strip()
            if not note:
                continue
            await self._record_timeline_event(
                event_type="hub_think",
                payload={
                    "source_event_id": source_event_id,
                    "stream_id": stream_id,
                    "index": index,
                    "total": len(thoughts),
                    "thought": note,
                },
            )
            logger.info(
                f"[中枢思考] source_event_id={source_event_id}, stream_id={stream_id}, "
                f"index={index}/{len(thoughts)}, thought={_truncate(note, 140)}"
            )

    async def _dispatch_chat_task(
        self,
        event: dict[str, Any],
        objective: str,
        task_id: str,
        task_mark: str,
    ) -> dict[str, Any]:
        """派发聊天任务给 ChatTaskAgent。"""
        message = event.get("message", {})
        stream_id = str(message.get("stream_id", "") or "")
        sender_name = str(message.get("sender_name", "") or "对方")
        if not stream_id:
            return {
                "success": False,
                "task_id": task_id,
                "task_mark": task_mark,
                "error": "missing_stream_id",
                "expression_summary": "任务未执行：缺少 stream_id",
            }

        if self.plugin is None:
            return {
                "success": False,
                "task_id": task_id,
                "task_mark": task_mark,
                "error": "plugin_not_bound",
                "expression_summary": "任务未执行：DecisionHub 未绑定插件实例",
            }

        created_at = _now_iso()

        task_data: dict[str, Any] = {
            "task_id": task_id,
            "task_mark": task_mark,
            "task_type": "chat",
            "status": "dispatched",
            "created_at": created_at,
            "stream_id": stream_id,
            "source_event_id": event.get("event_id", ""),
            "objective": objective,
            "supplement": objective,
            "input_summary": f"来自 {sender_name} 的消息事件",
        }

        await self.workspace.write_json(f"tasks/active/{task_id}.json", task_data)
        await self._record_timeline_event(
            event_type="task_dispatched",
            payload={
                "task_id": task_id,
                "task_mark": task_mark,
                "task_type": "chat",
                "stream_id": stream_id,
                "source_event_id": event.get("event_id", ""),
                "objective": objective,
                "supplement": objective,
            },
        )
        logger.info(
            f"[任务追踪] 派发任务 task_id={task_id}, task_mark={task_mark}, stream_id={stream_id}"
        )
        logger.info(
            f"[任务追踪] 任务补充 task_id={task_id}, supplement={_truncate(objective, 180)}"
        )

        task_data["status"] = "running"
        task_data["started_at"] = _now_iso()
        await self.workspace.write_json(f"tasks/active/{task_id}.json", task_data)
        await self._record_timeline_event(
            event_type="task_started",
            payload={
                "task_id": task_id,
                "task_mark": task_mark,
                "stream_id": stream_id,
            },
        )
        logger.info(f"[任务追踪] 任务执行中 task_id={task_id}, task_mark={task_mark}")

        result_payload: dict[str, Any] = {}
        success = False
        error_text = ""

        try:
            from ..agents import ChatTaskAgent

            agent = ChatTaskAgent(stream_id=stream_id, plugin=self.plugin)
            success, raw_result = await agent.execute(
                task_id=task_id,
                task_mark=task_mark,
                objective=objective,
                event=event,
                recent_events=await self.get_recent_events(),
                dry_run=False,
            )
            result_payload = raw_result if isinstance(raw_result, dict) else {"detail": str(raw_result)}
        except Exception as exc:
            error_text = str(exc)
            result_payload = {"detail": f"chat task 执行异常: {exc}"}
            logger.error(f"聊天任务执行异常: task_id={task_id}, error={exc}", exc_info=True)

        send_call = result_payload.get("send_call", {})
        if isinstance(send_call, dict) and send_call:
            await self._record_timeline_event(
                event_type="chat_send_called",
                payload={
                    "task_id": task_id,
                    "task_mark": task_mark,
                    "stream_id": stream_id,
                    "send_call": send_call,
                },
            )
            logger.info(
                f"[事件回流] 发送调用 task_id={task_id}, "
                f"send_call={_truncate(str(send_call), 240)}"
            )

        completed_data = dict(task_data)
        completed_data["status"] = "completed" if success else "failed"
        completed_data["finished_at"] = _now_iso()
        completed_data["result"] = result_payload
        if error_text:
            completed_data["error"] = error_text

        await self.workspace.write_json(f"tasks/completed/{task_id}.json", completed_data)
        await self._record_timeline_event(
            event_type="task_completed" if success else "task_failed",
            payload={
                "task_id": task_id,
                "task_mark": task_mark,
                "stream_id": stream_id,
                "sent": bool(result_payload.get("sent")),
                "segment_count": int(result_payload.get("segment_count", 0) or 0),
                "mode": str(result_payload.get("mode", "")),
                "expression_summary": str(result_payload.get("expression_summary", "")),
                "send_call": send_call if isinstance(send_call, dict) else {},
                "send_detail": str(result_payload.get("send_detail", "")),
                "supplement": objective,
                "error": error_text or str(result_payload.get("error", "")),
            },
        )

        if success:
            logger.info(
                f"[任务追踪] 任务完成 task_id={task_id}, task_mark={task_mark}, "
                f"sent={bool(result_payload.get('sent'))}"
            )
            logger.info(
                f"[事件回流] 任务结果 task_id={task_id}, "
                f"mode={str(result_payload.get('mode', '') or 'unknown')}, "
                f"segment_count={int(result_payload.get('segment_count', 0) or 0)}, "
                f"expression_summary={_truncate(str(result_payload.get('expression_summary', '') or ''), 100)}, "
                f"send_detail={_truncate(str(result_payload.get('send_detail', '') or ''), 120)}, "
                f"send_call={_truncate(str(send_call), 180)}"
            )
        else:
            logger.error(
                f"[任务追踪] 任务失败 task_id={task_id}, task_mark={task_mark}, "
                f"error={error_text or str(result_payload.get('error', ''))}"
            )
            logger.error(
                f"[事件回流] 任务失败详情 task_id={task_id}, "
                f"mode={str(result_payload.get('mode', '') or 'unknown')}, "
                f"expression_summary={_truncate(str(result_payload.get('expression_summary', '') or ''), 100)}, "
                f"error={_truncate(error_text or str(result_payload.get('error', '') or ''), 160)}"
            )

        active_path = self.workspace.get_path("tasks/active", f"{task_id}.json")
        if active_path.exists():
            active_path.unlink()

        return {
            "success": success,
            "task_id": task_id,
            "task_mark": task_mark,
            "sent": bool(result_payload.get("sent")),
            "segment_count": int(result_payload.get("segment_count", 0) or 0),
            "expression_summary": str(result_payload.get("expression_summary", "") or ""),
            "send_detail": str(result_payload.get("send_detail", "") or ""),
            "send_call": send_call if isinstance(send_call, dict) else {},
            "mode": str(result_payload.get("mode", "")),
            "error": error_text or str(result_payload.get("error", "") or ""),
        }

    async def _finalize_event(
        self,
        pending_rel_path: str,
        event: dict[str, Any],
        decision: dict[str, Any],
        task_result: dict[str, Any] | None,
    ) -> None:
        processed = dict(event)
        processed["processed_at"] = _now_iso()
        processed["decision"] = decision
        if task_result:
            processed["task_result"] = task_result

        processed_rel_path = pending_rel_path.replace(
            "events/pending/",
            "events/processed/",
            1,
        )
        await self.workspace.write_json(processed_rel_path, processed)

        pending_abs = self.workspace.get_path(*pending_rel_path.split("/"))
        if pending_abs.exists():
            pending_abs.unlink()

    def _append_recent_event(
        self,
        state: dict[str, Any],
        event: dict[str, Any],
        decision: dict[str, Any],
        task_result: dict[str, Any] | None,
    ) -> None:
        event_type = str(event.get("event_type", "") or "message_received")
        message = event.get("message", {})
        text = ""
        if isinstance(message, dict):
            text = str(message.get("text", "") or "")
        if not text and event_type == "user_profile_updated":
            profile = event.get("profile", {})
            if isinstance(profile, dict):
                nickname = str(profile.get("nickname", "") or "")
                user_id = str(profile.get("user_id", "") or "")
                cardname = str(profile.get("cardname", "") or "")
                text = (
                    f"用户资料更新 nickname={nickname or '-'} "
                    f"cardname={cardname or '-'} user_id={user_id or '-'}"
                )

        send_preview = ""
        send_call_name = ""
        segment_count = 0
        send_detail = ""
        thought_preview = ""
        if isinstance(task_result, dict):
            segment_count = int(task_result.get("segment_count", 0) or 0)
            send_detail = str(task_result.get("send_detail", "") or "")
            send_call = task_result.get("send_call", {})
            if isinstance(send_call, dict):
                send_call_name = str(send_call.get("name", "") or "")
                args = send_call.get("arguments", {})
                if isinstance(args, dict):
                    content = args.get("content")
                    if isinstance(content, list):
                        merged = " / ".join(
                            seg.strip() for seg in content if isinstance(seg, str) and seg.strip()
                        )
                        send_preview = _truncate(merged, 80)
                    elif isinstance(content, str):
                        send_preview = _truncate(content.strip(), 80)
        thoughts = decision.get("thoughts", [])
        if isinstance(thoughts, list) and thoughts:
            thought_preview = _truncate(
                " | ".join(str(item).strip() for item in thoughts if str(item).strip()),
                120,
            )

        entry = {
            "event_id": event.get("event_id", ""),
            "timestamp": event.get("timestamp", _now_iso()),
            "stream_id": message.get("stream_id", "") if isinstance(message, dict) else "",
            "sender": message.get("sender_name", "") if isinstance(message, dict) else "",
            "preview": _truncate(text, 80),
            "decision": "chat" if decision.get("should_chat") else event_type,
            "reason": decision.get("reason", ""),
            "task_id": task_result.get("task_id", "") if task_result else "",
            "task_mark": task_result.get("task_mark", "") if task_result else "",
            "expression_summary": (
                task_result.get("expression_summary", "") if task_result else ""
            ),
            "segment_count": segment_count,
            "send_preview": send_preview,
            "send_call_name": send_call_name,
            "send_detail": _truncate(send_detail, 120),
            "thought_preview": thought_preview,
        }

        recent = state.setdefault("recent_events", [])
        if not isinstance(recent, list):
            recent = []
            state["recent_events"] = recent
        recent.append(entry)

        window = state.get("event_window_size", 20)
        if not isinstance(window, int) or window <= 0:
            window = 20
            state["event_window_size"] = window

        if len(recent) > window:
            state["recent_events"] = recent[-window:]

    def _new_task_id(self) -> str:
        return f"task_chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"

    def _build_task_mark(self, task_id: str, event: dict[str, Any]) -> str:
        event_id = str(event.get("event_id", "") or "evt_unknown")
        message = event.get("message", {})
        stream_id = (
            str(message.get("stream_id", "") or "stream_unknown")
            if isinstance(message, dict)
            else "stream_unknown"
        )
        return f"chat::{stream_id[-12:]}::{event_id[-8:]}::{task_id[-8:]}"

    async def _record_timeline_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> str:
        event_id = (
            f"evt_hub_{event_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        )
        data = {
            "event_id": event_id,
            "event_type": event_type,
            "timestamp": _now_iso(),
            "payload": payload,
        }
        await self.workspace.write_json(f"events/timeline/{event_id}.json", data)
        return event_id

    async def _load_state(self) -> dict[str, Any]:
        try:
            data = await self.workspace.read_json(self._state_path)
        except Exception as exc:
            logger.warning(f"读取中枢状态失败，使用默认状态: {exc}")
            data = None

        if isinstance(data, dict):
            return data
        return self._build_default_state()

    async def _save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = _now_iso()
        await self.workspace.write_json(self._state_path, state)

    def _build_default_state(self) -> dict[str, Any]:
        now = _now_iso()
        return {
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "event_window_size": 20,
            "recent_events": [],
            "stats": {
                "total_events": 0,
                "chat_dispatched": 0,
                "chat_completed": 0,
                "chat_failed": 0,
            },
        }


_decision_hub_service: DecisionHubService | None = None


def get_decision_hub(
    plugin: BasePlugin | None = None,
    workspace: WorkspaceService | None = None,
) -> DecisionHubService:
    """获取 DecisionHub 服务单例。"""
    global _decision_hub_service
    if _decision_hub_service is None:
        _decision_hub_service = DecisionHubService(plugin=plugin, workspace=workspace)
        return _decision_hub_service

    if plugin is not None:
        _decision_hub_service.bind_plugin(plugin)

    return _decision_hub_service
