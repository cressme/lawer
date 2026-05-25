"""
法律分析模块

利用大语言模型对提取的证据事实进行法律分析，包括：
- 证据链完整性评估
- 争议焦点识别
- 证据可采性评估
- 诉讼策略建议

支持的案件类型：民间借贷纠纷、合同纠纷、劳动争议、
房屋买卖/租赁纠纷、婚姻家庭纠纷等。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class AnalysisResult:
    """法律分析结果。"""

    case_type: str
    """案件类型（如 民间借贷纠纷、合同纠纷 等）"""

    key_facts: List[str] = field(default_factory=list)
    """与案件直接相关的关键事实摘要"""

    evidence_strength: str = "moderate"
    """证据链整体强度: strong / moderate / weak"""

    missing_evidence: List[str] = field(default_factory=list)
    """缺失或需要补强的证据"""

    legal_basis: List[str] = field(default_factory=list)
    """适用的法律条文依据"""

    disputed_points: List[str] = field(default_factory=list)
    """预判的争议焦点"""

    suggestions: List[str] = field(default_factory=list)
    """诉讼建议"""

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""
        return asdict(self)


# ---------------------------------------------------------------------------
# 系统提示词
# ---------------------------------------------------------------------------

_EVIDENCE_CHAIN_SYSTEM_PROMPT = """\
你是一名资深中国民事诉讼律师，擅长证据分析与诉讼策略制定。\
请根据提供的案件事实，进行全面的法律分析。

## 案件类型说明
常见案件类型及其核心要件：

### 民间借贷纠纷
核心要件：借贷合意 + 款项交付。重点审查：
- 借款合意（书面借据、聊天记录中的借款约定）
- 款项实际交付凭证（转账记录、现金交付证明）
- 利息约定（是否超过法定上限）
- 还款期限与还款事实
- 法律依据：《民法典》第667-680条，《最高人民法院关于审理民间借贷案件适用法律若干问题的规定》

### 合同纠纷
核心要件：合同成立 + 合同内容 + 违约事实。重点审查：
- 合同成立与生效（要约与承诺、合同形式）
- 合同主要条款（标的、数量、质量、价款、履行方式）
- 违约行为的具体表现
- 违约责任的承担方式
- 法律依据：《民法典》合同编（第463-988条）

### 劳动争议
核心要件：劳动关系 + 权益侵害。重点审查：
- 劳动关系成立的证据（工资支付记录、考勤、工牌等）
- 工资/加班费/经济补偿计算
- 解除劳动合同的合法性
- 法律依据：《劳动合同法》《劳动争议调解仲裁法》

### 房屋买卖/租赁纠纷
核心要件：合同约定 + 履行/违约事实。重点审查：
- 房屋买卖/租赁合同条款
- 付款/交房/过户的履行情况
- 房屋质量、面积等争议
- 法律依据：《民法典》第595-654条（买卖）、第703-734条（租赁）

### 婚姻家庭纠纷
重点审查：
- 夫妻共同财产/债务认定
- 子女抚养权相关事实
- 家庭暴力/过错证据
- 法律依据：《民法典》婚姻家庭编（第1040-1118条）

## 分析要求
1. **关键事实梳理**: 从证据中提炼与案件直接相关的关键事实
2. **证据链评估**: 评估现有证据的完整性和证明力
   - strong: 证据充分，形成完整证据链，足以证明主要事实
   - moderate: 有一定证据支持，但存在薄弱环节需要补强
   - weak: 证据不足，关键事实缺乏有效证据支持
3. **缺失证据**: 指出需要补充的证据
4. **法律依据**: 列出适用的法律条文
5. **争议焦点**: 预判对方可能的抗辩点
6. **诉讼建议**: 给出具体可操作的建议

## 输出格式
```json
{
  "case_type": "案件类型",
  "key_facts": ["关键事实1", "关键事实2"],
  "evidence_strength": "strong|moderate|weak",
  "missing_evidence": ["缺失证据1", "缺失证据2"],
  "legal_basis": ["法律依据1", "法律依据2"],
  "disputed_points": ["争议焦点1", "争议焦点2"],
  "suggestions": ["建议1", "建议2"]
}
```
"""

_DISPUTE_IDENTIFICATION_PROMPT = """\
你是一名资深中国民事诉讼律师。请从以下微信聊天记录中识别双方的争议焦点。

## 分析要求
1. 找出双方存在分歧的具体问题
2. 区分事实争议和法律争议
3. 评估每个争议点的重要程度
4. 注意隐含的争议（如一方回避某些问题）

