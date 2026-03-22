"""diary_plugin 连续记忆 prompt 注入测试。"""

from __future__ import annotations

import asyncio
from typing import Any
from types import SimpleNamespace

from plugins.diary_plugin.config import DiaryConfig
from plugins.diary_plugin.event_handler import ContinuousMemoryPromptInjector
from src.kernel.event import EventDecision


def test_continuous_memory_injects_into_dedicated_prompt_block() -> None:
    """连续记忆应注入到 system prompt 的 dedicated continuous_memory 区块。"""

    config = DiaryConfig()
    config.continuous_memory.enabled = True
    config.continuous_memory.inject_prompt = True
    config.continuous_memory.target_prompt_names = ["default_chatter_system_prompt"]

    handler = ContinuousMemoryPromptInjector(plugin=SimpleNamespace(config=config))

    block = "## 连续记忆\n\n- [L1] 已经存在的内容"

    class _DummyService:
        def render_continuous_memory_for_prompt(self, stream_id: str, chat_type: str | None = None) -> str:
            assert stream_id == "sid_x"
            assert chat_type == "private"
            return block

    handler._get_service = lambda: _DummyService()  # type: ignore[method-assign]

    params: dict[str, Any] = {
        "name": "default_chatter_system_prompt",
        "template": "{extra_info}\n{continuous_memory}",
        "values": {
            "stream_id": "sid_x",
            "chat_type": "private",
            "continuous_memory": "old",
            "extra_info": "keep",
        },
        "policies": {},
        "strict": False,
    }

    decision, out = asyncio.run(handler.execute("on_prompt_build", params))

    assert decision is EventDecision.SUCCESS
    assert out["values"]["continuous_memory"] == block
    assert out["values"]["extra_info"] == "keep"
