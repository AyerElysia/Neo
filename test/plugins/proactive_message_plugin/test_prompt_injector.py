"""主动时间 prompt 注入器测试。"""

from __future__ import annotations

import asyncio

from plugins.proactive_message_plugin.config import ProactiveMessageConfig
from plugins.proactive_message_plugin.plugin import (
    ProactiveMessagePlugin,
    ProactiveTimePromptInjector,
)


def test_time_prompt_injector_appends_block_to_extra() -> None:
    """prompt 注入器应向 extra 追加时间块。"""
    plugin = ProactiveMessagePlugin(ProactiveMessageConfig())
    plugin.config.settings.enabled = True
    plugin.config.settings.inject_prompt = True
    plugin.config.settings.target_prompt_names = ["default_chatter_user_prompt"]
    plugin.config.settings.time_prompt_title = "主动时间感知"
    plugin._service = type(
        "_FakeService",
        (),
        {
            "render_time_prompt_block": staticmethod(
                lambda *_args, **_kwargs: "【主动时间感知】\n- 现在时间：2026年3月22日"
            )
        },
    )()
    injector = ProactiveTimePromptInjector(plugin)
    params = {
        "name": "default_chatter_user_prompt",
        "values": {"stream_id": "stream_1", "extra": "旧内容"},
    }

    _, updated = asyncio.run(injector.execute("on_prompt_build", params))

    assert "旧内容" in updated["values"]["extra"]
    assert "主动时间感知" in updated["values"]["extra"]
    assert "现在时间" in updated["values"]["extra"]