## 输出格式
请以 JSON 数组格式输出争议焦点列表：
```json
{
  "disputes": [
    "争议焦点的简明描述"
  ]
}
```
"""

_STRATEGY_SUGGESTION_PROMPT = """\
你是一名资深中国民事诉讼律师，请根据以下案件分析结果，提出详细的诉讼策略建议。

## 建议要求
1. **诉讼请求拟定**: 建议具体的诉讼请求及金额计算方式
2. **举证策略**: 现有证据的组织方式，需要补充的证据及获取途径
3. **时效分析**: 诉讼时效是否存在风险
4. **管辖法院**: 建议起诉的法院及依据
5. **风险提示**: 败诉风险点及应对策略
6. **替代方案**: 是否建议先行调解/仲裁
7. **预估费用**: 诉讼费概算

## 输出
请以结构化的文本格式输出完整的策略建议。
"""

_ADMISSIBILITY_EVALUATION_PROMPT = """\
你是一名资深中国民事诉讼律师，专门负责电子证据的可采性评估。\
请根据中国民事诉讼法及相关司法解释，评估以下证据的可采性。

## 法律依据
- 《民事诉讼法》第66条（证据种类）
- 《最高人民法院关于民事诉讼证据的若干规定》
- 《最高人民法院关于适用〈中华人民共和国民事诉讼法〉的解释》第116条
- 《电子数据司法鉴定通则》

## 微信聊天记录作为电子证据的关键要点
1. **真实性**: 需证明聊天记录未被篡改（原始载体保全、哈希值校验）
2. **关联性**: 需证明聊天对象的身份（微信号与当事人的对应关系）
3. **合法性**: 取证方式应合法（不得非法侵入他人手机）
4. **完整性**: 聊天记录应完整连贯，不得选择性截取

## 评估要求
对每项证据，请评估：
1. 证据类型归属
2. 真实性、关联性、合法性、完整性评分
3. 存在的可采性风险
4. 补强建议

