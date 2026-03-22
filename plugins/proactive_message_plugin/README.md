# 主动消息插件 (Proactive Message Plugin)

这个插件现在不只是“等待后主动发消息”，而是一个把时间感知、主动性和内心独白绑在一起的系统。

## 核心效果

- 用户一段时间不回复后，插件会进入等待状态
- 等待到点后，LLM 会先生成内心独白，再决定发消息还是继续等
- 每次 prompt 构建时，都会注入一段动态时间块
- 时间不再只是“现在几点”，而是“余温、悬停、牵挂、收回”的流动体验
- `query_time` 已并入本插件，不再依赖独立的 `time_awareness_plugin`

## 你现在能得到什么

主模型每一轮都能看到：

- 当前时间
- 距离上次用户消息多久
- 当前时间阶段
- 距离上次主动发言多久
- 主观等待压强

内心独白阶段还能看到：

- 稳定的时间哲学 reminder
- 动态时间块
- 之前的内心独白历史

另外，内心独白现在还能调用：

- `send_text`
- `send_emoji_meme`
- `wait_longer`
- `think`

## 配置

在 `config/plugins/proactive_message_plugin/config.toml` 中：

```toml
[settings]
enabled = true
first_check_minutes = 5.0
min_wait_interval_minutes = 1.0
max_wait_minutes = 180.0
post_send_followup_minutes = 180.0
monologue_history_limit = 20

inject_prompt = true
target_prompt_names = ["default_chatter_user_prompt"]
time_prompt_title = "时间感知"

ignored_chat_types = ["group"]
```

### 参数说明

- `first_check_minutes`：第一次进入等待后，多久触发一次独白
- `min_wait_interval_minutes`：LLM 继续等待时的最小间隔
- `max_wait_minutes`：累计最大等待时间
- `post_send_followup_minutes`：主动发消息后，无人回复时再次检查的间隔
- `monologue_history_limit`：独白历史注入数量
- `inject_prompt`：是否注入动态时间块
- `target_prompt_names`：哪些 prompt 会被注入
- `time_prompt_title`：时间块标题

## 工作流程

```text
用户发消息
  ↓
插件记录聊天流状态
  ↓
Chatter 进入 Wait
  ↓
等待 first_check_minutes
  ↓
生成内心独白
  ↓
LLM 选择：
  ├─ send_text / send_emoji_meme → 主动发消息 → 等待 follow-up
  └─ wait_longer → checkpoint 等待时长 → 继续等待
```

## 时间感知工作流

动态时间块会在 `on_prompt_build` 时注入到 `default_chatter_user_prompt` 的 `extra` 区域。

注入内容包括：

- 现在时间
- 当前时间阶段
- 距离上次用户消息
- 等待感
- 时间余温
- 主观等待压强

这能让主模型每轮都感到“时间在流动”，而不是只在启动时看见一个静态时间戳。

## 工具

### `query_time`

查询当前时间，返回中式时间描述。

### `wait_longer`

让内心独白表达“先等等”的意愿。

## 组件

| 文件 | 作用 |
|------|------|
| `plugin.py` | 插件主类、事件处理器、时间提醒注入 |
| `service.py` | 每个聊天流的状态、等待计时、时间块构建 |
| `inner_monologue.py` | 内心独白 prompt、LLM 调用、结果解析 |
| `temporal.py` | 时间阶段、等待压强、时间描述函数 |
| `tools/query_time.py` | 查询时间工具 |
| `tools/wait_longer.py` | 继续等待工具 |

## 迁移说明

`time_awareness_plugin` 已经迁移到这里。
如果你还在 `core.toml` 里启用了旧插件，建议移除它，避免重复维护同一套时间语义。

## 版本

### 1.1.0

- 并入动态时间感知
- 新增 `query_time`
- 新增时间 prompt 注入器
- 内心独白可调用 `send_emoji_meme`
