"""drive_core_plugin 测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from plugins.drive_core_plugin.components.events.drive_core_prompt_injector import (
    DriveCorePromptInjector,
)
from plugins.drive_core_plugin.components.events.drive_core_scan_event import (
    DriveCoreScanEvent,
)
from plugins.drive_core_plugin.commands.drive_core_command import DriveCoreCommand
from plugins.drive_core_plugin.config import DriveCoreConfig
from plugins.drive_core_plugin import service as service_module
from plugins.drive_core_plugin.plugin import DriveCorePlugin
from plugins.drive_core_plugin.service import (
    DriveCoreService,
    DriveState,
    DriveWorkspace,
    initialize_drive_core_service,
)
from src.kernel.event import EventDecision


@pytest.fixture(autouse=True)
def reset_singleton() -> None:
    """避免测试间单例与锁对象残留。"""

    service_module._SERVICE_INSTANCE = None
    service_module._STREAM_LOCKS.clear()
    yield
    service_module._SERVICE_INSTANCE = None
    service_module._STREAM_LOCKS.clear()


def _make_plugin(tmp_path: Path) -> DriveCorePlugin:
    config = DriveCoreConfig()
    config.storage.base_path = str(tmp_path / "drive_core")
    return DriveCorePlugin(config=config)


def _make_service(tmp_path: Path) -> DriveCoreService:
    return DriveCoreService(plugin=_make_plugin(tmp_path))


def test_plugin_returns_components() -> None:
    """插件应返回 service、command 和事件处理器组件。"""

    plugin = DriveCorePlugin(config=DriveCoreConfig())
    components = plugin.get_components()

    assert DriveCoreService in components
    assert DriveCoreCommand in components
    assert DriveCoreScanEvent in components
    assert DriveCorePromptInjector in components


def test_initialize_rebinds_service_plugin(tmp_path: Path) -> None:
    """重复初始化时，服务应切换到最新插件实例。"""

    plugin_a = _make_plugin(tmp_path)
    plugin_b = _make_plugin(tmp_path)

    service_a = initialize_drive_core_service(plugin_a)
    service_b = initialize_drive_core_service(plugin_b)

    assert service_a is service_b
    assert service_b.plugin is plugin_b


def test_shared_persona_prompt_is_awaited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """共享人设提示词应正确 await diary 的异步构建函数。"""

    service = _make_service(tmp_path)
    fake_prompt = AsyncMock(return_value="shared persona")
    monkeypatch.setattr(
        "plugins.diary_plugin.prompts.build_shared_persona_prompt",
        fake_prompt,
    )

    prompt = asyncio.run(
        service._get_shared_persona_prompt(
            chat_stream=SimpleNamespace(
                platform="qq",
                chat_type="private",
                bot_nickname="AyerElysia",
                bot_id="bot_1",
            ),
            chat_type="private",
            platform="qq",
            stream_name="Alice",
        )
    )

    assert prompt == "shared persona"
    assert fake_prompt.await_count == 1


def test_observe_chat_turn_persists_workspace_and_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """推进应持久化工作区，并在阈值前只累计计数。"""

    service = _make_service(tmp_path)
    service._cfg().scan.trigger_every_n_messages = 2

    async def fake_llm(**_: Any) -> dict[str, Any]:
        return {
            "topic": "小星星的情感",
            "question": "我现在到底想确认什么？",
            "hypothesis": "我在靠近一个重要关系。",
            "next_action": "继续查日记",
            "open_questions": ["这段关系意味着什么？"],
            "should_close": False,
            "summary": "我先形成一个初步判断。",
        }

    service._call_llm_for_workspace = fake_llm  # type: ignore[method-assign]

    async def _run() -> tuple[tuple[bool, str], tuple[bool, str]]:
        first = await service.observe_chat_turn(
            stream_id="stream_drive",
            chat_type="private",
            platform="qq",
            stream_name="Alice",
            trigger="auto",
        )
        second = await service.observe_chat_turn(
            stream_id="stream_drive",
            chat_type="private",
            platform="qq",
            stream_name="Alice",
            trigger="auto",
        )
        return first, second

    first, second = asyncio.run(_run())
    state = service.get_state("stream_drive", "private")

    assert first[0] is True
    assert second[0] is True
    assert state.message_count_since_scan == 1
    assert state.current_workspace is not None
    assert state.current_workspace.topic == "小星星的情感"
    assert state.current_workspace.question == "我现在到底想确认什么？"
    assert "累计推进计数" in second[1]


def test_prompt_block_uses_current_workspace(tmp_path: Path) -> None:
    """prompt 注入块应反映当前工作区状态。"""

    service = _make_service(tmp_path)
    state = DriveState.empty(
        stream_id="stream_prompt",
        chat_type="private",
        platform="qq",
        stream_name="Alice",
    )
    state.current_workspace = DriveWorkspace(
        task_id="drive_1",
        topic="小星星",
        question="我现在到底想确认什么？",
        hypothesis="我在靠近一个重要关系。",
        next_action="继续查日记",
        summary="",
        conclusion="",
        should_close=False,
        status="open",
        trigger="auto",
        step_index=1,
        max_steps=4,
        created_at="2026-03-22T00:00:00+08:00",
        updated_at="2026-03-22T00:00:00+08:00",
        evidence=["日记里提到了小星星"],
        open_questions=["这段关系意味着什么？"],
        tool_trace=["auto"],
        working_notes=[],
        source_summary="日记里提到了小星星",
    )
    service._save_state(state)

    block = service.render_prompt_block("stream_prompt", "private")

    assert "【内驱力】" in block
    assert "我现在到底想确认什么？" in block
    assert "继续查日记" in block