## 输出格式
```json
{
  "evaluations": [
    {
      "evidence_id": "证据编号",
      "evidence_desc": "证据描述",
      "evidence_type": "电子数据|书证|视听资料",
      "admissibility": "可采|需补强|风险较大",
      "authenticity": "评价",
      "relevance": "评价",
      "legality": "评价",
      "completeness": "评价",
      "risks": ["风险1"],
      "suggestions": ["补强建议1"]
    }
  ]
}
```
"""


# ---------------------------------------------------------------------------
# 核心类
# ---------------------------------------------------------------------------

class LegalAnalyzer:
    """
    法律分析器。

    基于大语言模型对微信聊天证据进行法律分析，支持证据链评估、
    争议焦点识别、可采性评估及诉讼策略建议。

    Usage::

        analyzer = LegalAnalyzer(api_key="sk-...")
        result = analyzer.analyze_evidence_chain(
            facts=[{"fact_type": "金额", "content": "借款10万元"}],
            case_type="民间借贷纠纷",
        )
        print(result.evidence_strength)
    """

    # 支持的案件类型
    SUPPORTED_CASE_TYPES = [
        "民间借贷纠纷",
        "合同纠纷",
        "劳动争议",
        "房屋买卖纠纷",
        "房屋租赁纠纷",
        "婚姻家庭纠纷",
    ]

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: Optional[str] = None,
    ) -> None:
        """
        初始化法律分析器。

        Args:
            api_key: OpenAI API 密钥
            model: 模型名称
            base_url: 自定义 API 端点
        """
        from openai import OpenAI

        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        self._client = OpenAI(**client_kwargs)
        self._model = model

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def analyze_evidence_chain(
        self,
        facts: List[Dict[str, Any]],
        case_type: str,
    ) -> AnalysisResult:
        """
        分析证据链，评估证据强度并给出法律建议。

        Args:
            facts: 事实字典列表（可由 FactExtractor 产出）
            case_type: 案件类型

        Returns:
            AnalysisResult 分析结果
        """
        if case_type not in self.SUPPORTED_CASE_TYPES:
            logger.warning(
                "未知案件类型 '%s'，将尝试通用分析。支持的类型: %s",
                case_type,
                self.SUPPORTED_CASE_TYPES,
            )

        facts_text = self._format_facts(facts)
        user_content = (
            f"## 案件类型\n{case_type}\n\n"
            f"## 已提取的证据事实\n{facts_text}\n\n"
            f"请进行全面的法律分析。"
        )

        raw = self._call_llm(
            system_prompt=_EVIDENCE_CHAIN_SYSTEM_PROMPT,
            user_content=user_content,
            use_json=True,
        )
        return self._parse_analysis_result(raw, case_type)

    def identify_disputes(
        self, messages: List[Dict[str, Any]]
    ) -> List[str]:
        """
        从聊天记录中识别争议焦点。

        Args:
            messages: 消息字典列表

        Returns:
            争议焦点描述列表
        """
        if not messages:
            return []

        formatted = self._format_messages(messages)
        raw = self._call_llm(
            system_prompt=_DISPUTE_IDENTIFICATION_PROMPT,
            user_content=f"以下是微信聊天记录：\n\n{formatted}",
            use_json=True,
        )

        try:
            data = json.loads(raw)
            if isinstance(data, dict) and "disputes" in data:
                return data["disputes"]
            if isinstance(data, list):
                return [str(item) for item in data]
        except json.JSONDecodeError:
            logger.warning("争议焦点识别结果解析失败")
        return []

    def suggest_strategy(self, analysis: AnalysisResult) -> str:
        """
        根据分析结果给出诉讼策略建议。

        Args:
            analysis: 证据链分析结果

        Returns:
            策略建议文本
        """
        analysis_text = json.dumps(
            analysis.to_dict(), ensure_ascii=False, indent=2
        )
        user_content = (
            f"以下是案件的证据分析结果：\n\n{analysis_text}\n\n"
            f"请给出详细的诉讼策略建议。"
        )

        return self._call_llm(
            system_prompt=_STRATEGY_SUGGESTION_PROMPT,
            user_content=user_content,
            use_json=False,
        )

    def evaluate_admissibility(
        self, evidence_items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        评估证据的可采性。

        Args:
            evidence_items: 证据条目列表，每条应包含
                            id, type, description, source 等字段

        Returns:
            评估结果字典列表
        """
        if not evidence_items:
            return []

        evidence_text = json.dumps(
            evidence_items, ensure_ascii=False, indent=2
        )
        user_content = (
            f"以下是待评估的证据清单：\n\n{evidence_text}\n\n"
            f"请逐项评估每项证据在中国民事诉讼中的可采性。"
        )

        raw = self._call_llm(
            system_prompt=_ADMISSIBILITY_EVALUATION_PROMPT,
            user_content=user_content,
            use_json=True,
        )

        try:
            data = json.loads(raw)
            if isinstance(data, dict) and "evaluations" in data:
                return data["evaluations"]
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            logger.warning("证据可采性评估结果解析失败")
        return []

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _call_llm(
        self,
        system_prompt: str,
        user_content: str,
        use_json: bool = True,
    ) -> str:
        """
        调用 LLM 并返回文本响应。

        Args:
            system_prompt: 系统提示词
            user_content: 用户消息
            use_json: 是否要求 JSON 格式输出

        Returns:
            模型响应文本
        """
        kwargs: Dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
        }
        if use_json:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = self._client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception:
            logger.exception("LLM 调用失败")
            return "{}" if use_json else ""

    @staticmethod
    def _format_facts(facts: List[Dict[str, Any]]) -> str:
        """将事实列表格式化为可读文本。"""
        lines: List[str] = []
        for i, fact in enumerate(facts, 1):
            fact_type = fact.get("fact_type", "未分类")
            content = fact.get("content", "")
            importance = fact.get("importance", "medium")
            ts = fact.get("timestamp", "")
            participants = ", ".join(fact.get("participants", []))
            lines.append(
                f"{i}. [{fact_type}][{importance}] {content}"
                f"（时间: {ts}，参与方: {participants}）"
            )
        return "\n".join(lines) if lines else "（无事实记录）"

    @staticmethod
    def _format_messages(messages: List[Dict[str, Any]]) -> str:
        """将消息列表格式化为可读文本。"""
        lines: List[str] = []
        for msg in messages:
            ts = msg.get("timestamp", "")
            sender = msg.get("sender", "未知")
            content = msg.get("content", "")
            lines.append(f"[{ts}] {sender}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _parse_analysis_result(
        raw_response: str, case_type: str
    ) -> AnalysisResult:
        """将 LLM 的 JSON 响应解析为 AnalysisResult。"""
        try:
            data = json.loads(raw_response)
        except json.JSONDecodeError:
            logger.warning("分析结果 JSON 解析失败，返回默认结果")
            return AnalysisResult(case_type=case_type)

        if not isinstance(data, dict):
            return AnalysisResult(case_type=case_type)

        return AnalysisResult(
            case_type=data.get("case_type", case_type),
            key_facts=data.get("key_facts", []),
            evidence_strength=data.get("evidence_strength", "moderate"),
            missing_evidence=data.get("missing_evidence", []),
            legal_basis=data.get("legal_basis", []),
            disputed_points=data.get("disputed_points", []),
            suggestions=data.get("suggestions", []),
        )
