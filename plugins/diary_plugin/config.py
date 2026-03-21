"""日记插件配置类。

定义日记插件的可配置项，包括存储路径、格式选项等。
"""

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


@config_section("plugin")
class PluginSection(SectionBase):
    """插件主配置。"""

    enabled: bool = Field(default=True, description="是否启用日记插件")
    inject_system_prompt: bool = Field(
        default=True,
        description="是否向 actor bucket 注入系统提示语",
    )


@config_section("storage")
class StorageSection(SectionBase):
    """存储配置。"""

    base_path: str = Field(
        default="data/diaries",
        description="日记存储根目录",
    )
    date_format: str = Field(
        default="%Y-%m",
        description="日期目录格式（用于月份目录）",
    )
    file_format: str = Field(
        default="%Y-%m-%d.md",
        description="日记文件名格式",
    )


@config_section("format")
class FormatSection(SectionBase):
    """日记格式配置。"""

    enable_header: bool = Field(
        default=True,
        description="是否在日记开头添加基本信息头（日期、星期、天气等）",
    )
    enable_section: bool = Field(
        default=True,
        description="是否启用时间段分类（上午/下午/晚上）",
    )
    time_format: str = Field(
        default="%H:%M",
        description="时间戳格式",
    )
    default_section: str = Field(
        default="其他",
        description="默认时间段分类",
    )


@config_section("dedup")
class DedupSection(SectionBase):
    """去重配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用写前重复检查",
    )
    similarity_threshold: float = Field(
        default=0.8,
        description="相似度阈值（超过此值视为重复）",
    )
    min_content_length: int = Field(
        default=5,
        description="最小内容长度（短于此长度不进行去重检查）",
    )


@config_section("reminder")
class ReminderSection(SectionBase):
    """System Reminder 配置。"""

    bucket: str = Field(
        default="actor",
        description="System Reminder 注入的 bucket",
    )
    name: str = Field(
        default="关于写日记",
        description="System Reminder 名称",
    )
    custom_instructions: str = Field(
        default="",
        description="自定义引导语（会追加到默认引导语后面）",
    )


@config_section("auto_diary")
class AutoDiarySection(SectionBase):
    """自动写日记配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用自动写日记功能",
    )
    message_threshold: int = Field(
        default=20,
        description="触发自动写日记的消息数量阈值（同时作为总结的消息条数）",
    )
    allow_group_chat: bool = Field(
        default=False,
        description="是否允许群聊自动写日记（False=仅私聊触发）",
    )


@config_section("model")
class ModelSection(SectionBase):
    """模型配置。"""

    task_name: str = Field(
        default="diary",
        description="写日记使用的任务模型名称（对应 model.toml 中的 [model_tasks.xxx]）",
    )


class DiaryConfig(BaseConfig):
    """日记插件配置。

    配置项说明：
    - plugin: 插件主配置（启用状态、提示注入）
    - storage: 存储配置（路径、格式）
    - format: 日记格式配置（头部、时间段、时间戳）
    - dedup: 去重配置（启用、阈值）
    - reminder: System Reminder 配置
    - auto_diary: 自动写日记配置（启用、阈值、提醒消息）
    - model: 模型配置（任务模型名称）

    默认配置路径：config/plugins/diary_plugin/config.toml
    """

    config_name = "config"
    config_description = "日记插件配置"

    plugin: PluginSection = Field(default_factory=PluginSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    format: FormatSection = Field(default_factory=FormatSection)
    dedup: DedupSection = Field(default_factory=DedupSection)
    reminder: ReminderSection = Field(default_factory=ReminderSection)
    auto_diary: AutoDiarySection = Field(default_factory=AutoDiarySection)
    model: ModelSection = Field(default_factory=ModelSection)
