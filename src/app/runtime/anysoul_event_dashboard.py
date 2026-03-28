"""AnySoul 中枢事件流 Web 看板（SSE 实时）。

提供一个轻量级网页用于观察 `data/anysoul_workspace` 下的中枢事件流：
- events/timeline/*.json
- events/pending/*.json
- events/processed/*.json
- tasks/active/*.json
- tasks/completed/*.json

挂载方式（由 runtime/bot.py 调用）：
    get_anysoul_event_dashboard().mount(fastapi_app)

页面地址：
    http://<host>:<port>/_anysoul_hub/
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return "{}"


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _sort_key(path: Path) -> tuple[float, str]:
    try:
        return (path.stat().st_mtime, path.name)
    except Exception:
        return (0.0, path.name)


class AnySoulEventDashboard:
    """中枢事件流仪表盘。"""

    def __init__(self, workspace_base: str | None = None) -> None:
        env_base = os.getenv("ANYSOUL_WORKSPACE_BASE", "").strip()
        base = workspace_base or env_base or "data/anysoul_workspace"
        self._workspace = Path(base).resolve()
        self._events_timeline = self._workspace / "events" / "timeline"
        self._events_pending = self._workspace / "events" / "pending"
        self._events_processed = self._workspace / "events" / "processed"
        self._tasks_active = self._workspace / "tasks" / "active"
        self._tasks_completed = self._workspace / "tasks" / "completed"
        self._state_path = self._workspace / "state" / "decision_hub.json"
        self._mounted = False

    def mount(self, app: Any, prefix: str = "/_anysoul_hub") -> None:
        """挂载路由到 FastAPI 主应用。"""
        if self._mounted:
            return
        self._mounted = True
        app.include_router(self._build_router(), prefix=prefix)

    def _build_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def webui() -> HTMLResponse:  # type: ignore[return-value]
            return HTMLResponse(_WEBUI_HTML)

        @router.get("/api/snapshot")
        async def snapshot(limit: int = 120) -> JSONResponse:
            return JSONResponse(self._build_snapshot(limit=max(10, min(limit, 500))))

        @router.get("/api/hub_context")
        async def hub_context() -> JSONResponse:
            return JSONResponse(self._build_hub_context())

        @router.get("/api/event/{event_id}")
        async def get_event(event_id: str) -> JSONResponse:
            path = self._events_timeline / f"{event_id}.json"
            if not path.exists():
                return JSONResponse({"error": "not found"}, status_code=404)
            return JSONResponse(_safe_read_json(path))

        @router.get("/api/stream", include_in_schema=False)
        async def stream(limit: int = 120) -> StreamingResponse:
            async def generate() -> AsyncIterator[str]:
                capped_limit = max(10, min(limit, 500))
                fingerprint = ""
                snapshot_data = self._build_snapshot(limit=capped_limit)
                fingerprint = self._fingerprint(snapshot_data)
                yield f"event: snapshot\ndata: {_json_dumps(snapshot_data)}\n\n"

                while True:
                    await asyncio.sleep(1.0)
                    current = self._build_snapshot(limit=capped_limit)
                    current_fp = self._fingerprint(current)
                    if current_fp != fingerprint:
                        fingerprint = current_fp
                        yield f"event: update\ndata: {_json_dumps(current)}\n\n"
                    else:
                        yield ": heartbeat\n\n"

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        return router

    def _build_snapshot(self, limit: int = 120) -> dict[str, Any]:
        timeline_files = sorted(self._events_timeline.glob("*.json"), key=_sort_key)
        pending_files = sorted(self._events_pending.glob("*.json"), key=_sort_key)
        processed_files = sorted(self._events_processed.glob("*.json"), key=_sort_key)
        active_files = sorted(self._tasks_active.glob("*.json"), key=_sort_key)
        completed_files = sorted(self._tasks_completed.glob("*.json"), key=_sort_key)

        selected = timeline_files[-limit:]
        events: list[dict[str, Any]] = []
        for path in reversed(selected):
            raw = _safe_read_json(path)
            payload = raw.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}
            events.append(
                {
                    "event_id": str(raw.get("event_id", path.stem)),
                    "event_type": str(raw.get("event_type", "unknown")),
                    "timestamp": str(raw.get("timestamp", "")),
                    "summary": self._summary(raw, payload),
                    "source_event_id": str(payload.get("source_event_id", "")),
                    "task_id": str(payload.get("task_id", "")),
                    "task_mark": str(payload.get("task_mark", "")),
                    "stream_id": str(payload.get("stream_id", "")),
                    "payload": payload,
                }
            )

        latest_timeline = timeline_files[-1].name if timeline_files else ""
        return {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "workspace": str(self._workspace),
            "hub_context": self._build_hub_context(),
            "counts": {
                "timeline": len(timeline_files),
                "pending": len(pending_files),
                "processed": len(processed_files),
                "tasks_active": len(active_files),
                "tasks_completed": len(completed_files),
            },
            "latest": {"timeline_file": latest_timeline},
            "events": events,
        }

    def _build_hub_context(self) -> dict[str, Any]:
        state = _safe_read_json(self._state_path)
        recent = state.get("recent_events", [])
        if not isinstance(recent, list):
            recent = []

        # 与中枢决策保持一致：先取最近 12 条，再格式化时再取最后 8 条
        visible_recent = recent[-12:]
        lines: list[str] = []
        for item in visible_recent[-8:]:
            if not isinstance(item, dict):
                continue
            decision = str(item.get("decision", "") or "")
            preview = str(item.get("preview", "") or "")
            expression_summary = str(item.get("expression_summary", "") or "")
            send_preview = str(item.get("send_preview", "") or "")
            segment_count = int(item.get("segment_count", 0) or 0)
            thought_preview = str(item.get("thought_preview", "") or "")
            lines.append(
                f"- [{decision}] {_truncate(preview, 48)}"
                f"{' | ' + _truncate(expression_summary, 36) if expression_summary else ''}"
                f"{' | 发送:' + _truncate(send_preview, 30) if send_preview else ''}"
                f"{' | 分段=' + str(segment_count) if segment_count > 0 else ''}"
                f"{' | 思考:' + _truncate(thought_preview, 24) if thought_preview else ''}"
            )

        recent_block = "\n".join(lines) if lines else "- （暂无）"
        return {
            "event_window_size": int(state.get("event_window_size", 20) or 20),
            "recent_total": len(recent),
            "recent_visible_count": len(visible_recent),
            "recent_events": visible_recent,
            "recent_block_for_prompt": recent_block,
        }

    @staticmethod
    def _summary(raw: dict[str, Any], payload: dict[str, Any]) -> str:
        event_type = str(raw.get("event_type", "unknown"))
        if event_type == "decision_made":
            return (
                f"决策: should_chat={payload.get('should_chat')} "
                f"reason={payload.get('reason', '')}"
            )
        if event_type == "task_dispatched":
            return f"派发任务: {payload.get('task_id', '')}"
        if event_type == "task_started":
            return f"任务开始: {payload.get('task_id', '')}"
        if event_type == "task_completed":
            return (
                f"任务完成: {payload.get('task_id', '')} "
                f"sent={payload.get('sent')} seg={payload.get('segment_count', 0)}"
            )
        if event_type == "task_failed":
            return f"任务失败: {payload.get('task_id', '')} err={payload.get('error', '')}"
        if event_type == "chat_send_called":
            send_call = payload.get("send_call", {})
            if isinstance(send_call, dict):
                args = send_call.get("arguments", {})
                if isinstance(args, dict):
                    content = args.get("content", "")
                    if isinstance(content, list):
                        text = " / ".join(
                            seg.strip() for seg in content if isinstance(seg, str) and seg.strip()
                        )
                    else:
                        text = str(content)
                    return f"发送调用: {send_call.get('name', '')} -> {text[:90]}"
            return "发送调用事件"
        if event_type == "hub_think":
            return f"中枢思考: {str(payload.get('thought', ''))[:90]}"
        return f"{event_type}"

    @staticmethod
    def _fingerprint(snapshot_data: dict[str, Any]) -> str:
        counts = snapshot_data.get("counts", {})
        if not isinstance(counts, dict):
            counts = {}
        latest = snapshot_data.get("latest", {})
        if not isinstance(latest, dict):
            latest = {}
        hub_ctx = snapshot_data.get("hub_context", {})
        if not isinstance(hub_ctx, dict):
            hub_ctx = {}
        return "|".join(
            [
                str(counts.get("timeline", 0)),
                str(counts.get("pending", 0)),
                str(counts.get("processed", 0)),
                str(counts.get("tasks_active", 0)),
                str(counts.get("tasks_completed", 0)),
                str(latest.get("timeline_file", "")),
                str(hub_ctx.get("recent_total", 0)),
                str(hub_ctx.get("recent_visible_count", 0)),
            ]
        )


_dashboard: AnySoulEventDashboard | None = None


def get_anysoul_event_dashboard() -> AnySoulEventDashboard:
    """获取全局中枢事件看板实例。"""
    global _dashboard
    if _dashboard is None:
        _dashboard = AnySoulEventDashboard()
    return _dashboard


_WEBUI_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AnySoul 中枢事件流</title>
  <style>
    :root {
      --bg: #f6f2e9;
      --panel: #fffdf7;
      --line: #e7dcc7;
      --text: #2d2418;
      --muted: #7a6856;
      --accent: #0f766e;
      --accent-soft: rgba(15, 118, 110, 0.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top right, rgba(15,118,110,.12), transparent 35%),
        radial-gradient(circle at top left, rgba(180,83,9,.10), transparent 30%),
        var(--bg);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    header {
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(255,253,247,.86);
      backdrop-filter: blur(10px);
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    h1 { margin: 0; font-size: 18px; }
    .muted { color: var(--muted); font-size: 12px; }
    .chips { display: flex; gap: 8px; flex-wrap: wrap; margin-left: auto; }
    .chip {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
    }
    .live { color: var(--accent); font-weight: 700; }
    main {
      flex: 1;
      min-height: 0;
      display: grid;
      grid-template-columns: 420px 1fr;
      gap: 0;
    }
    aside {
      border-right: 1px solid var(--line);
      overflow: auto;
      padding: 12px;
    }
    .event-item {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 10px 12px;
      margin-bottom: 10px;
      cursor: pointer;
    }
    .event-item.active {
      border-color: rgba(15,118,110,.45);
      background: linear-gradient(180deg, var(--accent-soft), var(--panel));
    }
    .et { font-weight: 700; font-size: 13px; }
    .ts { color: var(--muted); font-size: 11px; margin-top: 3px; }
    .sm { margin-top: 7px; font-size: 12px; line-height: 1.45; color: #3a3024; }
    section {
      padding: 14px;
      overflow: auto;
    }
    .detail {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px;
      margin-bottom: 12px;
    }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: #1f1a14;
      color: #f6ece1;
      border-radius: 12px;
      padding: 12px;
      font-size: 12px;
      line-height: 1.55;
      overflow: auto;
    }
    @media (max-width: 960px) {
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); max-height: 45vh; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>AnySoul 中枢事件流</h1>
      <div class="muted">实时显示 timeline / pending / tasks 变化（SSE）</div>
    </div>
    <div class="chips" id="chips">
      <span class="chip">连接中...</span>
    </div>
  </header>
  <main>
    <aside id="list"></aside>
    <section>
      <div class="detail">
        <div style="font-weight:700; margin-bottom:10px;">中枢看到的 [近期事件] 文本（用于提示词）</div>
        <pre id="hub-block">- （暂无）</pre>
      </div>
      <div class="detail">
        <div style="font-weight:700; margin-bottom:10px;">中枢看到的 recent_events（结构）</div>
        <pre id="hub-json">{}</pre>
      </div>
      <div class="detail">
        <div id="detail-title" style="font-weight:700; margin-bottom:10px;">请选择左侧事件</div>
        <pre id="detail-json">{}</pre>
      </div>
    </section>
  </main>

  <script>
    const chips = document.getElementById('chips');
    const list = document.getElementById('list');
    const detailTitle = document.getElementById('detail-title');
    const detailJson = document.getElementById('detail-json');
    const hubBlock = document.getElementById('hub-block');
    const hubJson = document.getElementById('hub-json');
    let current = null;
    let selectedId = null;

    function esc(s) {
      return String(s || '').replace(/[&<>"']/g, m => ({
        '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
      }[m]));
    }

    function updateChips(snapshot, live) {
      const c = snapshot.counts || {};
      chips.innerHTML =
        `<span class="chip ${live ? 'live' : ''}">${live ? '实时连接' : '连接中断'}</span>` +
        `<span class="chip">timeline: ${c.timeline || 0}</span>` +
        `<span class="chip">pending: ${c.pending || 0}</span>` +
        `<span class="chip">processed: ${c.processed || 0}</span>` +
        `<span class="chip">active: ${c.tasks_active || 0}</span>` +
        `<span class="chip">completed: ${c.tasks_completed || 0}</span>`;
    }

    function render(snapshot) {
      current = snapshot;
      updateChips(snapshot, true);
      const hubContext = snapshot.hub_context || {};
      hubBlock.textContent = hubContext.recent_block_for_prompt || '- （暂无）';
      hubJson.textContent = JSON.stringify(hubContext, null, 2);
      const events = snapshot.events || [];
      list.innerHTML = '';
      if (!events.length) {
        list.innerHTML = '<div class="event-item"><div class="sm">暂无事件</div></div>';
        return;
      }
      events.forEach(ev => {
        const div = document.createElement('div');
        div.className = 'event-item' + (ev.event_id === selectedId ? ' active' : '');
        div.innerHTML =
          `<div class="et">${esc(ev.event_type)} <span class="muted">#${esc(ev.event_id)}</span></div>` +
          `<div class="ts">${esc(ev.timestamp)}</div>` +
          `<div class="sm">${esc(ev.summary || '')}</div>`;
        div.onclick = () => {
          selectedId = ev.event_id;
          detailTitle.textContent = `${ev.event_type}  |  ${ev.event_id}`;
          detailJson.textContent = JSON.stringify(ev, null, 2);
          render(snapshot);
        };
        list.appendChild(div);
      });
      if (!selectedId && events[0]) {
        selectedId = events[0].event_id;
        detailTitle.textContent = `${events[0].event_type}  |  ${events[0].event_id}`;
        detailJson.textContent = JSON.stringify(events[0], null, 2);
      }
    }

    function connect() {
      const es = new EventSource('/_anysoul_hub/api/stream');
      es.addEventListener('snapshot', e => render(JSON.parse(e.data)));
      es.addEventListener('update', e => render(JSON.parse(e.data)));
      es.onerror = () => {
        if (current) updateChips(current, false);
        es.close();
        setTimeout(connect, 3000);
      };
    }

    connect();
  </script>
</body>
</html>
"""
