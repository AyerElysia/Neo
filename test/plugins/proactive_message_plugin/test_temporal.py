"""proactive_message_plugin 时间辅助测试。"""

from __future__ import annotations

from proactive_message_plugin.temporal import (
    build_time_prompt_block,
    classify_time_phase,
)


def test_classify_time_phase_uses_waiting_pressure() -> None:
    """等待期应优先返回冷却阶段。"""
    assert classify_time_phase(12.0, cooldown_remaining_minutes=3.0) == "冷却期"
    assert classify_time_phase(2.0) == "余温期"
    assert classify_time_phase(12.0) == "悬停期"
    assert classify_time_phase(35.0) == "牵挂期"
    assert classify_time_phase(75.0) == "收回期"


def test_build_time_prompt_block_contains_key_fields() -> None:
    """时间块应包含可直接注入 prompt 的关键字段。"""
    block = build_time_prompt_block(
        current_time_text="2026年3月22日 (周日) 午时 (中午，12点30分，3刻)，马年",
        phase="牵挂期",
        elapsed_user_minutes=42.0,
        waiting_minutes=8.0,
        cooldown_remaining_minutes=8.0,
        last_proactive_minutes=15.0,
        initiative_fatigue=21.0,
        prompt_title="主动时间感知",
    )

    assert "主动时间感知" in block
    assert "当前阶段：牵挂期" in block
    assert "距离上次用户消息：42分钟" in block
    assert "等待感：8分钟" in block
    assert "主观等待压强" in block
