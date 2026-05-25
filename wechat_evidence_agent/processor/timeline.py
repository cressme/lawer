"""
对话时间线重建模块

将解析后的消息列表重建为结构化的对话时间线，支持：
1. 按日期分组消息
2. 按日期范围、消息类型、关键词过滤
3. 统计摘要（类型分布、日期范围、参与者统计）
4. 上下文关联查找
5. 全文搜索
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from .message_parser import MessageType, ParsedMessage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 时间线数据结构
# ---------------------------------------------------------------------------

@dataclass
class DayMessages:
    """单日消息分组。"""
    date: date
    messages: List[ParsedMessage] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.messages)


@dataclass
class Timeline:
    """
    对话时间线。

    将消息按日期分组存储，提供快速按日期访问的能力。

    Attributes:
        days: 按日期排序的每日消息分组列表。
        all_messages: 按时间排序的全部消息列表。
    """
    days: List[DayMessages] = field(default_factory=list)
    all_messages: List[ParsedMessage] = field(default_factory=list)

    @property
    def start_date(self) -> Optional[date]:
        """时间线起始日期。"""
        return self.days[0].date if self.days else None

    @property
    def end_date(self) -> Optional[date]:
        """时间线结束日期。"""
        return self.days[-1].date if self.days else None

    @property
    def total_count(self) -> int:
        """消息总数。"""
        return len(self.all_messages)

    def get_day(self, target_date: date) -> Optional[DayMessages]:
        """获取指定日期的消息分组。"""
        for day in self.days:
            if day.date == target_date:
                return day
        return None


# ---------------------------------------------------------------------------
# 时间线构建器
# ---------------------------------------------------------------------------

class TimelineBuilder:
    """
    对话时间线构建器

    将 ParsedMessage 列表组织为按日期分组的时间线结构，
    并提供过滤、搜索、统计等功能。

    用法示例::

        builder = TimelineBuilder()
        timeline = builder.build_timeline(parsed_messages)
        stats = builder.get_summary_stats(timeline)
        results = builder.search(timeline, keyword="合同")
    """

    def build_timeline(
        self, messages: List[ParsedMessage]
    ) -> Timeline:
        """
        将消息列表构建为时间线。

        消息会按时间戳排序并按日期分组。

        Args:
            messages: 解析后的消息列表。

        Returns:
            构建完成的 Timeline 对象。
        """
        # 按时间戳排序
        sorted_msgs = sorted(messages, key=lambda m: m.timestamp)

        # 按日期分组
        day_map: Dict[date, List[ParsedMessage]] = defaultdict(list)
        for msg in sorted_msgs:
            if msg.timestamp > 0:
                msg_date = datetime.fromtimestamp(msg.timestamp).date()
            else:
                msg_date = date(1970, 1, 1)
            day_map[msg_date].append(msg)

        # 构建有序的 DayMessages 列表
        days = [
            DayMessages(date=d, messages=msgs)
            for d, msgs in sorted(day_map.items())
        ]

        timeline = Timeline(days=days, all_messages=sorted_msgs)
        logger.info(
            "时间线构建完成: %d 条消息, %d 天, 范围 %s ~ %s",
            timeline.total_count,
            len(days),
            timeline.start_date,
            timeline.end_date,
        )
        return timeline

    def filter_timeline(
        self,
        timeline: Timeline,
        *,
        date_range: Optional[Tuple[date, date]] = None,
        msg_types: Optional[List[MessageType]] = None,
        keyword: Optional[str] = None,
        sender: Optional[str] = None,
    ) -> Timeline:
        """
        按条件过滤时间线，返回新的 Timeline。

        所有过滤条件为 AND 关系：同时满足所有指定条件的消息才会保留。

        Args:
            timeline: 原始时间线。
            date_range: 日期范围 (start_date, end_date)，含两端。
            msg_types: 要保留的消息类型列表。
            keyword: 内容关键词 (不区分大小写)。
            sender: 发送者筛选。

        Returns:
            过滤后的新 Timeline 对象。
        """
        filtered: List[ParsedMessage] = []

        for msg in timeline.all_messages:
            # 日期范围过滤
            if date_range:
                if msg.timestamp > 0:
                    msg_date = datetime.fromtimestamp(msg.timestamp).date()
                else:
                    continue
                if msg_date < date_range[0] or msg_date > date_range[1]:
                    continue

            # 消息类型过滤
            if msg_types and msg.msg_type not in msg_types:
                continue

            # 关键词过滤
            if keyword and keyword.lower() not in msg.content.lower():
                continue

            # 发送者过滤
            if sender and msg.sender != sender:
                continue

            filtered.append(msg)

        return self.build_timeline(filtered)

    def search(
        self,
        timeline: Timeline,
        keyword: str,
        *,
        msg_type: Optional[MessageType] = None,
        date_range: Optional[Tuple[date, date]] = None,
    ) -> List[ParsedMessage]:
        """
        在时间线中搜索消息。

        Args:
            timeline: 要搜索的时间线。
            keyword: 搜索关键词 (不区分大小写)。
            msg_type: 可选的消息类型过滤。
            date_range: 可选的日期范围过滤 (start_date, end_date)。

        Returns:
            匹配的消息列表，按时间排序。
        """
        msg_types = [msg_type] if msg_type else None
        filtered = self.filter_timeline(
            timeline,
            date_range=date_range,
            msg_types=msg_types,
            keyword=keyword,
        )
        return filtered.all_messages

    def find_related_messages(
        self,
        timeline: Timeline,
        msg_id: int,
        context_count: int = 5,
    ) -> List[ParsedMessage]:
        """
        查找指定消息的上下文关联消息。

        返回目标消息前后各 context_count 条消息，用于提供对话上下文。

        Args:
            timeline: 时间线对象。
            msg_id: 目标消息的 msg_id (MsgSvrID)。
            context_count: 前后各取多少条消息，默认 5 条。

        Returns:
            包含目标消息及其上下文的消息列表。
            若找不到目标消息则返回空列表。
        """
        messages = timeline.all_messages

        # 查找目标消息的索引
        target_idx: Optional[int] = None
        for i, msg in enumerate(messages):
            if msg.msg_id == msg_id:
                target_idx = i
                break

        if target_idx is None:
            logger.warning("未找到消息 msg_id=%s", msg_id)
            return []

        start = max(0, target_idx - context_count)
        end = min(len(messages), target_idx + context_count + 1)
        return messages[start:end]

    def get_summary_stats(self, timeline: Timeline) -> dict:
        """
        获取时间线的统计摘要。

        返回包含以下信息的字典：
        - total_messages: 消息总数
        - date_range: 日期范围
        - days_count: 覆盖天数
        - type_counts: 各消息类型的数量统计
        - participant_stats: 参与者发送消息数统计
        - daily_counts: 每日消息数量
        - busiest_day: 消息最多的一天
        - avg_daily: 日均消息数

        Args:
            timeline: 时间线对象。

        Returns:
            统计摘要字典。
        """
        if not timeline.all_messages:
            return {
                "total_messages": 0,
                "date_range": None,
                "days_count": 0,
                "type_counts": {},
                "participant_stats": {},
                "daily_counts": {},
                "busiest_day": None,
                "avg_daily": 0.0,
            }

        # 按消息类型统计
        type_counts: Dict[str, int] = defaultdict(int)
        for msg in timeline.all_messages:
            type_counts[msg.msg_type.name] += 1

        # 按参与者统计
        participant_stats: Dict[str, int] = defaultdict(int)
        for msg in timeline.all_messages:
            participant_stats[msg.sender] += 1

        # 每日消息数
        daily_counts: Dict[str, int] = {}
        for day in timeline.days:
            daily_counts[day.date.isoformat()] = day.count

        # 最活跃的一天
        busiest_day = max(timeline.days, key=lambda d: d.count)

        # 日均消息数
        days_count = len(timeline.days)
        avg_daily = timeline.total_count / days_count if days_count > 0 else 0

        return {
            "total_messages": timeline.total_count,
            "date_range": {
                "start": timeline.start_date.isoformat() if timeline.start_date else None,
                "end": timeline.end_date.isoformat() if timeline.end_date else None,
            },
            "days_count": days_count,
            "type_counts": dict(type_counts),
            "participant_stats": dict(participant_stats),
            "daily_counts": daily_counts,
            "busiest_day": {
                "date": busiest_day.date.isoformat(),
                "count": busiest_day.count,
            },
            "avg_daily": round(avg_daily, 1),
        }
