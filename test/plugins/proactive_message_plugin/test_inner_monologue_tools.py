"""内心独白工具集测试。"""

from __future__ import annotations

from plugins.proactive_message_plugin import inner_monologue


def test_inner_monologue_exposes_emoji_action() -> None:
    """内心独白阶段应能看到表情包 action。"""
    assert inner_monologue.SendEmojiMemeAction is not None
    assert getattr(inner_monologue.SendEmojiMemeAction, "action_name", "") == "send_emoji_meme"
