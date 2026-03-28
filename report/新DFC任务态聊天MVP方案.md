# 新DFC任务态聊天 MVP 方案

## 1. 目标边界

本阶段只实现一个任务态：`聊天`。  
中枢系统只做一个决策：`要不要进入聊天任务`。

不做的内容：
- 不做多任务并行编排
- 不做复杂意图分类
- 不做完整 AnySoul 全能力调度

## 2. 最小闭环

闭环定义：

1. 收到 QQ 消息（`on_message_received`）  
2. 中枢把消息写入 `events/pending/*.json`  
3. 中枢决策 `should_chat = true/false`  
4. 若为 `true`，中枢创建 `chat_task` 到 `tasks/active/*.json`  
5. 新 Agent 执行聊天任务（生成回复并发送）  
6. Agent 回传执行结果摘要（不回传回复原文）  
7. 中枢写回 `tasks/completed/*.json` 与 `events/processed/*.json`，更新中枢状态

## 3. 组件改造

### 3.1 新增 DecisionHubService（长期驻留）

职责：
- 维护中枢状态：`state/decision_hub.json`
- 维护近期事件窗口：`event_window_size` + `recent_events`
- 消费 `events/pending`，生成 `events/processed`
- 在聊天决策成立时派发任务并回收结果

### 3.2 新增 ChatTaskAgent（任务执行）

职责：
- 输入：任务目标、当前事件、近期事件摘要、Soul/Memory片段
- 产出：
  - 实际回复（用于发送）
  - `expression_summary`（表达概览，不含原文）
  - 发送状态
- 发送动作复用 `default_chatter` 的 `send_text` action

### 3.3 新增消息事件处理器

`CentralHubMessageEventHandler`：
- 订阅 `ON_MESSAGE_RECEIVED`
- 将消息转成中枢事件入队
- 触发中枢处理一轮（MVP 先单条处理）

## 4. 数据落盘约定

### 4.1 事件

- `events/pending/{event_id}.json`：待决策事件
- `events/processed/{event_id}.json`：已处理事件 + 决策结果 + 任务结果摘要

### 4.2 任务

- `tasks/active/{task_id}.json`：执行中任务
- `tasks/completed/{task_id}.json`：完成/失败任务

### 4.3 中枢状态

`state/decision_hub.json` 关键字段：
- `event_window_size`: 中枢可见的近期事件数量
- `recent_events`: 近期事件摘要列表（中枢稳定上下文）
- `stats`: 处理总量、派发量、成功/失败数

## 5. 决策规则（MVP）

先采用可解释、可控的规则：

- 空文本：不聊天
- 机器人消息（`sender_role=bot` 或 `is_self=true`）：不聊天
- 其余默认：聊天

后续可替换为 LLM/策略混合决策器，但接口保持不变。

## 6. 与“长期中枢”一致性

- 中枢服务为常驻单例，不随单轮对话销毁
- 中枢上下文 = 事件流历史摘要（`recent_events`）
- 窗口大小由状态参数控制，可热调整
- 心跳开启时，中枢可挂接心跳回调持续处理 backlog

## 7. 实施顺序

1. 新建 `decision_hub.py`（状态、决策、派发、回收）
2. 新建 `chat_task_agent.py`（任务执行 + 摘要回传）
3. 新建 `central_hub_event_handler.py`（消息转事件）
4. 在 `plugin.py` 完成组件注册与初始化接线
5. 增加测试并跑通基础回归
