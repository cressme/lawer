"""
WeChat Evidence Agent - 数据处理层 (L2)

提供微信消息解析、对话时间线重建、媒体高级处理等功能。
在 L1 提取层的基础上，将原始数据转化为结构化的证据素材。
"""

from .media_processor import MediaProcessor
from .message_parser import (
    AppSubType,
    MessageParser,
    MessageType,
    ParsedMessage,
)
from .timeline import DayMessages, Timeline, TimelineBuilder

__all__ = [
    "MessageParser",
    "ParsedMessage",
    "MessageType",
    "AppSubType",
    "TimelineBuilder",
    "Timeline",
    "DayMessages",
    "MediaProcessor",
]
