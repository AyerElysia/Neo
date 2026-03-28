"""Render event stream visualization report.

Usage:
    uv run python scripts/render_event_stream.py
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class EventSnapshot:
    pending: list[Path]
    processed: list[Path]
    archive: list[Path]
    latest_pending: list[dict[str, Any]]


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _list_json_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted([p for p in directory.glob("*.json") if p.is_file()], key=lambda p: p.name)


def _collect_snapshot(events_root: Path, latest_limit: int) -> EventSnapshot:
    pending_dir = events_root / "pending"
    processed_dir = events_root / "processed"
    archive_dir = events_root / "archive"

    pending = _list_json_files(pending_dir)
    processed = _list_json_files(processed_dir)
    archive = _list_json_files(archive_dir)

    latest_pending_data: list[dict[str, Any]] = []
    for path in reversed(pending[-latest_limit:]):
        data = _load_json(path) or {}
        latest_pending_data.append(
            {
                "id": data.get("id", path.stem),
                "type": data.get("type", "unknown"),
                "source": data.get("source", "unknown"),
                "priority": data.get("priority", "unknown"),
                "timestamp": data.get("timestamp", "unknown"),
                "file": path.name,
            }
        )

    return EventSnapshot(
        pending=pending,
        processed=processed,
        archive=archive,
        latest_pending=latest_pending_data,
    )


def _build_report(events_root: Path, snapshot: EventSnapshot) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(snapshot.pending) + len(snapshot.processed) + len(snapshot.archive)

    latest_section = ["| id | type | source | priority | timestamp | file |", "|---|---|---|---|---|---|"]
    if snapshot.latest_pending:
        for event in snapshot.latest_pending:
            latest_section.append(
                "| {id} | {type} | {source} | {priority} | {timestamp} | {file} |".format(
                    id=str(event["id"]).replace("|", "\\|"),
                    type=str(event["type"]).replace("|", "\\|"),
                    source=str(event["source"]).replace("|", "\\|"),
                    priority=str(event["priority"]).replace("|", "\\|"),
                    timestamp=str(event["timestamp"]).replace("|", "\\|"),
                    file=str(event["file"]).replace("|", "\\|"),
                )
            )
    else:
        latest_section.append("| (none) | - | - | - | - | - |")

    return f"""# 事件流可视化（Event Stream Visualization）

生成时间：`{generated_at}`  
事件目录：`{events_root}`

## 1. 当前状态总览

- 总事件数：`{total}`
- `pending`：`{len(snapshot.pending)}`
- `processed`：`{len(snapshot.processed)}`
- `archive`：`{len(snapshot.archive)}`

```mermaid
pie title Event Status Distribution
    "pending" : {len(snapshot.pending)}
    "processed" : {len(snapshot.processed)}
    "archive" : {len(snapshot.archive)}
```

## 2. 事件流主链路（系统视角）

```mermaid
flowchart LR
    IN[外部输入\\nQQ消息/提醒/触发] --> ENQ[写入 events/pending]
    ENQ --> HB{{Heartbeat/Decision Hub}}
    HB -->|处理成功| PROC[移动到 events/processed]
    HB -->|忽略/过期| ARC[移动到 events/archive]
    HB -->|需要回复| SEND[MessageSender -> Adapter -> QQ]
```

## 3. 状态机（单事件视角）

```mermaid
stateDiagram-v2
    [*] --> Pending
    Pending --> Processed: mark_event(processed)
    Pending --> Archived: mark_event(archived)
    Processed --> Archived: retention/cleanup
```

## 4. 消息触发到回复（时序图）

```mermaid
sequenceDiagram
    participant QQ as QQ User
    participant AD as napcat_adapter
    participant RX as MessageReceiver
    participant HUB as Decision Hub
    participant EVT as events/pending
    participant TX as MessageSender

    QQ->>AD: 发送消息
    AD->>RX: incoming envelope
    RX->>EVT: 生成事件（pending）
    RX->>HUB: ON_MESSAGE_RECEIVED
    HUB->>EVT: 读取/标记事件
    HUB->>TX: send_text/action
    TX->>AD: outbound envelope
    AD-->>QQ: 收到回复
```

## 5. 最新 Pending 事件

{chr(10).join(latest_section)}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Render event stream visualization markdown report.")
    parser.add_argument(
        "--events-root",
        default="data/anysoul_workspace/events",
        help="Events root directory (default: data/anysoul_workspace/events)",
    )
    parser.add_argument(
        "--output",
        default="report/事件流可视化.md",
        help="Output markdown path (default: report/事件流可视化.md)",
    )
    parser.add_argument(
        "--latest-limit",
        type=int,
        default=20,
        help="How many latest pending events to include (default: 20)",
    )
    args = parser.parse_args()

    events_root = Path(args.events_root).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    snapshot = _collect_snapshot(events_root, max(1, args.latest_limit))
    report = _build_report(events_root, snapshot)
    output_path.write_text(report, encoding="utf-8")

    print(f"Rendered: {output_path}")
    print(
        "Counts -> pending={p}, processed={r}, archive={a}".format(
            p=len(snapshot.pending),
            r=len(snapshot.processed),
            a=len(snapshot.archive),
        )
    )


if __name__ == "__main__":
    main()

