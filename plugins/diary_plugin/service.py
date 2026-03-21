"""日记管理服务实现。

提供日记的创建、读取、写入、去重检查等功能。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from src.app.plugin_system.base import BaseService
from src.kernel.logger import get_logger

from .config import DiaryConfig


logger = get_logger("diary_plugin")


@dataclass
class DiaryEvent:
    """单条日记事件。"""

    timestamp: str
    content: str
    section: str


@dataclass
class DiaryContent:
    """日记内容结构。"""

    raw_text: str
    date: str
    events: list[DiaryEvent] = field(default_factory=list)
    sections: dict[str, list[DiaryEvent]] = field(default_factory=dict)
    exists: bool = True


class DiaryService(BaseService):
    """日记管理服务。

    对外提供：
    - read_today: 读取今天日记
    - read_date: 读取指定日期日记
    - append_entry: 追加日记条目（带重复检查）
    - can_modify: 检查是否可修改指定日期
    """

    service_name: str = "diary_service"
    service_description: str = """
    日记管理服务，提供日记的创建、读取、写入能力。

    核心功能：
    - 读取指定日期的日记
    - 追加新条目到今天的日记
    - 自动去重检查
    - 日期隔离（只能修改今天）
    """
    version: str = "1.0.0"

    def _cfg(self) -> DiaryConfig:
        """获取插件配置实例。"""
        cfg = self.plugin.config
        if not isinstance(cfg, DiaryConfig):
            raise RuntimeError("diary_plugin config 未正确加载")
        return cfg

    def _get_base_path(self) -> Path:
        """获取日记存储根目录。"""
        return Path(self._cfg().storage.base_path)

    def _get_date_file_path(self, date: str) -> Path:
        """获取指定日期日记文件路径。

        Args:
            date: 日期字符串，格式 YYYY-MM-DD

        Returns:
            日记文件路径
        """
        try:
            date_obj = datetime.strptime(date, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError(f"日期格式错误：{date}，应为 YYYY-MM-DD") from e

        base_path = self._get_base_path()
        month_dir = base_path / date_obj.strftime(self._cfg().storage.date_format)
        day_file = month_dir / date_obj.strftime(self._cfg().storage.file_format)

        return day_file

    def _get_today_file_path(self) -> Path:
        """获取今天日记文件路径。"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self._get_date_file_path(today)

    def _is_today(self, date: str | None = None) -> bool:
        """检查指定日期是否为今天。

        Args:
            date: 日期字符串，为空时检查今天

        Returns:
            bool: 是否为今天
        """
        if date is None:
            return True

        try:
            date_obj = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return False

        return date_obj == datetime.now().date()

    def can_modify(self, date: str) -> tuple[bool, str]:
        """检查是否可以修改指定日期的日记。

        Args:
            date: 日期字符串 YYYY-MM-DD

        Returns:
            (can_modify, reason)
        """
        if not self._is_today(date):
            return False, "只能修改今天的日记，不能修改历史日记"

        return True, "可以修改"

    def read_today(self) -> DiaryContent:
        """读取今天日记全文。

        Returns:
            DiaryContent: 日记内容结构
        """
        today = datetime.now().strftime("%Y-%m-%d")
        return self.read_date(today)

    def read_date(self, date: str) -> DiaryContent:
        """读取指定日期日记。

        Args:
            date: 日期字符串 YYYY-MM-DD

        Returns:
            DiaryContent: 日记内容结构
        """
        path = self._get_date_file_path(date)

        if not path.exists():
            return DiaryContent(
                raw_text="",
                date=date,
                events=[],
                sections={
                    "上午": [],
                    "下午": [],
                    "晚上": [],
                    "其他": [],
                },
                exists=False,
            )

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.error(f"读取日记文件失败：{path} - {e}")
            return DiaryContent(
                raw_text="",
                date=date,
                events=[],
                sections={},
                exists=False,
            )

        # 解析事件
        events = self._parse_events(content)
        sections = self._parse_sections(events)

        return DiaryContent(
            raw_text=content,
            date=date,
            events=events,
            sections=sections,
            exists=True,
        )

    def _parse_events(self, content: str) -> list[DiaryEvent]:
        """解析日记内容为事件列表。

        识别格式：**[HH:MM]** 内容

        Args:
            content: 日记原文

        Returns:
            list[DiaryEvent]: 事件列表
        """
        events: list[DiaryEvent] = []

        # 匹配时间戳格式：**[HH:MM]** 内容
        pattern = r"\*\*\[(\d{2}:\d{2})\]\*\*\s*(.+?)(?=\n\*\*\[|\Z)"

        for match in re.finditer(pattern, content, re.DOTALL):
            timestamp = match.group(1)
            text = match.group(2).strip()

            if text:
                # 判断时间段
                section = self._get_section_by_time(timestamp)
                events.append(
                    DiaryEvent(
                        timestamp=timestamp,
                        content=text,
                        section=section,
                    )
                )

        return events

    def _get_section_by_time(self, time_str: str) -> str:
        """根据时间字符串判断时间段。

        Args:
            time_str: 时间字符串 HH:MM

        Returns:
            str: 时间段（上午/下午/晚上/其他）
        """
        try:
            hour = int(time_str.split(":")[0])
        except (ValueError, IndexError):
            return "其他"

        if 5 <= hour < 12:
            return "上午"
        elif 12 <= hour < 18:
            return "下午"
        elif 18 <= hour < 23:
            return "晚上"
        else:
            return "其他"

    def _parse_sections(self, events: list[DiaryEvent]) -> dict[str, list[DiaryEvent]]:
        """将事件按时间段分类。

        Args:
            events: 事件列表

        Returns:
            dict[str, list[DiaryEvent]]: 时间段分类字典
        """
        sections: dict[str, list[DiaryEvent]] = {
            "上午": [],
            "下午": [],
            "晚上": [],
            "其他": [],
        }

        for event in events:
            if event.section in sections:
                sections[event.section].append(event)

        return sections

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """计算两个文本的 Jaccard 相似度。

        Args:
            text1: 文本 1
            text2: 文本 2

        Returns:
            float: 相似度 (0.0-1.0)
        """
        # 简单分词（按字符）
        set1 = set(text1.lower())
        set2 = set(text2.lower())

        if not set1 or not set2:
            return 0.0

        intersection = len(set1 & set2)
        union = len(set1 | set2)

        return intersection / union if union > 0 else 0.0

    def _is_duplicate(
        self,
        new_content: str,
        existing_events: list[DiaryEvent],
        threshold: float | None = None,
    ) -> tuple[bool, str | None]:
        """检查新内容是否与已有事件重复。

        Args:
            new_content: 新日记内容
            existing_events: 已有事件列表
            threshold: 相似度阈值，默认使用配置值

        Returns:
            (is_duplicate, similar_event_content)
        """
        dedup_cfg = self._cfg().dedup

        if not dedup_cfg.enabled:
            return False, None

        if threshold is None:
            threshold = dedup_cfg.similarity_threshold

        # 内容太短不进行去重检查
        if len(new_content.strip()) < dedup_cfg.min_content_length:
            return False, None

        new_content_lower = new_content.lower().strip()

        for event in existing_events:
            event_content = event.content.lower().strip()

            # 精确匹配检查
            if new_content_lower in event_content or event_content in new_content_lower:
                return True, event.content

            # 相似度检查
            similarity = self._calculate_similarity(new_content_lower, event_content)
            if similarity > threshold:
                return True, event.content

        return False, None

    def append_entry(
        self,
        content: str,
        section: str = "其他",
        date: str | None = None,
    ) -> tuple[bool, str]:
        """追加日记条目。

        Args:
            content: 日记内容
            section: 时间段（上午/下午/晚上/其他）
            date: 日期 YYYY-MM-DD，为空时默认今天

        Returns:
            (success, message)
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        # 日期隔离检查
        can_modify, reason = self.can_modify(date)
        if not can_modify:
            return False, reason

        # 读取今天日记检查重复
        today_content = self.read_today()
        existing_events = today_content.events

        # 去重检查
        is_dup, similar_content = self._is_duplicate(content, existing_events)
        if is_dup:
            return False, f"今天已经记录过类似内容了：{similar_content}"

        # 获取路径并确保目录存在
        path = self._get_today_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        # 生成时间戳
        timestamp = datetime.now().strftime(self._cfg().format.time_format)

        # 生成日记条目
        entry = f"\n**[{timestamp}]** {content}\n"

        # 写入文件
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(entry)

            logger.info(f"日记已更新 [{section}] {timestamp}")
            return True, f"日记已更新 [{section}]"

        except Exception as e:
            logger.error(f"写入日记失败：{e}")
            return False, f"写入失败：{e}"

    def get_today_summary(self) -> dict[str, Any]:
        """获取今天日记摘要（用于 Tool 返回）。

        Returns:
            dict: 包含日期、事件列表、各时间段摘要
        """
        today_content = self.read_today()

        events_data = [
            {
                "timestamp": event.timestamp,
                "content": event.content,
                "section": event.section,
            }
            for event in today_content.events
        ]

        sections_summary = {
            section: [event.content for event in events]
            for section, events in today_content.sections.items()
        }

        return {
            "date": today_content.date,
            "exists": today_content.exists,
            "event_count": len(today_content.events),
            "events": events_data,
            "sections": sections_summary,
            "raw_text": today_content.raw_text,
        }
