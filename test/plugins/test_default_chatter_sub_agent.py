"""default_chatter.sub_agent 行为测试。"""

from __future__ import annotations

from typing import Any

import pytest

from plugins.default_chatter.config import DefaultChatterConfig
from plugins.default_chatter.plugin import DefaultChatter, DefaultChatterPlugin
from src.core.models.stream import ChatStream


def _build_chatter() -> DefaultChatter:
    """构造默认聊天器实例。"""
    config = DefaultChatterConfig.from_dict({"plugin": {"enabled": True, "mode": "enhanced"}})
    plugin = DefaultChatterPlugin(config=config)
    return DefaultChatter(stream_id="test_stream", plugin=plugin)


@pytest.mark.asyncio
async def test_sub_agent_is_disabled_in_private_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """私聊场景应跳过 decide_should_respond。"""
    chatter = _build_chatter()
    stream = ChatStream(stream_id="s_private", platform="qq", chat_type="private")

    called = {"value": False}

    async def _fake_decide(**_kwargs: Any) -> dict[str, object]:
        called["value"] = True
        return {"reason": "should not be called", "should_respond": False}

    monkeypatch.setattr("plugins.default_chatter.plugin.decide_should_respond", _fake_decide)

    result = await chatter.sub_agent("hello", [], stream)

    assert result["should_respond"] is True
    assert "私聊场景" in result["reason"]
    assert called["value"] is False


@pytest.mark.asyncio
async def test_sub_agent_keeps_decision_flow_in_group_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """群聊场景应继续走 decide_should_respond。"""
    chatter = _build_chatter()
    stream = ChatStream(stream_id="s_group", platform="qq", chat_type="group")

    captured: dict[str, Any] = {}

    async def _fake_decide(**kwargs: Any) -> dict[str, object]:
        captured.update(kwargs)
        return {"reason": "group decision", "should_respond": False}

    monkeypatch.setattr("plugins.default_chatter.plugin.decide_should_respond", _fake_decide)

    result = await chatter.sub_agent("group-msg", [], stream)

    assert result == {"reason": "group decision", "should_respond": False}
    assert captured["chatter"] is chatter
    assert captured["chat_stream"] is stream
    assert captured["unreads_text"] == "group-msg"
    assert captured["fallback_prompt"]
    assert "logger" in captured
