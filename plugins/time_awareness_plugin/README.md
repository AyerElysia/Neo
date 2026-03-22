# Time Awareness Plugin - 兼容占位

时间感知能力已经迁移到 `proactive_message_plugin`。
这个插件现在只保留为兼容占位，避免旧配置直接报错。

## 迁移后的能力

- 动态时间块注入
- `query_time` 工具
- 主动消息里的等待阶段感知
- 更强的时间流动感

## 兼容说明

- 这个插件不再注册任何 active 组件
- 保留配置只是为了不让旧配置立刻失效
- 建议从 `core.toml` 的插件列表中移除它

## 新实现位置

- 主实现：`plugins/proactive_message_plugin/README.md`
- 主工具：`plugins/proactive_message_plugin/tools/query_time.py`
- 时间辅助：`proactive_message_plugin/temporal.py`

## 版本历史

### 2.1.0

- 降级为兼容占位
- 时间感知能力迁移到 `proactive_message_plugin`
