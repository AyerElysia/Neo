# 新 DFC 中枢化方案 v1

> 目标：把 DFC 从“聊天执行器”升级为“长期存在的决策中枢”，聊天仅作为任务态之一。  
> 范围：Neo-MoFox-Loop 当前代码基线，兼容现有 StreamLoop/Adapter 链路。

---

## 1. 背景与核心共识

你已经确认的新方向是：

1. **决策器长期存在**：不再每轮临时构造，而是常驻中枢，有稳定上下文状态。
2. **长期上下文来源于事件流历史**：维护“可见窗口”参数，决定中枢每次决策看到多少近期事件。
3. **中枢能力集是完整工具集合**：不仅聊天工具，还包括事件/待办/记忆/身份等工具。
4. **系统提示词改为 Soul + Memory 主导**：`soul.md` 与记忆快照成为核心认知输入。
5. **旧 DFC 人设注入机制可舍弃**：不再依赖 `personality` 配置拼接人设文本。

一句话：  
**中枢是常驻“主意识”，DFC-chat 是它的一种任务执行模式。**

---

## 2. 目标架构（逻辑层）

## 2.1 三层结构

1. **中枢层（常驻）**
- `DecisionHub`：维护长期状态、事件窗口、预算、策略。
- 统一入口：消息触发 + 心跳触发。

2. **任务层（可调度）**
- `ChatTask`（原 DFC 聊天能力）
- `TodoTask`
- `MemoryMaintenanceTask`
- `SoulReflectionTask`

3. **执行层（工具）**
- 中枢调用工具完成动作：读事件、改待办、检索记忆、输出消息等。

## 2.2 数据流主线

1. 事件进入 `events/pending`（消息事件/提醒事件/系统触发事件）。  
2. 中枢读取“最近窗口事件 + 当前状态”构建决策上下文。  
3. 中枢产出 `DecisionPlan`（是否回复、是否延迟、是否推进任务）。  
4. 任务执行器按计划调用工具。  
5. 事件状态迁移（`pending -> processed/archive`），并更新中枢状态。

---

## 3. 关键设计点

## 3.1 决策器常驻化

新增长期状态对象（建议持久化到 `workspace/state/decision_state.json`）：

```json
{
  "hub_id": "main",
  "last_tick_at": "2026-03-27T22:00:00",
  "event_cursor": "evt_20260327_001",
  "event_window_size": 200,
  "active_mode": "chat",
  "daily_budget_used": 23,
  "daily_budget_total": 100,
  "recent_decisions": []
}
```

说明：
- `event_window_size` 是你说的“她能看到多少近期事件”的核心参数。
- `event_cursor` 让中枢具备“连续阅读事件流”的能力，不重复吃同一批事件。

## 3.2 事件流上下文窗口

建议支持两种窗口策略（可组合）：

1. **数量窗口**：最近 N 条（默认 200）。  
2. **时间窗口**：最近 T 小时（默认 24h）。

最终参与决策的事件 = `数量窗口 ∩ 时间窗口`（或按配置取并集）。

额外建议：
- 对超长事件内容做摘要，不把原文全部塞给模型。
- 按优先级（high > medium > low）与新鲜度排序。

## 3.3 提示词体系重构（去 personality）

新系统提示词建议拆为三段：

1. **Soul 核心段**（来自 `soul.md`）
- 我是谁
- 核心价值
- 自我叙事
- 关系图谱摘要

2. **Memory 近期段**（来自 memory 检索）
- 与当前事件/stream 高相关的近期记忆摘要
- 关键历史承诺/未完成事项

3. **运行规则段**（框架固定）
- 工具调用约束
- 预算与风险控制
- 事件状态迁移规则

这将替代旧 DFC 的 `personality` 配置拼装逻辑。

## 3.4 工具中枢化

中枢工具集按“领域”组织，而不是按“插件”组织：

- 事件域：`list_events`, `event_detail`, `mark_event`
- 任务域：`create_todo`, `list_todos`, `update_todo`, `complete_todo`
- 记忆域：`write_memory`, `read_memory`, `search_memories`...
- 身份域：`read_soul`, `reflect_soul`
- 交互域：`send_message`（或兼容 `action-send_text`）
- 维护域：`think`, `schedule_heartbeat`, `heartbeat_status`

