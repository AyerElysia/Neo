"""unfinished_thought_plugin 测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from plugins.unfinished_thought_plugin.commands.unfinished_thought_command import (
    UnfinishedThoughtCommand,
)
from plugins.unfinished_thought_plugin.components.events.prompt_injector import (
    UnfinishedThoughtPromptInjector,
)
from plugins.unfinished_thought_plugin.components.events.scan_trigger_event import (
    UnfinishedThoughtScanEvent,
)
from plugins.unfinished_thought_plugin.config import UnfinishedThoughtConfig
from plugins.unfinished_thought_plugin import service as service_module
from plugins.unfinished_thought_plugin.service import (
    ThoughtScanRecord,
    UnfinishedThoughtItem,
    UnfinishedThoughtService,
    UnfinishedThoughtState,
)
from src.kernel.event import EventDecision


@pytest.fixture(autouse=True)
def reset_locks() -> None:
    """避免测试间锁对象残留。"""

    service_module._STREAM_LOCKS.clear()
    yield
    service_module._STREAM_LOCKS.clear()


def _make_plugin(tmp_path: Path) -> Any:
    config = UnfinishedThoughtConfig()
    config.storage.base_path = str(tmp_path / "unfinished_thoughts")
    return SimpleNamespace(config=config)


def _make_service(tmp_path: Path) -> UnfinishedThoughtService:
    return UnfinishedThoughtService(plugin=cast(Any, _make_plugin(tmp_path)))


def test_state_round_trip_preserves_items_and_history() -> None:
    """状态应可完整序列化和反序列化。"""

    state = UnfinishedThoughtState(
        stream_id="stream_1",
        chat_type="private",
        platform="qq",
        stream_name="Alice",
        updated_at="2026-03-22T00:00:00+08:00",
        message_count_since_scan=3,
        thoughts=[
            UnfinishedThoughtItem(
                thought_id="th_1",
                title="刚才的话题",
                content="我刚刚其实还没把那个话题想完",
                status="open",
                priority=2,
                reason="被打断",
                source_event="auto",
                created_at="2026-03-22T00:00:00+08:00",
                updated_at="2026-03-22T00:00:00+08:00",
                last_mentioned_at="2026-03-22T00:00:00+08:00",
                mention_count=1,
            )
        ],
        history=[
            ThoughtScanRecord(
                record_id="scan_1",
                created_at="2026-03-22T00:00:00+08:00",
                trigger="auto",
                source_summary="new=1, update=0, resolved=0, paused=0",
                recent_message_count=12,
                new_count=1,
                updated_count=0,
                resolved_count=0,
                paused_count=0,
            )
        ],
    )

    restored = UnfinishedThoughtState.from_dict(state.to_dict())

    assert restored.stream_id == state.stream_id
    assert restored.chat_type == state.chat_type
    assert restored.message_count_since_scan == 3
    assert restored.thoughts[0].thought_id == "th_1"
    assert restored.history[0].record_id == "scan_1"


def test_record_chat_turn_triggers_scan_after_threshold(tmp_path: Path) -> None:
    """固定对话数达到阈值后应自动扫描并重置计数。"""

    service = _make_service(tmp_path)
    service._cfg().scan.trigger_every_n_messages = 2

    async def fake_scan(**_: Any) -> dict[str, Any]:
        return {
            "new_thoughts": [
                {
                    "title": "刚才的话题",
                    "content": "我刚刚其实还没把那个话题想完",
                    "priority": 2,
                    "reason": "被打断",
                }
            ],
            "updates": [],
            "resolved_ids": [],
            "paused_ids": [],
        }

    service._call_llm_for_scan = fake_scan  # type: ignore[method-assign]

    async def _run() -> tuple[str, str]:
        first = await service.record_chat_turn(
            stream_id="stream_auto",
            chat_type="private",
            platform="qq",
            stream_name="Alice",
        )
        second = await service.record_chat_turn(
            stream_id="stream_auto",
            chat_type="private",
            platform="qq",
            stream_name="Alice",
        )
        return first[1], second[1]

    first_msg, second_msg = asyncio.run(_run())
    state = service.get_state("stream_auto", "private")

    assert "计数 1/2" in first_msg
    assert "已更新" in second_msg
    assert state.message_count_since_scan == 0
    assert len(state.thoughts) == 1
    assert state.thoughts[0].title == "刚才的话题"
    assert len(state.history) == 1


def test_record_chat_turn_restores_counter_when_scan_fails(tmp_path: Path) -> None:
    """自动扫描失败时应恢复触发前计数，避免丢失下一次扫描节奏。"""

    service = _make_service(tmp_path)
    service._cfg().scan.trigger_every_n_messages = 2
    service._call_llm_for_scan = AsyncMock(return_value=None)  # type: ignore[method-assign]

    async def _run() -> tuple[str, str]:
        first = await service.record_chat_turn(
            stream_id="stream_fail",
            chat_type="private",
            platform="qq",
            stream_name="Alice",
        )
        second = await service.record_chat_turn(
            stream_id="stream_fail",
            chat_type="private",
            platform="qq",
            stream_name="Alice",
        )
        return first[1], second[1]

    first_msg, second_msg = asyncio.run(_run())
    state = service.get_state("stream_fail", "private")

    assert "计数 1/2" in first_msg
    assert "未完成念头扫描失败" in second_msg
    assert state.message_count_since_scan == 2


def test_prompt_injector_randomly_injects_active_thoughts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """prompt 注入应从活跃念头里随机抽取 1-3 条。"""

    plugin = _make_plugin(tmp_path)
    service = _make_service(tmp_path)

    state = UnfinishedThoughtState(
        stream_id="stream_prompt",
        chat_type="private",
        thoughts=[
            UnfinishedThoughtItem(
                thought_id="th_1",
                title="刚才的话题",
                content="我刚刚其实还没把那个话题想完",
                status="open",
                priority=2,
                created_at="2026-03-22T00:00:00+08:00",
                updated_at="2026-03-22T00:00:00+08:00",
                last_mentioned_at="2026-03-22T00:00:00+08:00",
            ),
            UnfinishedThoughtItem(
                thought_id="th_2",
                title="压下的情绪",
                content="我现在还是有点想先把情绪放一放",
                status="paused",
                priority=1,
                created_at="2026-03-22T00:00:00+08:00",
                updated_at="2026-03-22T00:00:00+08:00",
                last_mentioned_at="2026-03-22T00:00:00+08:00",
            ),
            UnfinishedThoughtItem(
                thought_id="th_3",
                title="已结束",
                content="这件事已经说完了",
                status="resolved",
                priority=1,
                created_at="2026-03-22T00:00:00+08:00",
                updated_at="2026-03-22T00:00:00+08:00",
                last_mentioned_at="2026-03-22T00:00:00+08:00",
            ),
        ],
    )
    service._save_state(state)

    monkeypatch.setattr("plugins.unfinished_thought_plugin.service.random.randint", lambda a, b: 2)
    monkeypatch.setattr(
        "plugins.unfinished_thought_plugin.service.random.sample",
        lambda seq, k: list(seq)[:k],
    )
    monkeypatch.setattr(
        "plugins.unfinished_thought_plugin.components.events.prompt_injector.get_unfinished_thought_service",
        lambda: service,
    )

    handler = UnfinishedThoughtPromptInjector(plugin=cast(Any, plugin))
    params: dict[str, Any] = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {"stream_id": "stream_prompt", "chat_type": "private", "extra": "已有额外信息"},
        "policies": {},
        "strict": False,
    }

    decision, out = asyncio.run(handler.execute("on_prompt_build", params))

    assert decision is EventDecision.SUCCESS
    assert "已有额外信息" in out["values"]["extra"]
    assert "## 未完成念头" in out["values"]["extra"]
    assert "刚才的话题" in out["values"]["extra"]
    assert "已结束" not in out["values"]["extra"]


def test_command_routes_manage_thoughts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """命令应支持查看、添加、扫描、暂停、恢复和清空。"""

    plugin = _make_plugin(tmp_path)
    service = _make_service(tmp_path)
    chat_stream = SimpleNamespace(
        stream_id="stream_cmd",
        chat_type="group",
        platform="qq",
        stream_name="测试群",
    )

    monkeypatch.setattr(
        "plugins.unfinished_thought_plugin.commands.unfinished_thought_command.get_unfinished_thought_service",
        lambda: service,
    )
    monkeypatch.setattr(
        "src.core.managers.get_stream_manager",
        lambda: SimpleNamespace(_streams={"stream_cmd": chat_stream}),
    )

    command = UnfinishedThoughtCommand(plugin=cast(Any, plugin), stream_id="stream_cmd")

    ok_add, msg_add = asyncio.run(command.add("我还没想完这件事"))
    state = service.get_state("stream_cmd", "group")
    thought_id = state.thoughts[0].thought_id

    async def fake_scan(**_: Any) -> dict[str, Any]:
        return {
            "new_thoughts": [
                {
                    "title": "后续补充",
                    "content": "这件事我后来又想到一点",
                    "priority": 1,
                    "reason": "重新浮现",
                }
            ],
            "updates": [],
            "resolved_ids": [],
            "paused_ids": [],
        }

    service._call_llm_for_scan = fake_scan  # type: ignore[method-assign]

    ok_view, msg_view = asyncio.run(command.view())
    ok_history, msg_history = asyncio.run(command.history())
    ok_scan, msg_scan = asyncio.run(command.scan())
    ok_pause, msg_pause = asyncio.run(command.pause(thought_id))
    ok_resolve, msg_resolve = asyncio.run(command.resolve(thought_id))
    ok_clear, msg_clear = asyncio.run(command.clear())

    assert ok_add is True
    assert "已添加未完成念头" in msg_add
    assert ok_view is True and "未完成念头" in msg_view
    assert ok_history is True
    assert ok_scan is True and "已更新" in msg_scan
    assert ok_pause is True and "paused" in msg_pause
    assert ok_resolve is True and "resolved" in msg_resolve
    assert ok_clear is True and "已清空" in msg_clear


def test_command_execute_preserves_add_text_with_spaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """add 子命令应把空格内容视为完整文本。"""

    plugin = _make_plugin(tmp_path)
    service = _make_service(tmp_path)
    chat_stream = SimpleNamespace(
        stream_id="stream_cmd_space",
        chat_type="private",
        platform="qq",
        stream_name="Alice",
    )

    monkeypatch.setattr(
        "plugins.unfinished_thought_plugin.commands.unfinished_thought_command.get_unfinished_thought_service",
        lambda: service,
    )
    monkeypatch.setattr(
        "src.core.managers.get_stream_manager",
        lambda: SimpleNamespace(_streams={"stream_cmd_space": chat_stream}),
    )

    command = UnfinishedThoughtCommand(plugin=cast(Any, plugin), stream_id="stream_cmd_space")

    ok, msg = asyncio.run(command.execute("add 我 还 没 想 完 这 件 事"))
    state = service.get_state("stream_cmd_space", "private")

    assert ok is True
    assert "已添加未完成念头" in msg
    assert state.thoughts[0].content == "我 还 没 想 完 这 件 事"


def test_scan_event_counts_and_triggers_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """事件处理器应把对话计数与自动扫描传给 service。"""

    plugin = _make_plugin(Path("/tmp"))
    service = SimpleNamespace(record_chat_turn=AsyncMock(return_value=(True, "计数 1/8")))

    monkeypatch.setattr(
        "plugins.unfinished_thought_plugin.components.events.scan_trigger_event.get_unfinished_thought_service",
        lambda: service,
    )

    handler = UnfinishedThoughtScanEvent(plugin=cast(Any, plugin))
    params = {"stream_id": "stream_evt", "chat_type": "private", "platform": "qq", "stream_name": "Alice"}
    decision, out = asyncio.run(handler.execute("on_chatter_step", params))

    assert decision is EventDecision.SUCCESS
    assert out is params
    assert service.record_chat_turn.await_count == 1
