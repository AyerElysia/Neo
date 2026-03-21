"""self_narrative_plugin 测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from plugins.self_narrative_plugin.commands.self_narrative_command import (
    SelfNarrativeCommand,
)
from plugins.self_narrative_plugin.components.events.prompt_injector import (
    SelfNarrativePromptInjector,
)
from plugins.self_narrative_plugin.components.events.startup_event import (
    SelfNarrativeStartupEvent,
)
from plugins.self_narrative_plugin.config import SelfNarrativeConfig
from plugins.self_narrative_plugin import service as service_module
from plugins.self_narrative_plugin.service import (
    SelfNarrativeRevision,
    SelfNarrativeService,
    SelfNarrativeState,
    initialize_self_narrative_service,
)
from src.kernel.event import EventDecision


@pytest.fixture(autouse=True)
def reset_singleton() -> None:
    """重置模块级单例，避免测试间状态泄漏。"""

    service_module._SERVICE_INSTANCE = None
    service_module._STREAM_LOCKS.clear()
    yield
    service_module._SERVICE_INSTANCE = None
    service_module._STREAM_LOCKS.clear()


def _make_plugin(tmp_path: Path, *, schedule_enabled: bool = True) -> Any:
    config = SelfNarrativeConfig()
    config.storage.base_path = str(tmp_path / "self_narratives")
    config.schedule.enabled = schedule_enabled
    return SimpleNamespace(config=config)


def _make_service(tmp_path: Path, *, schedule_enabled: bool = True) -> SelfNarrativeService:
    return SelfNarrativeService(plugin=cast(Any, _make_plugin(tmp_path, schedule_enabled=schedule_enabled)))


def test_state_round_trip_preserves_content() -> None:
    """自我叙事状态应可完整序列化和反序列化。"""

    state = SelfNarrativeState(
        stream_id="stream_1",
        chat_type="group",
        platform="qq",
        stream_name="测试群",
        updated_at="2026-03-22T00:00:00+08:00",
        last_daily_ref_date="2026-03-21",
        last_manual_update_at="2026-03-22T00:10:00+08:00",
        self_view=["我最近更安静"],
        ongoing_patterns=["我在熟悉关系里更放松"],
        open_loops=["我还在整理这段变化"],
        identity_bounds=["我更重视真实表达，而不是迎合"],
        history=[
            SelfNarrativeRevision(
                revision_id="rev_1",
                created_at="2026-03-22T00:00:00+08:00",
                trigger="daily",
                reference_date="2026-03-21",
                source_summary="日记摘要=3",
                self_view=["我最近更安静"],
                ongoing_patterns=["我在熟悉关系里更放松"],
                open_loops=["我还在整理这段变化"],
                identity_bounds=["我更重视真实表达，而不是迎合"],
            )
        ],
    )

    restored = SelfNarrativeState.from_dict(state.to_dict())

    assert restored.stream_id == state.stream_id
    assert restored.chat_type == state.chat_type
    assert restored.platform == state.platform
    assert restored.stream_name == state.stream_name
    assert restored.self_view == state.self_view
    assert restored.ongoing_patterns == state.ongoing_patterns
    assert restored.open_loops == state.open_loops
    assert restored.identity_bounds == state.identity_bounds
    assert restored.history[0].source_summary == "日记摘要=3"


def test_update_narrative_persists_daily_revision(tmp_path: Path) -> None:
    """每日更新应写入磁盘并记录历史。"""

    service = _make_service(tmp_path)

    async def fake_llm(**_: Any) -> dict[str, list[str]]:
        return {
            "self_view": ["我最近更安静"],
            "ongoing_patterns": ["我在熟悉关系里更放松"],
            "open_loops": ["我还在整理这段变化"],
            "identity_bounds": ["我更重视真实表达，而不是迎合"],
        }

    service._call_llm_for_update = fake_llm  # type: ignore[method-assign]

    ok, message = asyncio.run(
        service.update_narrative(
            stream_id="stream_daily",
            chat_type="private",
            platform="qq",
            stream_name="Alice",
            trigger="daily",
        )
    )

    state = service.get_state("stream_daily", "private")
    expected_ref_date = (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()
    state_path = tmp_path / "self_narratives" / "private" / "stream_daily.json"

    assert ok is True
    assert "已更新" in message
    assert state_path.exists()
    assert state.last_daily_ref_date == expected_ref_date
    assert len(state.history) == 1
    assert state.history[0].reference_date == expected_ref_date
    assert state.self_view == ["我最近更安静"]
    assert state.ongoing_patterns == ["我在熟悉关系里更放松"]
    assert state.open_loops == ["我还在整理这段变化"]
    assert "我更重视真实表达，而不是迎合" in state.identity_bounds
    assert len(state.identity_bounds) == 3


def test_manual_update_enforces_cooldown(tmp_path: Path) -> None:
    """手动更新应遵守冷却时间。"""

    service = _make_service(tmp_path)

    async def fake_llm(**_: Any) -> dict[str, list[str]]:
        return {"self_view": ["我在手动更新"], "ongoing_patterns": [], "open_loops": [], "identity_bounds": []}

    service._call_llm_for_update = fake_llm  # type: ignore[method-assign]

    ok1, _ = asyncio.run(
        service.update_narrative(
            stream_id="stream_manual",
            chat_type="private",
            trigger="manual",
        )
    )
    ok2, message2 = asyncio.run(
        service.update_narrative(
            stream_id="stream_manual",
            chat_type="private",
            trigger="manual",
        )
    )

    assert ok1 is True
    assert ok2 is False
    assert "冷却" in message2


def test_initialize_starts_and_stops_scheduler_when_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """启用自动调度时应启动并在关闭时清理定时任务。"""

    service = _make_service(tmp_path, schedule_enabled=True)
    fake_scheduler = SimpleNamespace(
        create_schedule=AsyncMock(return_value="sched-1"),
        remove_schedule=AsyncMock(return_value=None),
    )

    monkeypatch.setattr(
        "plugins.self_narrative_plugin.service.get_unified_scheduler",
        lambda: fake_scheduler,
    )
    service._catch_up_on_startup = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(service.initialize())
    asyncio.run(service.shutdown())

    assert service._initialized is False
    assert fake_scheduler.create_schedule.await_count == 1
    assert fake_scheduler.remove_schedule.await_count == 1
    assert fake_scheduler.remove_schedule.await_args.args == ("sched-1",)
    assert service._catch_up_on_startup.await_count == 1


def test_initialize_skips_scheduler_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭自动调度时不应创建定时任务。"""

    service = _make_service(tmp_path, schedule_enabled=False)
    fake_scheduler = SimpleNamespace(
        create_schedule=AsyncMock(return_value="sched-1"),
        remove_schedule=AsyncMock(return_value=None),
    )

    monkeypatch.setattr(
        "plugins.self_narrative_plugin.service.get_unified_scheduler",
        lambda: fake_scheduler,
    )
    service._catch_up_on_startup = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(service.initialize())
    asyncio.run(service.shutdown())

    assert service._initialized is False
    assert service._schedule_task_id is None
    assert fake_scheduler.create_schedule.await_count == 0
    assert fake_scheduler.remove_schedule.await_count == 0
    assert service._catch_up_on_startup.await_count == 0


