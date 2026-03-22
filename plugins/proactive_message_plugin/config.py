"""Proactive Message Plugin 配置。

配置主动发消息功能的参数。
"""

from typing import ClassVar

from src.core.components.base.config import BaseConfig, SectionBase, config_section, Field


class ProactiveMessageConfig(BaseConfig):
    """Proactive Message Plugin 配置类。

    Attributes:
        config_name: 配置文件名称
        config_description: 配置描述
    """

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "主动发消息插件配置"

    @config_section("settings")
    class SettingsSection(SectionBase):
        """基础设置配置项。"""

        enabled: bool = Field(
            default=True,
            description="是否启用主动发消息功能。设为 false 可临时禁用，无需从 core.toml 移除插件。",
        )

        # 首次触发内心独白的等待时间（分钟）
        first_check_minutes: float = Field(
            default=10.0,
            description="首次触发内心独白的等待时间（分钟）",
        )

        # 默认最小等待间隔（分钟），防止 LLM 说"等 1 分钟"太频繁
        min_wait_interval_minutes: float = Field(
            default=5.0,
            description="默认最小等待间隔（分钟），防止 LLM 说'等 1 分钟'太频繁",
        )

        # 最大等待时间（分钟），超过后强制触发
        max_wait_minutes: float = Field(
            default=180.0,
            description="最大等待时间（分钟），超过后强制触发",
        )

        post_send_followup_minutes: float = Field(
            default=10.0,
            description="主动发送后若无人回复，再次触发内心独白前的等待时间（分钟）",
        )

        monologue_history_limit: int = Field(
            default=5,
            description="内心独白提示中携带的历史独白条数",
        )

        inject_prompt: bool = Field(
            default=True,
            description="是否在 on_prompt_build 阶段注入动态时间感知块。",
        )

        target_prompt_names: list[str] = Field(
            default=["default_chatter_user_prompt"],
            description="需要注入动态时间感知块的 prompt 名称列表。",
        )

        time_prompt_title: str = Field(
            default="时间感知",
            description="动态时间感知块的标题。",
        )

        # 忽略的聊天类型
        ignored_chat_types: list[str] = Field(
            default=["group"],
            description="忽略的聊天类型列表，如 ['group'] 表示群聊不触发",
        )

    settings: SettingsSection = Field(default_factory=SettingsSection)