注：具体工具白名单以你截图中的清单为准，建议在配置里显式声明。

---

## 4. 模块落地方案（代码层）

## 4.1 新增模块

建议新增：

1. `src/core/cognition/decision_hub.py`
- `build_context()`
- `decide()`
- `act()`
- `persist_state()`

2. `src/core/cognition/event_window.py`
- 事件窗口读取与裁剪策略

3. `src/core/cognition/prompt_builder.py`
- 从 Soul/Memory 生成 system prompt

4. `plugins/anysoul_core/chatter.py`
- `AnySoulCentralChatter`（新主 chatter）

## 4.2 复用与改造现有 DFC

保留并复用：
- DFC 的 tool call FSM
- 去重机制与 `pass/stop` 控制流
- stream 基础集成（不动 transport 主链路）

替换掉：
- 旧的人设注入逻辑（`personality`）
- 仅按未读消息构造上下文的单一输入模式

## 4.3 事件入口补齐

建议在 `anysoul_core` 增加事件处理器：
- 订阅 `ON_MESSAGE_RECEIVED`
- 将消息标准化写入 `events/pending/*.json`
- 保持 message 与事件流双轨兼容（过渡期）

---

## 5. 配置设计（建议）

在 `config/plugins/anysoul_core.toml` 增加：

```toml
[central_hub]
enabled = true
event_window_size = 200
event_window_hours = 24
max_recent_decisions = 100

[central_hub.prompt]
use_soul_prompt = true
use_memory_prompt = true
disable_legacy_personality = true

[central_hub.tools]
profile = "core_v1"
```

并在 `default_chatter` 配置中增加：
- `legacy_mode = false`（用于灰度）

---

## 6. 分阶段实施计划

## 阶段 A：中枢壳子上线（低风险）
- 新增 `DecisionHub` 状态与事件窗口模块。
- 暂时仍调用 DFC 原执行流程。
- 不切流量，只做内部可运行。

验收：
- 能持久化中枢状态；
- 能读取事件窗口并输出决策日志。

## 阶段 B：提示词改造（中风险）
- 接入 Soul + Memory 提示词主干。
- 关闭 legacy personality 注入（可开关回退）。

验收：
- 对话输出不崩；
- 系统提示来源可观测、可回放。

## 阶段 C：新 Chatter 接管（中高风险）
- `AnySoulCentralChatter` 注册并灰度启用。
- `default_chatter` 退为 fallback。

验收：
- 群聊/私聊场景稳定；
- 工具调用成功率不低于旧 DFC。

## 阶段 D：心跳与消息双触发统一（高价值）
- 心跳回调改为中枢统一入口。
- 消息触发与心跳触发同核决策。

验收：
- 决策路径统一；
- 预算控制有效；
- 事件状态迁移完整。

---

## 7. 风险与回滚

## 风险

1. 上下文暴涨导致时延上升  
2. 工具过多导致调用抖动  
3. 群聊误触发自主行为  
4. 新中枢与旧 chatter 冲突选路

## 对策

1. 严格事件窗口 + 摘要压缩  
2. 工具分层白名单（按 mode 裁剪）  
3. 保留 sub-agent gate，默认群聊保守策略  
4. 配置开关灰度 + fallback 到 default_chatter

## 回滚

- 一键关闭 `central_hub.enabled`
- 恢复 `default_chatter` 为主
- 保留事件与状态数据，不丢历史

---

## 8. MVP（最小可行版本）

先做最小闭环，不一次性重构全量：

1. 中枢常驻状态（含 `event_window_size` 参数）  
2. 事件窗口 + Soul/Memory 提示词接入  
3. 复用 DFC FSM 执行链  
4. 新 Chatter 可切换运行  
5. legacy personality 注入可关闭

达到这 5 点，就能证明“决策器中枢化”方向成立。

---

## 9. 结论

这个方案的本质是：

**把“聊天器主导系统”改成“中枢主导系统”。**

DFC 不再是系统本体，而是中枢调度的一种任务执行模式；  
事件流历史成为长期上下文主线；  
Soul + Memory 成为身份与认知连续性的主提示词来源。

