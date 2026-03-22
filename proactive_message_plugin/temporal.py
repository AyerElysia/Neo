"""主动消息插件的时间感知辅助函数。"""

from __future__ import annotations

from datetime import datetime
from math import floor


def build_chinese_datetime(dt: datetime | None = None) -> str:
    """生成中式时间描述。"""

    dt = dt or datetime.now()

    shichen_map = {
        (23, 1): ("子时", "深夜"),
        (1, 3): ("丑时", "凌晨"),
        (3, 5): ("寅时", "黎明"),
        (5, 7): ("卯时", "清晨"),
        (7, 9): ("辰时", "上午"),
        (9, 11): ("巳时", "上午"),
        (11, 13): ("午时", "中午"),
        (13, 15): ("未时", "下午"),
        (15, 17): ("申时", "下午"),
        (17, 19): ("酉时", "傍晚"),
        (19, 21): ("戌时", "晚上"),
        (21, 23): ("亥时", "深夜"),
    }

    shichen_name = "未知"
    shichen_period = "未知"
    for (start, end), (name, period) in shichen_map.items():
        if start == 23:
            if dt.hour >= 23 or dt.hour < 1:
                shichen_name = name
                shichen_period = period
                break
            continue
        if start <= dt.hour < end:
            shichen_name = name
            shichen_period = period
            break

    ke = (dt.minute // 15) + 1

    zodiac_map = {
        0: "猴",
        1: "鸡",
        2: "狗",
        3: "猪",
        4: "鼠",
        5: "牛",
        6: "虎",
        7: "兔",
        8: "龙",
        9: "蛇",
        10: "马",
        11: "羊",
    }
    zodiac = zodiac_map[dt.year % 12]

    weekday_map = {
        0: "周一",
        1: "周二",
        2: "周三",
        3: "周四",
        4: "周五",
        5: "周六",
        6: "周日",
    }
    weekday = weekday_map[dt.weekday()]

    return (
        f"{dt.year}年{dt.month}月{dt.day}日 ({weekday}) "
        f"{shichen_name} ({shichen_period}，{dt.hour}点{dt.minute}分，{ke}刻)，"
        f"{zodiac}年"
    )


def format_elapsed_minutes(minutes: float) -> str:
    """把分钟数转成更适合 prompt 的主观时间描述。"""

    value = max(0.0, float(minutes))
    if value < 0.5:
        return "刚刚"
    if value < 5:
        return f"约{floor(value)}分钟"
    if value < 60:
        return f"{floor(value)}分钟"
    hours = value / 60.0
    if hours < 3:
        return f"约{hours:.1f}小时"
    return f"约{hours:.1f}小时"


def classify_time_phase(
    elapsed_minutes: float,
    *,
    cooldown_remaining_minutes: float = 0.0,
) -> str:
    """根据沉默时长判断主观时间阶段。"""

    if cooldown_remaining_minutes > 0:
        return "冷却期"

    minutes = max(0.0, float(elapsed_minutes))
    if minutes < 5:
        return "余温期"
    if minutes < 20:
        return "悬停期"
    if minutes < 60:
        return "牵挂期"
    return "收回期"


def describe_time_phase(phase: str) -> str:
    """把时间阶段转成更主观的描述。"""

    mapping = {
        "余温期": "刚聊完不久，话题余温还在",
        "悬停期": "已经出现一点空档感，注意力开始悬着",
        "牵挂期": "沉默开始变得有重量，会更在意对方是否还在忙",
        "收回期": "主动冲动在收拢，开始考虑要不要先安静下来",
        "冷却期": "刚主动过，时间上还在缓冲，不急着再开口",
    }
    return mapping.get(phase, "时间仍在流动，感觉正在缓慢变化")


def compute_subjective_pressure(
    *,
    elapsed_minutes: float,
    waiting_minutes: float,
    cooldown_remaining_minutes: float = 0.0,
    initiative_fatigue: float = 0.0,
) -> int:
    """计算主观等待压强。"""

    pressure = (
        max(0.0, elapsed_minutes) * 1.4
        + max(0.0, waiting_minutes) * 1.0
        + max(0.0, cooldown_remaining_minutes) * 0.8
        + max(0.0, initiative_fatigue) * 0.35
    )
    return max(0, min(100, int(round(pressure))))


def describe_pressure(pressure: int) -> str:
    """把压强值转成主观描述。"""

    if pressure < 15:
        return "几乎没有压迫感"
    if pressure < 35:
        return "轻微牵动"
    if pressure < 60:
        return "开始有点重量"
    if pressure < 80:
        return "已经比较明显"
    return "很强，像是时间正在催促内心变化"


def describe_afterglow(elapsed_minutes: float) -> str:
    """描述对话余温。"""

    minutes = max(0.0, float(elapsed_minutes))
    if minutes < 5:
        return "还很热"
    if minutes < 20:
        return "还在散发余热"
    if minutes < 60:
        return "开始变淡，但还没完全散"
    return "已经比较淡了，但仍有残响"


def build_time_prompt_block(
    *,
    current_time_text: str,
    phase: str,
    elapsed_user_minutes: float,
    waiting_minutes: float,
    cooldown_remaining_minutes: float = 0.0,
    last_proactive_minutes: float | None = None,
    initiative_fatigue: float = 0.0,
    prompt_title: str = "时间感知",
) -> str:
    """构建注入 prompt 的时间感知区块。"""

    pressure = compute_subjective_pressure(
        elapsed_minutes=elapsed_user_minutes,
        waiting_minutes=waiting_minutes,
        cooldown_remaining_minutes=cooldown_remaining_minutes,
        initiative_fatigue=initiative_fatigue,
    )

    lines = [
        f"【{prompt_title}】",
        f"- 现在时间：{current_time_text}",
        f"- 当前阶段：{phase}",
        f"- 主观描述：{describe_time_phase(phase)}",
        f"- 距离上次用户消息：{format_elapsed_minutes(elapsed_user_minutes)}",
        f"- 等待感：{format_elapsed_minutes(waiting_minutes)}",
        f"- 时间余温：{describe_afterglow(elapsed_user_minutes)}",
        f"- 主观等待压强：{pressure}/100（{describe_pressure(pressure)}）",
    ]
    if last_proactive_minutes is None:
        lines.append("- 距离上次主动：暂无记录")
    else:
        lines.append(f"- 距离上次主动：{format_elapsed_minutes(last_proactive_minutes)}")
    if cooldown_remaining_minutes > 0:
        lines.append(
            f"- 冷却剩余：{format_elapsed_minutes(cooldown_remaining_minutes)}"
        )
    if initiative_fatigue > 0:
        lines.append(f"- 主动疲劳：{initiative_fatigue:.0f}/100")
    return "\n".join(lines)
