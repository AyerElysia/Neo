"""消息接收器。

负责接收并处理来自 Adapter 的消息信封。
参考 old/chat/message_receive/message_handler.py 的设计。
"""

from typing import TYPE_CHECKING, Any

from mofox_wire import MessageEnvelope

from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.models.message import Message

logger = get_logger("message_receiver")


class MessageReceiver:
    """消息接收器。

    负责接收 MessageEnvelope 并协调消息处理流程。

    职责：
    1. 接收来自 Adapter 的 MessageEnvelope
    2. 使用 MessageConverter 转换为 Message
    3. 触发消息接收前事件
    4. 使用 MessageRouter 路由消息
    5. 触发消息接收后事件

    Attributes:
        _converter: 消息转换器
        _router: 消息路由器
        _event_manager: 事件管理器引用

    Examples:
        >>> receiver = MessageReceiver()
        >>> await receiver.receive_envelope(envelope, "my_plugin:adapter:qq")
    """

    def __init__(self) -> None:
        """初始化消息接收器。"""
        from src.core.transport.message_receive.converter import MessageConverter
        from src.core.transport.message_receive.router import MessageRouter

        self._converter = MessageConverter()
        self._router = MessageRouter()
        self._event_manager: Any = None
        self._lock: Any = None

        logger.info("MessageReceiver 初始化完成")

    def set_event_manager(self, event_manager: Any) -> None:
        """设置事件管理器引用。

        Args:
            event_manager: 事件管理器实例

        Examples:
            >>> receiver.set_event_manager(get_event_manager())
        """
        self._event_manager = event_manager
        logger.debug("MessageReceiver 设置事件管理器")

    async def receive_envelope(
        self,
        envelope: MessageEnvelope,
        adapter_signature: str,
    ) -> None:
        """接收消息信封并处理。

        这是消息接收的入口方法，完整的处理流程包括：
        1. 转换 MessageEnvelope 为 Message
        2. 触发接收前事件
        3. 路由消息到合适的处理器
        4. 触发接收后事件

        Args:
            envelope: 消息信封
            adapter_signature: 发送方适配器签名

        Raises:
            ValueError: 如果消息信封格式不正确
        """
        try:
            # 1. 转换为 Message
            message = await self._converter.envelope_to_message(envelope)

            # 2. 触发接收前事件
            await self._emit_pre_receive_event(message, envelope, adapter_signature)

            # 3. 路由消息
            await self._router.route_message(message)

            # 4. 触发接收后事件
            await self._emit_post_receive_event(message)

    async def _emit_pre_receive_event(
        self,
        message: "Message",
        envelope: MessageEnvelope,
        adapter_signature: str,
    ) -> None:
        """触发消息接收前事件。

        Args:
            message: 消息对象
            envelope: 消息信封
            adapter_signature: 适配器签名
        """
        if self._event_manager:
            try:
                from src.core.components.types import EventType

                await self._event_manager.publish_event(
                    EventType.ON_MESSAGE_RECEIVED,
                    {
                        "message": message,
                        "envelope": envelope,
                        "adapter_signature": adapter_signature,
                    },
                )
            except Exception as e:
                logger.warning(f"触发接收前事件失败: {e}")

    async def _emit_post_receive_event(self, message: "Message") -> None:
        """触发消息接收后事件。

        Args:
            message: 消息对象
        """
        # 可以添加更多后处理逻辑
        # 目前暂无后处理事件
        pass

# 全局单例
_global_message_receiver: "MessageReceiver | None" = None


def get_message_receiver() -> MessageReceiver:
    """获取全局 MessageReceiver 单例。

    Returns:
        MessageReceiver: 全局 MessageReceiver 单例

    Examples:
        >>> receiver = get_message_receiver()
    """
    global _global_message_receiver
    if _global_message_receiver is None:
        _global_message_receiver = MessageReceiver()
    return _global_message_receiver


def set_message_receiver(receiver: MessageReceiver) -> None:
    """设置全局 MessageReceiver 单例。

    Args:
        receiver: MessageReceiver 实例

    Examples:
        >>> set_message_receiver(MessageReceiver())
    """
    global _global_message_receiver
    _global_message_receiver = receiver


def reset_message_receiver() -> None:
    """重置全局 MessageReceiver。

    主要用于测试场景，确保测试之间不会相互影响。

    Examples:
        >>> reset_message_receiver()
    """
    global _global_message_receiver
    _global_message_receiver = None


__all__ = [
    "MessageReceiver",
    "get_message_receiver",
    "set_message_receiver",
    "reset_message_receiver",
]
