"""src.kernel.config

内核配置模块。

该模块提供基于 Pydantic 的类型安全配置文件系统，支持自动类型校验与 TOML 存储。

本模块采用“静态可见”的配置模型设计：配置类本身继承 :class:`pydantic.BaseModel`，
并在类体中显式声明各配置节字段（这样 IDE/Pylance 能正确推断类型）。

典型使用示例：
    ```python
    from src.kernel.config import ConfigBase, SectionBase, config_section, Field

    class MyConfig(ConfigBase):
        @config_section("general")
        class GeneralSection(SectionBase):
            enabled: bool = Field(default=True, description="启用功能")

        general: GeneralSection = Field(default_factory=GeneralSection)

    my_config = MyConfig.load("config/my_config.toml")
    print(my_config.general.enabled)
    ```
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, TypeVar, Self

from pydantic import BaseModel, ConfigDict, Field
import tomllib


SectionT = TypeVar("SectionT", bound="SectionBase")

__all__ = [
    "ConfigBase",
    "SectionBase",
    "config_section",
    "Field",
]


def config_section(name: str) -> Callable[[type[SectionT]], type[SectionT]]:
    """配置节装饰器。

    重要：该装饰器使用泛型返回类型，确保 IDE/Pylance 能保留被装饰类的具体类型，
    避免把 `SectionB` 降级成 `SectionBase`，从而导致字段（如 `value_b`）无法被识别。
    """

    def decorator(cls: type[SectionT]) -> type[SectionT]:
        cls.__config_section_name__ = name  # type: ignore[attr-defined]
        return cls

    return decorator


class SectionBase(BaseModel):
    """
    配置节基类。

    配置节是一组相关的配置选项。它们会被 ConfigBase 自动收集并映射到 TOML 节。

    Attributes:
        model_config: Pydantic 配置（默认禁止额外字段）

    示例：
        ```python
        @config_section("general")
        class GeneralSection(SectionBase):
            '''常规配置选项。'''
            enabled: bool = Field(default=True, description="启用功能")
            name: str = Field(default="default", description="功能名称")
        ```
    """

    model_config = ConfigDict(extra="forbid")


class ConfigBase(BaseModel):
    """
    配置基类（静态可见）。

    配置类本身是一个 Pydantic 模型，所有配置节都应作为字段显式声明。
    这能让 IDE/Pylance 在访问 `config.xxx.yyy` 时正确进行类型推断。

    示例：
        ```python
        class MyConfig(ConfigBase):
            @config_section("inner")
            class InnerSection(SectionBase):
                version: str = Field(...)
                enabled: bool = Field(...)

            @config_section("general")
            class GeneralSection(SectionBase):
                option1: str = Field(...)
                option2: int = Field(...)

            inner: InnerSection = Field(default_factory=InnerSection)
            general: GeneralSection = Field(default_factory=GeneralSection)

        # 从文件加载
        config = MyConfig.load("config.toml")
        print(config.general.option1)

        # 导出默认配置
        defaults = MyConfig.default()
        ```
    """

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """
        从字典加载配置。

        该方法根据定义的节验证数据，并返回验证后的配置实例。

        Args:
            data: 包含配置数据的字典（通常来自 tomllib.load()）

        Returns:
            验证后的配置实例（类型为当前配置类）

        Raises:
            ValidationError: 如果数据与节模式不匹配
        """
        return cls.model_validate(data)

    @classmethod
    def load(cls, path: str | Path) -> Self:
        """
        从 TOML 文件加载配置。

        该方法读取 TOML 文件，解析它，并根据定义的配置节验证内容。

        Args:
            path: TOML 配置文件的路径

        Returns:
            验证后的配置实例（类型为当前配置类）

        Raises:
            FileNotFoundError: 如果配置文件不存在
            ValidationError: 如果 TOML 数据与节模式不匹配
            tomllib.TOMLDecodeError: 如果 TOML 文件格式错误

        示例：
            ```python
            config = MyConfig.load("config/settings.toml")
            print(config.general.enabled)
            ```
        """
        path = Path(path)
        with path.open("rb") as f:
            raw = tomllib.load(f)

        return cls.from_dict(raw)

    @classmethod
    def default(cls) -> dict[str, Any]:
        """
        生成默认配置字典。

        该方法创建一个包含配置节所有默认值的字典。
        用于生成初始配置文件。

        Returns:
            以节名称为键、节数据为值的字典

        示例：
            ```python
            defaults = MyConfig.default()
            import tomli_w
            with open("config/default.toml", "wb") as f:
                tomli_w.dump(defaults, f)
            ```
        """
        return cls().model_dump()
