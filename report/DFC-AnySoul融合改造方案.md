# DFC × AnySoul 融合改造方案（决策器中枢化）

> 面向当前 Neo-MoFox-Loop 代码基线  
> 目标：将 `default_chatter`（DFC）的决策能力升级为 `anysoul_core` 的中枢系统，并落地为新的 Chatter

---

## 1. 改造目标（你这次需求的准确翻译）

你要的不是“再加一个工具插件”，而是：

1. 保留 DFC 在消息场景下成熟的决策能力（sub-agent 门控、工具调用 FSM、冷却机制等）。
2. 把决策权从“仅处理未读消息”升级到“统一管理消息、事件、待办、记忆、身份核、心跳预算”的中枢。
3. 最终交付一个**全新的 Chatter**，不是简单拼接两个插件。

一句话：  
**让 DFC 从“聊天执行器”变成 AnySoul 主意识的“决策内核”。**

---

## 2. 当前系统事实（基于现有代码）

### 2.1 DFC（default_chatter）已经具备的优势

- 有完整会话执行流程：`WAIT_USER -> MODEL_TURN -> TOOL_EXEC -> FOLLOW_UP`。
- 有 sub-agent 判定（是否应该回复），能降低群聊无效回复。
- 有工具调用去重、`pass_and_wait`/`stop_conversation` 控制流。
- 和 `StreamLoopManager`、`MessageReceiver`、`MessageSender` 已稳定耦合。

### 2.2 AnySoul（anysoul_core）已经具备的能力

- 已有 `workspace/memory/todo/soul/heartbeat` 服务与工具。
- 心跳服务可定时触发，支持预算、事件读取、待办检查。
- 但目前是“能力集合”，没有自己的 `Chatter` 中枢。

### 2.3 缺口（必须补）

- DFC 的决策输入主要是“未读消息 + 历史”，缺少“事件流 + todo + soul + 预算”统一上下文。
- AnySoul 的心跳回调尚未接入一个统一决策器，只是框架骨架。
- 两边都能跑，但还不是“一个统一认知循环”。

---

## 3. 目标架构（融合后的形态）

## 3.1 新角色划分

- **AnySoulDecisionHub（新）**：决策中枢（主意识），统一做“是否回复、做什么、何时做、先后顺序”。
- **AnySoulDFCChatter（新）**：对接 StreamLoop 的 Chatter 外壳，负责与现有消息流兼容。
- **AnySoul Services（已有）**：`memory/todo/soul/heartbeat/workspace` 作为中枢的数据与执行底座。
- **Tool Router（新）**：按场景裁剪可用工具，避免模型每轮看全部工具。

## 3.2 决策中枢输入与输出

### 输入（DecisionContext）

- 消息域：当前 stream 未读、历史窗口、chat_type、platform。
- AnySoul 域：pending events、active/due todos、memory 检索结果、soul 快照、heartbeat budget。
- 运行域：冷却状态、最近动作、失败重试计数。

### 输出（DecisionPlan）

- `mode`: `reply | defer | schedule | maintain | reflect`
- `actions`: 要执行的工具调用序列（含优先级）
- `response_policy`: 是否允许对外发消息（遵循单一出口）
- `next_wait`: 下次唤醒策略（Wait 秒数、或等待新消息）

---

## 4. 关键改造点：把 DFC 决策器“中枢化”

## 4.1 抽离 DFC 决策核心（第一原则）

将 DFC 中可复用的决策能力抽离成独立模块，不再绑在 `default_chatter` 插件内部：

- `decision_agent.py`（是否回复门控）  
- `runners.py`（FSM 主流程）  
- `tool_flow.py`（工具调用编排与去重）

建议抽取到（示例）：

- `src/core/cognition/decision_kernel.py`
- `src/core/cognition/tool_orchestrator.py`
- `src/core/cognition/response_gate.py`

这样 AnySoul 能直接调用这套内核，而不是复制粘贴。

## 4.2 决策输入升级（第二原则）

把 DFC 过去只看“消息”的输入，升级成 AnySoul 的统一上下文：

- 在进入 `MODEL_TURN` 前，先执行：
  - `list_events`（pending）
  - `list_todos`（active/due）
  - `search_memories`（按 stream/topic/user）
  - `read_soul`（身份核摘要）
  - `heartbeat_status`（预算约束）

这一步由 `AnySoulDecisionHub.build_context()` 完成，而不是散落在 prompt 里。

## 4.3 决策执行双通道（第三原则）

中枢同时支持两类触发：

1. **Reactive（消息触发）**：来自 `StreamLoopManager` 的常规聊天流程。  
2. **Proactive（心跳触发）**：来自 `HeartbeatService` 的自主循环。

两类触发都走同一个 `AnySoulDecisionHub.decide_and_act()`，保证行为一致性。

## 4.4 单一出口保持不变（第四原则）

无论消息触发还是心跳触发，对外输出都统一走现有发送链：

- `BaseAction._send_to_stream` / `MessageSender.send_message`

即：中枢可以决定“要不要发”，但“怎么发”仍复用当前稳定通道。

---

## 5. 新 Chatter 设计（落地对象）

建议新增：

