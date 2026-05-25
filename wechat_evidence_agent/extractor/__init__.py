"""
WeChat Evidence Agent - 数据提取层 (L1)

提供微信本地数据库解密、消息提取、媒体文件解码等功能。
用于律师从微信PC客户端本地加密数据库中提取聊天记录作为电子证据。
"""

from .wechat_db import WeChatDBExtractor
from .media import MediaExtractor
from .contacts import ContactManager

__all__ = [
    "WeChatDBExtractor",
    "MediaExtractor",
    "ContactManager",
]
