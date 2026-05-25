"""
WeChat Evidence Agent - 证据生成层 (L4)

提供法庭证据文件生成功能，包括：
- 证据清单生成（Word格式，符合中国法院证据清单规范）
- 聊天记录可视化渲染（仿微信对话界面）
- 多格式导出（DOCX / HTML / PDF / ZIP打包）
"""

from .generator import EvidenceItem, EvidenceListGenerator
from .chat_renderer import ChatRenderer
from .exporter import EvidenceExporter

__all__ = [
    "EvidenceItem",
    "EvidenceListGenerator",
    "ChatRenderer",
    "EvidenceExporter",
]
