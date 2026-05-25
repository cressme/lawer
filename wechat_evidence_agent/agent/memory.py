"""
案件记忆与上下文管理模块

提供案件信息持久化、证据标记管理、分析结果缓存等功能。
支持将当前工作状态序列化到 JSON 文件，以便跨会话恢复。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class EvidenceMark:
    """单条证据标记。"""
    msg_id: int
    label: str
    note: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class CaseInfo:
    """案件基本信息。"""
    case_type: str = ""
    plaintiff: str = ""
    defendant: str = ""
    claim_amount: str = ""
    court: str = ""
    case_number: str = ""
    key_dates: Dict[str, str] = field(default_factory=dict)
    description: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 案件记忆管理器
# ---------------------------------------------------------------------------

class CaseMemory:
    """
    案件记忆与上下文管理器

    负责在 Agent 会话过程中维护案件状态，包括：
    - 当前案件基本信息（案由、当事人、诉请等）
    - 已标记的关键证据列表
    - 缓存的分析结果

    支持序列化到 JSON 文件以便跨会话恢复工作状态。

    用法示例::

        memory = CaseMemory()
        memory.set_case_info(case_type="民间借贷纠纷", plaintiff="张三", defendant="李四")
        memory.add_evidence_mark(msg_id=12345, label="借款凭证", note="微信转账记录")
        memory.save_to_file("case_state.json")
    """

    def __init__(self) -> None:
        """初始化空的案件记忆。"""
        self._case_info: CaseInfo = CaseInfo()
        self._evidence_marks: List[EvidenceMark] = []
        self._analysis_results: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 案件信息管理
    # ------------------------------------------------------------------

    def set_case_info(
        self,
        case_type: str = "",
        plaintiff: str = "",
        defendant: str = "",
        claim_amount: str = "",
        court: str = "",
        case_number: str = "",
        key_dates: Optional[Dict[str, str]] = None,
        description: str = "",
        **extra: Any,
    ) -> None:
        """
        设置或更新案件基本信息。

        仅更新传入的非空字段，未传入的字段保持原值不变。

        Args:
            case_type: 案由，如"民间借贷纠纷""买卖合同纠纷"等。
            plaintiff: 原告姓名/名称。
            defendant: 被告姓名/名称。
            claim_amount: 诉讼请求金额。
            court: 管辖法院。
            case_number: 案号。
            key_dates: 关键时间节点，如 {"借款日期": "2024-01-15"}。
            description: 案件描述。
            **extra: 其他自定义字段。
        """
        if case_type:
            self._case_info.case_type = case_type
        if plaintiff:
            self._case_info.plaintiff = plaintiff
        if defendant:
            self._case_info.defendant = defendant
        if claim_amount:
            self._case_info.claim_amount = claim_amount
        if court:
            self._case_info.court = court
        if case_number:
            self._case_info.case_number = case_number
        if key_dates:
            self._case_info.key_dates.update(key_dates)
        if description:
            self._case_info.description = description
        if extra:
            self._case_info.extra.update(extra)

        logger.info("案件信息已更新: %s", self._case_info.case_type)

    def get_case_info(self) -> CaseInfo:
        """获取当前案件信息对象。"""
        return self._case_info

    def get_case_summary(self) -> str:
        """
        生成当前案件状态的格式化摘要。

        Returns:
            中文格式的案件摘要文本，包含案件信息、证据标记统计等。
        """
        ci = self._case_info
        lines: List[str] = ["【案件概况】"]

        if ci.case_type:
            lines.append(f"  案由：{ci.case_type}")
        if ci.case_number:
            lines.append(f"  案号：{ci.case_number}")
        if ci.plaintiff:
            lines.append(f"  原告：{ci.plaintiff}")
        if ci.defendant:
            lines.append(f"  被告：{ci.defendant}")
        if ci.claim_amount:
            lines.append(f"  诉请金额：{ci.claim_amount}")
        if ci.court:
            lines.append(f"  管辖法院：{ci.court}")
        if ci.description:
            lines.append(f"  案情摘要：{ci.description}")

        if ci.key_dates:
            lines.append("  关键时间节点：")
            for event, date_str in ci.key_dates.items():
                lines.append(f"    - {event}：{date_str}")

        # 证据标记统计
        if self._evidence_marks:
            lines.append(f"\n【证据标记】共 {len(self._evidence_marks)} 条")
            label_counts: Dict[str, int] = {}
            for mark in self._evidence_marks:
                label_counts[mark.label] = label_counts.get(mark.label, 0) + 1
            for label, count in label_counts.items():
                lines.append(f"  - {label}：{count} 条")

        # 分析结果概要
        if self._analysis_results:
            lines.append(f"\n【分析结果】已缓存 {len(self._analysis_results)} 项分析")
            for key in self._analysis_results:
                lines.append(f"  - {key}")

        if len(lines) == 1:
            lines.append("  （尚未设置案件信息）")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 证据标记管理
    # ------------------------------------------------------------------

    def add_evidence_mark(
        self,
        msg_id: int,
        label: str,
        note: str = "",
    ) -> EvidenceMark:
        """
        标记一条消息为关键证据。

        Args:
            msg_id: 消息 ID（MsgSvrID）。
            label: 证据标签，如"借款凭证""还款承诺""违约事实"等。
            note: 备注说明。

        Returns:
            创建的 EvidenceMark 对象。
        """
        mark = EvidenceMark(msg_id=msg_id, label=label, note=note)
        self._evidence_marks.append(mark)
        logger.info("添加证据标记: msg_id=%d, label=%s", msg_id, label)
        return mark

    def remove_evidence_mark(self, msg_id: int) -> bool:
        """
        移除指定消息的证据标记。

        Args:
            msg_id: 消息 ID。

        Returns:
            是否成功移除（消息未被标记时返回 False）。
        """
        before = len(self._evidence_marks)
        self._evidence_marks = [
            m for m in self._evidence_marks if m.msg_id != msg_id
        ]
        removed = len(self._evidence_marks) < before
        if removed:
            logger.info("移除证据标记: msg_id=%d", msg_id)
        return removed

    def get_evidence_marks(
        self,
        label: Optional[str] = None,
    ) -> List[EvidenceMark]:
        """
        获取证据标记列表。

        Args:
            label: 可选的标签筛选，为 None 时返回全部。

        Returns:
            证据标记列表。
        """
        if label is None:
            return list(self._evidence_marks)
        return [m for m in self._evidence_marks if m.label == label]

    # ------------------------------------------------------------------
    # 分析结果缓存
    # ------------------------------------------------------------------

    def cache_analysis(self, key: str, result: Any) -> None:
        """
        缓存分析结果。

        Args:
            key: 分析项标识，如"借贷关系分析""时间线分析"。
            result: 分析结果数据（可序列化对象）。
        """
        self._analysis_results[key] = {
            "result": result,
            "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        logger.info("缓存分析结果: %s", key)

    def get_analysis(self, key: str) -> Optional[Any]:
        """
        获取缓存的分析结果。

        Args:
            key: 分析项标识。

        Returns:
            分析结果数据，未缓存时返回 None。
        """
        cached = self._analysis_results.get(key)
        if cached:
            return cached["result"]
        return None

    def clear_analysis_cache(self) -> None:
        """清除所有缓存的分析结果。"""
        self._analysis_results.clear()
        logger.info("已清除分析结果缓存")

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def save_to_file(self, path: str | Path) -> None:
        """
        将当前案件记忆序列化保存到 JSON 文件。

        Args:
            path: 目标文件路径。

        Raises:
            OSError: 文件写入失败。
        """
        path = Path(path)
        data = {
            "case_info": asdict(self._case_info),
            "evidence_marks": [asdict(m) for m in self._evidence_marks],
            "analysis_results": self._analysis_results,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("案件记忆已保存至: %s", path)

    def load_from_file(self, path: str | Path) -> None:
        """
        从 JSON 文件加载案件记忆。

        Args:
            path: 源文件路径。

        Raises:
            FileNotFoundError: 文件不存在。
            json.JSONDecodeError: 文件格式错误。
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"案件记忆文件不存在: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 恢复案件信息
        ci_data = data.get("case_info", {})
        self._case_info = CaseInfo(
            case_type=ci_data.get("case_type", ""),
            plaintiff=ci_data.get("plaintiff", ""),
            defendant=ci_data.get("defendant", ""),
            claim_amount=ci_data.get("claim_amount", ""),
            court=ci_data.get("court", ""),
            case_number=ci_data.get("case_number", ""),
            key_dates=ci_data.get("key_dates", {}),
            description=ci_data.get("description", ""),
            extra=ci_data.get("extra", {}),
        )

        # 恢复证据标记
        self._evidence_marks = [
            EvidenceMark(
                msg_id=m["msg_id"],
                label=m["label"],
                note=m.get("note", ""),
                created_at=m.get("created_at", ""),
            )
            for m in data.get("evidence_marks", [])
        ]

        # 恢复分析结果缓存
        self._analysis_results = data.get("analysis_results", {})

        logger.info(
            "案件记忆已加载: %s (证据标记 %d 条, 分析缓存 %d 项)",
            path, len(self._evidence_marks), len(self._analysis_results),
        )

    def reset(self) -> None:
        """重置所有案件记忆。"""
        self._case_info = CaseInfo()
        self._evidence_marks.clear()
        self._analysis_results.clear()
        logger.info("案件记忆已重置")

    def __repr__(self) -> str:
        return (
            f"<CaseMemory case_type={self._case_info.case_type!r} "
            f"marks={len(self._evidence_marks)} "
            f"analyses={len(self._analysis_results)}>"
        )
