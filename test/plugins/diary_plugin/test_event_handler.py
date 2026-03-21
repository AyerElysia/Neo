"""diary_plugin 事件处理器测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from plugins.diary_plugin.config import DiaryConfig
from plugins.diary_plugin.event_handler import (
    AutoDiaryEventHandler,
    ContinuousMemoryPromptInjector,
)
from plugins.diary_plugin.service import DiaryService
from src.kernel.event import EventDecision


def _make_plugin(tmp_path: Path) -> Any:
    """构造最小插件对象。"""

    config = DiaryConfig()
    config.storage.base_path = str(tmp_path / "diaries")
    config.continuous_memory.base_path = str(tmp_path / "continuous_memories")
    return SimpleNamespace(config=config)


def test_auto_diary_handler_syncs_continuous_memory(tmp_path: Path) -> None:
    """自动写日记成功后应同步一条连续记忆原始项。"""

    plugin = _make_plugin(tmp_path)
    plugin.config.auto_diary.message_threshold = 1
    service = DiaryService(plugin=cast(Any, plugin))

    handler = AutoDiaryEventHandler(plugin=cast(Any, plugin))
    handler._llm_summarize = AsyncMock(return_value="我和用户讨论了连续记忆。")  # type: ignore[method-assign]
    handler._write_diary = AsyncMock(return_value=(True, "ok"))  # type: ignore[method-assign]

    chat_stream = SimpleNamespace(
        stream_id="stream_auto",
        chat_type="private",
        platform="qq",
        stream_name="Alice",
        context=SimpleNamespace(
            history_messages=[
                SimpleNamespace(
                    sender_name="Alice",
                    processed_plain_text="我们来聊连续记忆。",
                )
            ],
            unread_messages=[],
        ),
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "src.core.managers.get_stream_manager",
            lambda: SimpleNamespace(_streams={"stream_auto": chat_stream}),
        )
        monkeypatch.setattr(
            "src.app.plugin_system.api.service_api.get_service",
            lambda _signature: service,
        )
        decision, _ = asyncio.run(
            handler.execute(
                "on_chatter_step",
                {"stream_id": "stream_auto", "chat_type": "private"},
            )
        )

    memory = service.get_continuous_memory("stream_auto", "private")

    assert decision is EventDecision.SUCCESS
    assert len(memory.raw_entries) == 1
    assert memory.raw_entries[0].content == "我和用户讨论了连续记忆。"


def test_prompt_injector_appends_continuous_memory_block(tmp_path: Path) -> None:
    """prompt 注入器默认应注入连续记忆框架。"""

    plugin = _make_plugin(tmp_path)
    service = DiaryService(plugin=cast(Any, plugin))

    asyncio.run(
        service.append_continuous_memory_entry(
            stream_id="stream_prompt",
            chat_type="private",
            content="我记得用户希望保留原来的自动写日记逻辑。",
            section="其他",
        )
    )

    handler = ContinuousMemoryPromptInjector(plugin=cast(Any, plugin))
    params: dict[str, Any] = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {"stream_id": "stream_prompt", "extra": "已有额外信息"},
        "policies": {},
        "strict": False,
    }

    handler._get_service = lambda: service  # type: ignore[method-assign]

    decision, out = asyncio.run(handler.execute("on_prompt_build", params))

    assert decision is EventDecision.SUCCESS
    assert "已有额外信息" in out["values"]["extra"]
    assert "## 连续记忆" in out["values"]["extra"]
    assert "### 近期详细记忆" not in out["values"]["extra"]


def test_prompt_injector_can_include_recent_entries_when_enabled(tmp_path: Path) -> None:
    """开启开关后 prompt 注入器应包含近期详细记忆。"""

    plugin = _make_plugin(tmp_path)
    plugin.config.continuous_memory.include_recent_entries_in_prompt = True
    service = DiaryService(plugin=cast(Any, plugin))

    asyncio.run(
        service.append_continuous_memory_entry(
            stream_id="stream_prompt_detail",
            chat_type="private",
            content="我记得用户希望保留原来的自动写日记逻辑。",
            section="其他",
        )
    )

    handler = ContinuousMemoryPromptInjector(plugin=cast(Any, plugin))
    params: dict[str, Any] = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {"stream_id": "stream_prompt_detail", "extra": ""},
        "policies": {},
        "strict": False,
    }

    handler._get_service = lambda: service  # type: ignore[method-assign]

    _, out = asyncio.run(handler.execute("on_prompt_build", params))

    assert "### 近期详细记忆" in out["values"]["extra"]
    assert "我记得用户希望保留原来的自动写日记逻辑。" in out["values"]["extra"]
