"""
关键事实提取模块

利用大语言模型从微信聊天记录中提取具有法律意义的关键事实，包括：
- 金额信息（借款金额、转账金额、约定价格等）
- 承诺与约定（还款承诺、交付期限、服务约定等）
- 违约行为（逾期、拒绝履行、质量瑕疵等）
- 催告记录（催款、催交付、履行通知等）
- 确认与自认（对事实的确认、对债务的承认等）
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
class Fact:
    """从聊天记录中提取的单条法律事实。"""

    timestamp: str
    """事实对应的时间戳（ISO 格式或原始消息时间）"""

    fact_type: str
    """
    事实类型，取值范围：
    - 承诺: 一方做出的承诺或保证
    - 金额: 涉及具体金额的内容
    - 日期约定: 约定的时间节点或期限
    - 违约行为: 违反约定的行为
    - 催告: 一方对另一方的催促或通知
    - 确认: 对事实或义务的确认/自认
    - 其他: 其他具有法律意义的事实
    """

    content: str
    """事实内容的简明描述"""

    participants: List[str] = field(default_factory=list)
    """涉及的参与者"""

    importance: str = "medium"
    """重要程度: high / medium / low"""

    source_msg_ids: List[str] = field(default_factory=list)
    """来源消息的 ID 列表，用于溯源"""

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典。"""
        return asdict(self)


# ---------------------------------------------------------------------------
# 系统提示词
# ---------------------------------------------------------------------------

_FACT_EXTRACTION_SYSTEM_PROMPT = """\
你是一名资深中国民事诉讼律师助理，专门从微信聊天记录中提取具有法律证据价值的关键事实。

## 你的任务
仔细阅读以下微信聊天记录，提取所有具有法律意义的事实。

## 提取重点
请重点关注以下类型的信息：

1. **金额 (金额)**: 任何涉及具体金额的内容——借款金额、转账金额、约定价格、赔偿金额、\
工资薪酬等。注意区分人民币与其他货币。
2. **承诺 (承诺)**: 一方向另一方做出的承诺或保证——还款承诺、交付承诺、质量保证、\
服务承诺等。注意承诺的具体内容和条件。
3. **日期约定 (日期约定)**: 双方约定的时间节点——还款日期、交付期限、合同履行期限、\
试用期期限等。
4. **违约行为 (违约行为)**: 任何违反约定的行为——逾期未还款、拒绝履行、质量不符、\
擅自变更条件等。
5. **催告 (催告)**: 一方对另一方的催促——催款、催交付、催履行、警告、最后通牒等。\
催告记录在诉讼中具有重要证据价值。
6. **确认 (确认)**: 对事实的确认或自认——承认欠款、确认收货、认可合同条款、\
承认违约等。自认在民事诉讼中构成免证事实。
7. **其他 (其他)**: 其他可能具有法律意义的内容——威胁、恐吓、欺诈表述、\
重要背景信息等。

## 重要程度判断标准
- **high**: 直接关系到案件核心争议焦点的事实（如借款金额确认、还款承诺、违约自认）
- **medium**: 对案件有辅助证明作用的事实（如催告记录、背景约定）
- **low**: 可能有参考价值但非关键的事实（如一般性对话中透露的信息）

## 输出格式
请以 JSON 数组格式输出，每条事实包含以下字段：
```json
[
  {
    "timestamp": "消息对应的时间",
    "fact_type": "承诺|金额|日期约定|违约行为|催告|确认|其他",
    "content": "事实内容的简明描述",
    "participants": ["参与者1", "参与者2"],
    "importance": "high|medium|low",
    "source_msg_ids": ["消息ID"]
  }
]
```

## 注意事项
- 保持客观中立，如实提取事实，不添加主观推测
- content 字段应简明扼要但完整准确地描述事实
- 如果同一事实涉及多条消息，合并为一条事实并列出所有相关消息 ID
- 特别注意语音转文字内容，其证据效力可能与文字消息不同
- 注意表情包、红包、转账等特殊消息类型的法律意义
"""

_AMOUNT_EXTRACTION_SYSTEM_PROMPT = """\
你是一名专业的法律财务分析助理。请从以下微信聊天记录中提取所有涉及金额的信息。

## 提取要求
1. 提取所有明确提到的金额数字（包括大写数字、阿拉伯数字、口语化表达如"5万"）
2. 记录金额的上下文——是借款、还款、转账、约定价格还是其他
3. 记录金额提及的时间和发言人
4. 注意区分人民币和其他货币
5. 注意微信红包和转账消息中的金额

## 输出格式
```json
[
  {
    "amount": "金额数值（统一为数字，单位为元）",
    "currency": "CNY",
    "context": "该金额出现的上下文描述",
    "speaker": "提及该金额的发言人",
    "timestamp": "时间",
    "msg_id": "消息ID",
    "nature": "借款|还款|转账|约定价格|赔偿|工资|其他"
  }
]
```
"""

_TIMELINE_EXTRACTION_SYSTEM_PROMPT = """\
你是一名专业的法律事务时间线整理助理。请从以下微信聊天记录中提取关键事件，\
按时间顺序整理为事件时间线。

## 提取要求
1. 识别所有具有法律意义的关键事件节点
2. 按时间先后顺序排列
3. 每个事件需注明发生时间、事件内容、相关方
4. 特别关注：合同订立、款项支付、交付行为、违约发生、催告送达、协商过程等

## 输出格式
```json
[
  {
    "timestamp": "事件时间",
    "event": "事件简述",
    "participants": ["参与方"],
    "significance": "该事件的法律意义",
    "msg_ids": ["相关消息ID"]
  }
]
```
"""


# ---------------------------------------------------------------------------
# 核心类
# ---------------------------------------------------------------------------