def test_prompt_block_respects_identity_bounds_switch(tmp_path: Path) -> None:
    """关闭稳定边界显示时，只有边界内容的状态不应生成 prompt 块。"""

    service = _make_service(tmp_path)
    service._cfg().plugin.include_identity_bounds_in_prompt = False

    state = SelfNarrativeState.empty(
        stream_id="stream_prompt",
        chat_type="private",
        default_identity_bounds=["我更重视真实表达，而不是迎合"],
    )
    service._save_state(state)

    assert service.render_prompt_block("stream_prompt", "private") == ""


def test_prompt_injector_appends_rendered_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """prompt 注入器应把自我叙事写入 extra。"""

    plugin = _make_plugin(tmp_path)
    service = _make_service(tmp_path)
    initialize_self_narrative_service(plugin)

    state = SelfNarrativeState.empty(
        stream_id="stream_prompt",
        chat_type="private",
        default_identity_bounds=["我更重视真实表达，而不是迎合"],
    )
    state.self_view = ["我最近更安静"]
    state.ongoing_patterns = ["我在熟悉关系里更放松"]
    service._save_state(state)

    handler = SelfNarrativePromptInjector(plugin)
    params: dict[str, Any] = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {"stream_id": "stream_prompt", "chat_type": "private", "extra": "已有额外信息"},
        "policies": {},
        "strict": False,
    }

    monkeypatch.setattr(
        "plugins.self_narrative_plugin.components.events.prompt_injector.get_self_narrative_service",
        lambda: service,
    )

    decision, out = asyncio.run(handler.execute("on_prompt_build", params))

    assert decision is EventDecision.SUCCESS
    assert "已有额外信息" in out["values"]["extra"]
    assert "## 自我叙事" in out["values"]["extra"]
    assert "### 当前自我理解" in out["values"]["extra"]


def test_command_routes_use_current_stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """命令路由应从当前聊天流读取信息并调用对应服务接口。"""

    plugin = _make_plugin(tmp_path)
    service = SimpleNamespace(
        update_narrative=AsyncMock(return_value=(True, "自我叙事已更新")),
        render_state_summary=lambda **_: "状态摘要",
        render_history=lambda **_: "历史记录",
        reset_narrative=AsyncMock(return_value=(True, "自我叙事已重置")),
    )
    chat_stream = SimpleNamespace(
        stream_id="stream_cmd",
        chat_type="group",
        platform="qq",
        stream_name="测试群",
    )

    monkeypatch.setattr(
        "plugins.self_narrative_plugin.commands.self_narrative_command.get_self_narrative_service",
        lambda: service,
    )
    monkeypatch.setattr(
        "src.core.managers.get_stream_manager",
        lambda: SimpleNamespace(_streams={"stream_cmd": chat_stream}),
    )

    command = SelfNarrativeCommand(plugin=cast(Any, plugin), stream_id="stream_cmd")

    ok_update, msg_update = asyncio.run(command.update())
    ok_view, msg_view = asyncio.run(command.view())
    ok_history, msg_history = asyncio.run(command.history())
    ok_reset, msg_reset = asyncio.run(command.reset())

    assert ok_update is True
    assert msg_update == "自我叙事已更新"
    assert service.update_narrative.await_args.kwargs["stream_id"] == "stream_cmd"
    assert service.update_narrative.await_args.kwargs["chat_type"] == "group"
    assert ok_view is True and msg_view == "状态摘要"
    assert ok_history is True and msg_history == "历史记录"
    assert ok_reset is True and msg_reset == "自我叙事已重置"


def test_startup_event_initializes_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """ON_START 事件应触发服务初始化。"""

    plugin = _make_plugin(Path("/tmp"))
    service = SimpleNamespace(initialize=AsyncMock(return_value=None))

    monkeypatch.setattr(
        "plugins.self_narrative_plugin.components.events.startup_event.get_self_narrative_service",
        lambda: service,
    )

    handler = SelfNarrativeStartupEvent(plugin)
    decision, _ = asyncio.run(handler.execute("on_start", {}))

    assert decision is EventDecision.SUCCESS
    assert service.initialize.await_count == 1
