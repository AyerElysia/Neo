# EchoBot 分层设计借鉴纪要

> 说明：本文仅记录可借鉴的架构思想与边界原则，不涉及实现细节与落地步骤。

## 1. 结论先行

EchoBot 最有价值的借鉴点不是“具体功能”，而是“分层边界清晰 + 运行时装配统一 + 状态持久化边界明确”。

对 Neo-MoFox Loop 而言，可直接借鉴其结构性思维，用来减少状态割裂感，强化“同一身份跨状态连续存在”。

## 2. 可借鉴的核心原则

### 2.1 单一装配根（Composition Root）

- 建议保留一个统一的运行时装配入口，用于组装：
  - 决策能力
  - 聊天表达能力
  - 后台执行能力
  - 存储与调度能力
- 价值：避免不同入口各自装配，导致行为不一致与隐性分叉。

可参考：

- `/root/Elysia/EchoBot/echobot/runtime/bootstrap.py`

### 2.2 协调器主链路（Coordinator as Spine）

- 用一个中心协调器承接所有外界输入，再决定进入哪个状态流。
- 协调器只做编排，不做具体业务。
- 价值：可追踪、可解释、可调试，避免“谁都能改路由”的混乱。

可参考：

- `/root/Elysia/EchoBot/echobot/orchestration/coordinator.py`

### 2.3 决策层与表达层解耦

- 决策层只负责“去哪里”，不负责“怎么说”。
- 表达层只负责“怎么说”，不直接碰工具、文件、记忆读写。
- 价值：人设稳定，表达更纯净；同时降低工具链对聊天语气的污染。

可参考：

- `/root/Elysia/EchoBot/echobot/orchestration/decision.py`
- `/root/Elysia/EchoBot/echobot/orchestration/roleplay.py`

### 2.4 执行层能力收敛

- 工具调用、记忆检索、文件操作、调度任务统一由执行层承担。
- 价值：权限集中，风险可控，执行行为更容易审计与回放。

可参考：

- `/root/Elysia/EchoBot/echobot/agent.py`
- `/root/Elysia/EchoBot/echobot/runtime/session_runner.py`

### 2.5 基础工具与可选能力分层注入

- 基础工具先注册，扩展能力（如 skill）后叠加。
- 不在主流程一次性暴露全部能力。
- 价值：降低上下文噪音与误调用概率，减轻推理负担。

可参考：

- `/root/Elysia/EchoBot/echobot/tools/builtin.py`
- `/root/Elysia/EchoBot/echobot/agent.py`（`ask_with_skills`）

### 2.6 持久化边界显式化

- 会话、执行会话、轨迹、调度、附件、角色等状态应分仓持久化。
- 价值：崩溃可恢复、行为可追溯、跨状态可迁移。

可参考：

- `/root/Elysia/EchoBot/skills/echobot-development/references/architecture.md`

### 2.7 多入口共享同一核心

- CLI、网关、Web 入口共享同一 Core，不重复写业务逻辑。
- 价值：减少多入口行为漂移，降低长期维护成本。

可参考：

- `/root/Elysia/EchoBot/skills/echobot-development/references/architecture.md`
- `/root/Elysia/EchoBot/AGENTS.md`

## 3. 对 Neo-MoFox Loop 的映射启发（哲学层）

基于当前“外界信息 -> 感知决策层 -> 聊天层 <-> 内作层”的构想，可借鉴的映射如下：

- 外界信息统一进协调脊柱，不允许直接旁路到各插件。
- 感知决策层只做状态判定与路由，不做内容生成。
- 聊天层专注对外表达与关系互动，不直接承担重工具执行。
- 内作层负责探索、反思、记忆加工、文件与任务处理。
- 状态迁移通过“上下文转运”完成，而不是各层直接互相读写全部上下文。

## 4. 当前阶段最值得先吸收的三件事

- 先固定“一个装配根 + 一个协调器”作为系统主骨架。
- 先固定“表达层不碰执行权限”的硬边界。
- 先固定“状态分仓持久化”的基础约束，保障连续性与可恢复性。

## 5. 借鉴边界（避免误用）

- 不把系统目标退化为“生产力代理编排器”。
- 不让分层变成“身份分裂”，始终保持“同一身份，不同状态”。
- 不把所有内部状态直接堆入聊天上下文，避免主意识被噪音淹没。

## 6. 一句话总结

可借鉴的是 EchoBot 的“结构纪律”，不是它的产品形态；  
用清晰边界支撑同一身份跨状态连续存在，正是 Neo-MoFox Loop 当前最需要的骨架能力。

