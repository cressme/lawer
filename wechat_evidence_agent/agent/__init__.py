"""
WeChat Evidence Agent - 智能代理层 (L3)

基于 LLM 的法律证据分析智能助手核心模块。
提供对话管理、工具调度、案件记忆等功能，
律师可通过自然语言与系统交互完成证据整理工作。
"""

from .core import EvidenceAgent
from .memory import CaseInfo, CaseMemory, EvidenceMark
from .tools import OPENAI_TOOLS, ToolExecutor

__all__ = [
    "EvidenceAgent",
    "CaseMemory",
    "CaseInfo",
    "EvidenceMark",
    "ToolExecutor",
    "OPENAI_TOOLS",
]