- `plugins/anysoul_core/chatter.py`
  - `class AnySoulDFCChatter(BaseChatter)`
  - `chatter_name = "anysoul_dfc_chatter"`

在 `AnySoulCorePlugin.get_components()` 中加入该 Chatter。

### 行为逻辑

1. 从 stream 读取未读消息。
2. 调 `AnySoulDecisionHub` 构建上下文并决策。
3. 执行工具编排（复用 DFC FSM 与 tool_flow 机制）。
4. 输出 `Wait/Stop/Failure/Success` 给 StreamLoop。

### 选择策略（避免与 default_chatter 冲突）

当前 `ChatterManager` 会按兼容分数 + 签名字典序选 Chatter。  
为确保新 Chatter 优先，可采用任一策略：

1. 给新 Chatter 设置 `associated_platforms=["qq"]`（可多 +1 分）。  
2. 在配置中支持禁用 `default_chatter`。  
3. 后续加显式优先级字段（中期优化）。

---

## 6. 分阶段实施路线（建议按这个顺序）

## 阶段 A：内核抽离（低风险）

目标：不改行为，只抽模块。

- 抽离 DFC 决策与 tool orchestrator 到 `src/core/cognition/*`。
- `default_chatter` 改为调用新内核，保持对外行为不变。
- 验证现有回归（消息回复、工具调用、冷却）。

交付标志：  
`default_chatter` 行为不变，但决策逻辑已可被 AnySoul 重用。

## 阶段 B：AnySoul 中枢服务化（中风险）

目标：建立统一决策上下文。

- 新增 `AnySoulDecisionHub` 服务：
  - `build_context(stream_id | heartbeat_id)`
  - `decide(context)`
  - `act(plan)`
- 接入 `memory/todo/soul/heartbeat/events` 数据。
- 增加上下文压缩与 token 预算策略。

交付标志：  
消息触发时已经可以“感知 AnySoul 数据域”。

## 阶段 C：新 Chatter 上线（中高风险）

目标：推出 `AnySoulDFCChatter`，并灰度替换 DFC。

- 在 `anysoul_core` 注册新 Chatter。
- 增加配置开关（例如 `config/plugins/anysoul_core.toml`）：
  - `enable_anysoul_dfc_chatter = true`
  - `disable_default_chatter = false`（灰度阶段）
- 小流量验证后切主。

交付标志：  
消息侧主 Chatter 切到融合版。

## 阶段 D：心跳闭环（高价值）

目标：实现“同一决策内核同时驱动消息与心跳”。

- `HeartbeatService.register_callback(...)` 指向 `AnySoulDecisionHub`。
- 心跳触发可以推进 todo、整理记忆、必要时主动表达。
- 增加预算与节流防护（避免过度自主输出）。

交付标志：  
“消息驱动 + 心跳驱动”统一到一个认知内核。

---

## 7. 风险点与对策

1. 工具过多导致模型决策抖动  
对策：按场景裁剪工具（reply 模式不暴露维护类工具）。

2. 群聊误触发主动行为  
对策：保留并强化 sub-agent gate；群聊默认更保守策略。

3. 心跳与消息并发冲突（同一 stream 双写）  
对策：以 `stream_id` 维度加执行锁；心跳对正在处理的 stream 只做轻量维护。

4. 上下文过长导致成本和时延飙升  
对策：事件/todo/memory/soul 各自做摘要上限 + token budget 裁剪。

5. 回归风险  
对策：先“抽离不改行为”，再逐步开关切流，不一次性替换。

---

## 8. 建议的首批代码落点（可直接开工）

1. 新增中枢模块
- `src/core/cognition/decision_kernel.py`
- `src/core/cognition/anysoul_decision_hub.py`
- `src/core/cognition/tool_orchestrator.py`

2. 重构 DFC 复用中枢
- `plugins/default_chatter/runners.py`
- `plugins/default_chatter/tool_flow.py`
- `plugins/default_chatter/decision_agent.py`

3. AnySoul 新 Chatter
- `plugins/anysoul_core/chatter.py`
- `plugins/anysoul_core/plugin.py`（`get_components()` 增加 chatter）
- `plugins/anysoul_core/config.py`（增加切换开关）

4. 事件入口补齐
- `plugins/anysoul_core/event_handler.py`（订阅 `ON_MESSAGE_RECEIVED` 写入 `events/pending`）

---

## 9. 最小可行版本（MVP）定义

如果你要快速验证方向，MVP 建议只做这些：

1. 新建 `AnySoulDFCChatter`，复用当前 DFC FSM。  
2. 在每轮决策前附加：`list_todos + search_memories + read_soul`。  
3. 保留 `send_text` 主出口与现有 StreamLoop，不动消息基础设施。  
4. 先不做主动发言，只做“消息触发下的中枢化决策”。  

达到这 4 点，就已经是“DFC 决策器中枢化”的第一版，不会高风险重构全系统。

---

## 10. 结论

这次融合的正确路径不是“把 AnySoul 工具塞进 DFC”，而是：

**把 DFC 的决策器提炼成内核，再让 AnySoul 的事件/记忆/待办/身份/心跳全部成为这个内核的输入域。**

最终得到的将是一个新的主 Chatter：  
**既保留 DFC 的对话稳定性，又具备 AnySoul 的时间连续性与自主性。**

