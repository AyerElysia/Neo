"""diary_plugin 服务测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from plugins.diary_plugin.config import DiaryConfig
from plugins.diary_plugin.service import (
    ContinuousMemoryEntry,
    ContinuousMemorySummary,
    DiaryService,
)


def _make_service(tmp_path: Path) -> DiaryService:
    """创建带临时目录配置的 DiaryService。"""

    config = DiaryConfig()
    config.storage.base_path = str(tmp_path / "diaries")
    config.continuous_memory.base_path = str(tmp_path / "continuous_memories")
    plugin = SimpleNamespace(config=config)
    return DiaryService(plugin=cast(Any, plugin))


def test_append_entry_keeps_daily_diary_behavior(tmp_path: Path) -> None:
    """原有按天日记写入逻辑应保持可用。"""

    service = _make_service(tmp_path)

    success, message = service.append_entry("我今天在修 diary_plugin。", section="下午")

    assert success is True
    assert "日记已更新" in message

    today = service.read_today()
    assert today.exists is True
    assert len(today.events) == 1
    assert "我今天在修 diary_plugin。" in today.events[0].content


def test_continuous_memory_is_isolated_by_stream(tmp_path: Path) -> None:
    """不同聊天流的连续记忆应彼此隔离。"""

    service = _make_service(tmp_path)
    service._call_llm_for_continuous_memory_compression = AsyncMock(  # type: ignore[method-assign]
        return_value="压缩摘要"
    )

    async def _run() -> tuple[bool, bool]:
        ok1, _ = await service.append_continuous_memory_entry(
            stream_id="private_stream",
            chat_type="private",
            content="第一条私聊自动日记",
            section="上午",
            platform="qq",
            stream_name="Alice",
        )
        ok2, _ = await service.append_continuous_memory_entry(
            stream_id="group_stream",
            chat_type="group",
            content="第一条群聊自动日记",
            section="下午",
            platform="qq",
            stream_name="Test Group",
        )
        return ok1, ok2

    ok1, ok2 = asyncio.run(_run())

    private_memory = service.get_continuous_memory("private_stream", "private")
    group_memory = service.get_continuous_memory("group_stream", "group")

    assert ok1 is True
    assert ok2 is True
    assert len(private_memory.raw_entries) == 1
    assert len(group_memory.raw_entries) == 1
    assert private_memory.raw_entries[0].content == "第一条私聊自动日记"
    assert group_memory.raw_entries[0].content == "第一条群聊自动日记"


def test_continuous_memory_compresses_every_five_entries_and_recurses(
    tmp_path: Path,
) -> None:
    """连续记忆应每 5 条压缩并支持递归压缩到更高层。"""

    service = _make_service(tmp_path)

    async def _fake_compress(*, source_texts: list[str], target_level: int) -> str:
        return f"L{target_level}:{len(source_texts)}"

    service._call_llm_for_continuous_memory_compression = _fake_compress  # type: ignore[method-assign]

    async def _run() -> None:
        for index in range(25):
            success, message = await service.append_continuous_memory_entry(
                stream_id="stream_a",
                chat_type="private",
                content=f"自动日记项 {index}",
                section="其他",
            )
            assert success is True, message

    asyncio.run(_run())

    memory = service.get_continuous_memory("stream_a", "private")

    assert len(memory.raw_entries) == 0
    assert len(memory.summaries_by_level.get(1, [])) == 0
    assert len(memory.summaries_by_level.get(2, [])) == 1
    assert memory.summaries_by_level[2][0].content == "L2:5"


def test_render_continuous_memory_for_prompt_omits_recent_entries_by_default(
    tmp_path: Path,
) -> None:
    """默认 prompt 渲染应只保留压缩层摘要。"""

    service = _make_service(tmp_path)
    memory = service.get_continuous_memory("stream_x", "private")
    memory.raw_entries.extend(
        [
            ContinuousMemoryEntry(
                entry_id="raw_1",
                created_at="2026-03-21T10:00:00+08:00",
                diary_date="2026-03-21",
                section="上午",
                content="我和用户讨论了连续记忆。",
            ),
            ContinuousMemoryEntry(
                entry_id="raw_2",
                created_at="2026-03-21T11:00:00+08:00",
                diary_date="2026-03-21",
                section="上午",
                content="我确认要保留原来的自动写日记逻辑。",
            ),
        ]
    )
    memory.summaries_by_level[1] = [
        ContinuousMemorySummary(
            summary_id="l1_1",
            level=1,
            created_at="2026-03-21T12:00:00+08:00",
            source_ids=["raw_0", "raw_1", "raw_2", "raw_3", "raw_4"],
            content="我最近反复和用户讨论 diary_plugin 的连续记忆改造方向。",
        )
    ]
    service._save_continuous_memory(memory)

    rendered = service.render_continuous_memory_for_prompt("stream_x", "private")

    assert "## 连续记忆" in rendered
    assert "### 压缩记忆・L1" in rendered
    assert "### 近期详细记忆" not in rendered
    assert "我最近反复和用户讨论 diary_plugin 的连续记忆改造方向。" in rendered


def test_render_continuous_memory_for_prompt_can_include_recent_entries(
    tmp_path: Path,
) -> None:
    """开启开关后应显示近期详细记忆。"""

    service = _make_service(tmp_path)
    service._cfg().continuous_memory.include_recent_entries_in_prompt = True
    memory = service.get_continuous_memory("stream_y", "private")
    memory.raw_entries.append(
        ContinuousMemoryEntry(
            entry_id="raw_1",
            created_at="2026-03-21T11:00:00+08:00",
            diary_date="2026-03-21",
            section="上午",
            content="我确认要保留原来的自动写日记逻辑。",
        )
    )
    service._save_continuous_memory(memory)

    rendered = service.render_continuous_memory_for_prompt("stream_y", "private")

    assert "### 近期详细记忆" in rendered
    assert "我确认要保留原来的自动写日记逻辑。" in rendered
