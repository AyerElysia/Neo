"""default_chatter.decision_agent 模块测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugins.default_chatter.decision_agent import _fit_unreads_to_sub_agent_budget


@pytest.mark.asyncio
async def test_fit_unreads_keeps_text_when_within_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """当输入未超过预算时应保持原样。"""

    monkeypatch.setattr(
        "plugins.default_chatter.decision_agent._safe_count_tokens",
        lambda text, _model_identifier: len(text),
    )

    request = SimpleNamespace(
        model_set=[{"model_identifier": "demo-model", "max_context": 32768}]
    )
    text = "line-1\nline-2"

    fitted = _fit_unreads_to_sub_agent_budget(request, text)

    assert fitted == text


@pytest.mark.asyncio
async def test_fit_unreads_trims_old_prefix_when_over_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超过预算时应裁剪前缀，优先保留最新未读内容。"""

    monkeypatch.setattr(
        "plugins.default_chatter.decision_agent._safe_count_tokens",
        lambda text, _model_identifier: len(text),
    )

    request = SimpleNamespace(
        model_set=[{"model_identifier": "demo-model", "max_context": 4096}]
    )
    long_text = "old-message\n" + ("x" * 1500) + "\nlatest-message"

    fitted = _fit_unreads_to_sub_agent_budget(request, long_text)

    assert "latest-message" in fitted
    assert len(fitted) <= 1024
