"""提示词快照记录工具。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import uuid4


class PromptSnapshotWriter(Protocol):
    async def write_json(self, rel_path: str, data: dict[str, Any]) -> str:
        """写入 JSON 快照。"""
        ...


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _normalize_section(section: dict[str, Any]) -> dict[str, Any]:
    title = _coerce_text(section.get("title") or section.get("label") or "未命名区块").strip()
    role = _coerce_text(section.get("role", "")).strip()
    content = _coerce_text(section.get("content", ""))
    return {
        "title": title or "未命名区块",
        "role": role,
        "content": content,
    }


def render_prompt_snapshot(snapshot: dict[str, Any]) -> str:
    """把提示词快照渲染成适合人类阅读的纯文本。"""
    lines: list[str] = []
    title = _coerce_text(snapshot.get("title", "")).strip() or "提示词快照"
    lines.append(f"# {title}")

    meta = snapshot.get("metadata", {})
    if isinstance(meta, dict) and meta:
        lines.append("")
        lines.append("## 元信息")
        for key in sorted(meta.keys()):
            value = _coerce_text(meta.get(key, "")).strip()
            if not value:
                continue
            lines.append(f"- {key}: {value}")

    sections = snapshot.get("sections", [])
    if isinstance(sections, list) and sections:
        for section in sections:
            if not isinstance(section, dict):
                continue
            sec = _normalize_section(section)
            lines.append("")
            header = sec["title"]
            if sec["role"]:
                header = f"{header} [{sec['role']}]"
            lines.append(f"## {header}")
            lines.append(sec["content"].rstrip() or "（空）")

    return "\n".join(lines).rstrip() + "\n"


async def write_prompt_snapshot(
    writer: PromptSnapshotWriter,
    *,
    scope: str,
    title: str,
    sections: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
    store_history: bool = True,
) -> dict[str, Any]:
    """写入提示词快照，并更新 current 视图。"""
    snapshot_id = f"prompt_{scope}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    normalized_sections = [_normalize_section(section) for section in sections]
    snapshot: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "scope": scope,
        "title": title,
        "generated_at": datetime.now().isoformat(),
        "metadata": metadata or {},
        "sections": normalized_sections,
    }
    snapshot["rendered_prompt"] = render_prompt_snapshot(snapshot)

    await writer.write_json(f"prompts/current/{scope}.json", snapshot)
    if store_history:
        await writer.write_json(f"prompts/history/{scope}/{snapshot_id}.json", snapshot)

    return snapshot
