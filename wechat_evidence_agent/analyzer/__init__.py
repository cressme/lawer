"""
WeChat Evidence Agent - AI 分析层 (L5)

提供基于大语言模型的聊天证据智能分析功能，包括：
1. 关键事实提取（承诺、金额、日期约定、违约行为等）
2. 法律分析（证据链评估、争议焦点识别、诉讼策略建议）
"""

from .fact_extractor import Fact, FactExtractor
from .legal_analyzer import AnalysisResult, LegalAnalyzer

__all__ = [
    "FactExtractor",
    "Fact",
    "LegalAnalyzer",
    "AnalysisResult",
]