class FactExtractor:
    """
    关键事实提取器。

    利用大语言模型从微信聊天消息中自动提取具有法律证据价值的关键事实。

    Usage::

        extractor = FactExtractor(api_key="sk-...")
        facts = extractor.extract_facts(messages)
        for fact in facts:
            print(f"[{fact.importance}] {fact.fact_type}: {fact.content}")
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: Optional[str] = None,
    ) -> None:
        """
        初始化事实提取器。

        Args:
            api_key: OpenAI API 密钥
            model: 模型名称
            base_url: 自定义 API 端点
        """
        # 延迟导入，仅在实例化时检查依赖
        from openai import OpenAI

        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        self._client = OpenAI(**client_kwargs)
        self._model = model

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def extract_facts(self, messages: List[Dict[str, Any]]) -> List[Fact]:
        """
        从聊天消息列表中提取关键法律事实。

        Args:
            messages: 消息字典列表，每条消息应包含
                      id, timestamp, sender, content 等字段

        Returns:
            提取到的 Fact 对象列表
        """
        if not messages:
            return []

        formatted = self._format_messages(messages)
        raw_facts = self._call_llm(
            system_prompt=_FACT_EXTRACTION_SYSTEM_PROMPT,
            user_content=f"以下是微信聊天记录，请提取关键法律事实：\n\n{formatted}",
        )
        return self._parse_facts(raw_facts)

    def extract_key_amounts(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        提取聊天记录中所有涉及的金额信息。

        Args:
            messages: 消息字典列表

        Returns:
            金额信息字典列表，每条包含 amount, currency, context,
            speaker, timestamp, msg_id, nature 字段
        """
        if not messages:
            return []

        formatted = self._format_messages(messages)
        raw = self._call_llm(
            system_prompt=_AMOUNT_EXTRACTION_SYSTEM_PROMPT,
            user_content=f"以下是微信聊天记录，请提取所有金额信息：\n\n{formatted}",
        )
        return self._parse_json_list(raw)

    def extract_timeline_events(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        从聊天记录中提取关键事件时间线。

        Args:
            messages: 消息字典列表

        Returns:
            事件字典列表，按时间排序，每条包含 timestamp, event,
            participants, significance, msg_ids 字段
        """
        if not messages:
            return []

        formatted = self._format_messages(messages)
        raw = self._call_llm(
            system_prompt=_TIMELINE_EXTRACTION_SYSTEM_PROMPT,
            user_content=f"以下是微信聊天记录，请整理关键事件时间线：\n\n{formatted}",
        )
        return self._parse_json_list(raw)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _call_llm(self, system_prompt: str, user_content: str) -> str:
        """
        调用 LLM 并返回文本响应。

        Args:
            system_prompt: 系统提示词
            user_content: 用户消息内容

        Returns:
            模型响应文本
        """
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content or "[]"
        except Exception:
            logger.exception("LLM 调用失败")
            return "[]"

    @staticmethod
    def _format_messages(messages: List[Dict[str, Any]]) -> str:
        """
        将消息列表格式化为可读文本，供 LLM 分析。

        Args:
            messages: 原始消息字典列表

        Returns:
            格式化后的文本
        """
        lines: List[str] = []
        for msg in messages:
            msg_id = msg.get("id", "")
            ts = msg.get("timestamp", "")
            sender = msg.get("sender", "未知")
            content = msg.get("content", "")
            msg_type = msg.get("type", "text")

            prefix = f"[{ts}] {sender}"
            if msg_id:
                prefix = f"[ID:{msg_id}] {prefix}"

            if msg_type == "text":
                lines.append(f"{prefix}: {content}")
            elif msg_type in ("image", "video"):
                lines.append(f"{prefix}: [{msg_type.upper()}] {content}")
            elif msg_type == "voice":
                text = msg.get("voice_text", content)
                lines.append(f"{prefix}: [语音转文字] {text}")
            elif msg_type == "transfer":
                amount = msg.get("amount", content)
                lines.append(f"{prefix}: [微信转账] ¥{amount}")
            elif msg_type == "red_packet":
                lines.append(f"{prefix}: [微信红包] {content}")
            else:
                lines.append(f"{prefix}: [{msg_type}] {content}")

        return "\n".join(lines)

    @staticmethod
    def _parse_facts(raw_response: str) -> List[Fact]:
        """
        将 LLM 的 JSON 响应解析为 Fact 列表。

        Args:
            raw_response: LLM 返回的 JSON 字符串

        Returns:
            Fact 对象列表
        """
        items = FactExtractor._parse_json_list(raw_response)
        facts: List[Fact] = []
        for item in items:
            try:
                fact = Fact(
                    timestamp=str(item.get("timestamp", "")),
                    fact_type=item.get("fact_type", "其他"),
                    content=item.get("content", ""),
                    participants=item.get("participants", []),
                    importance=item.get("importance", "medium"),
                    source_msg_ids=[
                        str(mid) for mid in item.get("source_msg_ids", [])
                    ],
                )
                facts.append(fact)
            except Exception:
                logger.warning("跳过无法解析的事实条目: %s", item)
        return facts

    @staticmethod
    def _parse_json_list(raw_response: str) -> List[Dict[str, Any]]:
        """
        从 LLM 响应中解析 JSON 列表。

        兼容两种格式：直接数组 ``[...]`` 或包含数组字段的对象
        ``{"items": [...]}``。

        Args:
            raw_response: JSON 字符串

        Returns:
            字典列表
        """
        try:
            data = json.loads(raw_response)
        except json.JSONDecodeError:
            logger.warning("LLM 返回的 JSON 解析失败: %.200s", raw_response)
            return []

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # 尝试常见的包装键
            for key in ("items", "facts", "amounts", "events", "data", "results"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            # 返回单元素列表
            return [data]
        return []
