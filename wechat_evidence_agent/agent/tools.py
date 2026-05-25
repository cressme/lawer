"""
Agent 工具定义与执行模块

定义 Agent 可调用的工具函数（OpenAI function calling 格式），
以及负责将工具调用分发到底层模块的 ToolExecutor。
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_WECHAT_KEY_REQUIRED_MESSAGE = (
    "已识别到新版微信数据目录，但当前版本还不能稳定自动提取新版微信数据库密钥。"
    "为避免软件卡住，已暂停直接读取联系人/聊天。请先配置有效的 WECHAT_DB_KEY，"
    "或等待完成新版微信解密适配后再读取。"
)


@dataclass
class WorkspaceState:
    """Current agent working set shared across tool calls."""

    contact: str = ""
    contact_id: str = ""
    message_count: int = 0
    start_time: str = ""
    end_time: str = ""
    type_counts: Dict[str, int] = field(default_factory=dict)
    attachment_counts: Dict[str, int] = field(default_factory=dict)
    attachment_examples: List[Dict[str, Any]] = field(default_factory=list)
    preview: List[str] = field(default_factory=list)
    updated_at: str = ""

    def reset(self) -> None:
        self.contact = ""
        self.contact_id = ""
        self.message_count = 0
        self.start_time = ""
        self.end_time = ""
        self.type_counts.clear()
        self.attachment_counts.clear()
        self.attachment_examples.clear()
        self.preview.clear()
        self.updated_at = ""

    def to_prompt_context(self) -> str:
        if not self.contact:
            return "当前工作区：尚未提取聊天材料。"

        type_summary = "，".join(
            f"{name} {count} 条" for name, count in self.type_counts.items()
        ) or "无"
        attachment_summary = "，".join(
            f"{name} {count} 个" for name, count in self.attachment_counts.items()
        ) or "无"

        lines = [
            "当前工作区：",
            f"- 联系人：{self.contact}",
            f"- 联系人ID：{self.contact_id}",
            f"- 消息总数：{self.message_count} 条",
        ]
        if self.start_time or self.end_time:
            lines.append(f"- 时间范围：{self.start_time or '未知'} 至 {self.end_time or '未知'}")
        lines.append(f"- 消息类型：{type_summary}")
        lines.append(f"- 本地附件：{attachment_summary}")
        if self.attachment_examples:
            lines.append("- 附件样例：")
            for item in self.attachment_examples[:5]:
                lines.append(f"  - {item.get('type', '附件')} {item.get('name', '')} {item.get('path', '')}")
        if self.preview:
            lines.append("- 聊天预览：")
            lines.extend(f"  {line}" for line in self.preview[:5])
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# OpenAI function calling 工具定义
# ---------------------------------------------------------------------------

OPENAI_TOOLS: List[Dict[str, Any]] = [
    # placeholder - filled in sections below
]

# 清空占位，逐个定义后追加
OPENAI_TOOLS.clear()

OPENAI_TOOLS.append({
    "type": "function",
    "function": {
        "name": "extract_chat",
        "description": "提取指定联系人的聊天记录",
        "parameters": {
            "type": "object",
            "properties": {
                "contact": {
                    "type": "string",
                    "description": "联系人名称、备注或微信ID。用户要求查看、提取、分析某人的聊天时，应直接调用本工具。",
                },
                "start_date": {
                    "type": "string",
                    "description": "起始日期，格式 YYYY-MM-DD，可选",
                },
                "end_date": {
                    "type": "string",
                    "description": "结束日期，格式 YYYY-MM-DD，可选",
                },
            },
            "required": ["contact"],
        },
    },
})

OPENAI_TOOLS.append({
    "type": "function",
    "function": {
        "name": "search_messages",
        "description": "搜索关键消息",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "contact": {
                    "type": "string",
                    "description": "限定联系人范围，可选",
                },
                "msg_type": {
                    "type": "string",
                    "description": "消息类型筛选（text/image/voice/video/transfer/file），可选",
                },
            },
            "required": ["keyword"],
        },
    },
})

OPENAI_TOOLS.append({
    "type": "function",
    "function": {
        "name": "generate_evidence_list",
        "description": "生成证据清单文档",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "证据清单标题",
                },
                "case_type": {
                    "type": "string",
                    "description": "案由类型，如'民间借贷纠纷'",
                },
                "evidence_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "要纳入清单的证据消息ID列表，可选",
                },
            },
            "required": ["title", "case_type"],
        },
    },
})

OPENAI_TOOLS.append({
    "type": "function",
    "function": {
        "name": "analyze_case",
        "description": "分析案件要点和证据链",
        "parameters": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": "分析重点，如'借款''合同违约'等，可选",
                },
            },
            "required": [],
        },
    },
})

OPENAI_TOOLS.append({
    "type": "function",
    "function": {
        "name": "export_document",
        "description": "导出文档",
        "parameters": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["docx", "pdf", "html"],
                    "description": "导出格式",
                },
                "filename": {
                    "type": "string",
                    "description": "文件名（不含扩展名），可选",
                },
            },
            "required": ["format"],
        },
    },
})

OPENAI_TOOLS.append({
    "type": "function",
    "function": {
        "name": "list_contacts",
        "description": "列出或搜索联系人",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词，可选。不提供则列出所有联系人",
                },
            },
            "required": [],
        },
    },
})

OPENAI_TOOLS.append({
    "type": "function",
    "function": {
        "name": "set_wechat_dir",
        "description": "设置微信本地数据目录。用户给出 WeChat Files 或 xwechat_files 路径时调用此工具。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "微信数据目录路径，例如 C:\\Users\\name\\Documents\\xwechat_files",
                },
            },
            "required": ["path"],
        },
    },
})

OPENAI_TOOLS.append({
    "type": "function",
    "function": {
        "name": "mark_evidence",
        "description": "标记消息为关键证据",
        "parameters": {
            "type": "object",
            "properties": {
                "msg_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "要标记的消息ID列表",
                },
                "label": {
                    "type": "string",
                    "description": "证据标签，如'借款凭证''还款承诺''违约事实'",
                },
            },
            "required": ["msg_ids", "label"],
        },
    },
})

OPENAI_TOOLS.append({
    "type": "function",
    "function": {
        "name": "get_timeline",
        "description": "获取事件时间线",
        "parameters": {
            "type": "object",
            "properties": {
                "contact": {
                    "type": "string",
                    "description": "联系人名称或ID，可选。不提供则获取全局时间线",
                },
            },
            "required": [],
        },
    },
})

OPENAI_TOOLS.append({
    "type": "function",
    "function": {
        "name": "summarize_chat",
        "description": "概要总结聊天内容",
        "parameters": {
            "type": "object",
            "properties": {
                "contact": {
                    "type": "string",
                    "description": "联系人名称或ID",
                },
                "topic": {
                    "type": "string",
                    "description": "聚焦话题，如'借款相关''合同条款讨论'，可选",
                },
            },
            "required": ["contact"],
        },
    },
})

OPENAI_TOOLS.append({
    "type": "function",
    "function": {
        "name": "get_workspace",
        "description": "查看当前已提取的办案工作区状态，包括当前联系人、聊天数量、附件统计和预览。用户说继续分析、刚才那段、当前材料时可先调用。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
})


# ---------------------------------------------------------------------------
# 工具执行器
# ---------------------------------------------------------------------------

class ToolExecutor:
    """
    工具调用执行器

    接收 Agent 的工具调用请求，分发到对应的底层模块执行，
    并将执行结果格式化为中文文本返回给 Agent。

    需要注入底层模块实例（extractor、processor 等）。
    未注入的模块对应的工具调用将返回友好的错误提示。

    用法示例::

        executor = ToolExecutor(
            extractor=wechat_extractor,
            processor=message_processor,
        )
        result = executor.execute("list_contacts", {"keyword": "张三"})
    """

    def __init__(
        self,
        extractor: Any = None,
        processor: Any = None,
        evidence_generator: Any = None,
        analyzer: Any = None,
        memory: Any = None,
    ) -> None:
        """
        初始化工具执行器。

        Args:
            extractor: 数据提取层实例（WeChatDBExtractor）。
            processor: 数据处理层实例（包含 MessageParser、TimelineBuilder 等）。
            evidence_generator: 证据生成器实例。
            analyzer: 案件分析器实例。
            memory: 案件记忆管理器实例（CaseMemory）。
        """
        self.extractor = extractor
        self.processor = processor
        self.evidence_generator = evidence_generator
        self.analyzer = analyzer
        self.memory = memory
        self.workspace = WorkspaceState()

        # 工具名到处理方法的映射
        self._handlers: Dict[str, Any] = {
            "extract_chat": self._execute_extract_chat,
            "search_messages": self._execute_search_messages,
            "generate_evidence_list": self._execute_generate_evidence_list,
            "analyze_case": self._execute_analyze_case,
            "export_document": self._execute_export_document,
            "list_contacts": self._execute_list_contacts,
            "set_wechat_dir": self._execute_set_wechat_dir,
            "mark_evidence": self._execute_mark_evidence,
            "get_timeline": self._execute_get_timeline,
            "summarize_chat": self._execute_summarize_chat,
            "get_workspace": self._execute_get_workspace,
        }

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        执行工具调用。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数字典。

        Returns:
            格式化的中文结果文本。
        """
        handler = self._handlers.get(tool_name)
        if handler is None:
            return f"错误：未知的工具 '{tool_name}'。可用工具：{', '.join(self._handlers.keys())}"

        try:
            logger.info("执行工具: %s, 参数: %s", tool_name, arguments)
            result = handler(arguments)
            logger.info("工具 %s 执行完成", tool_name)
            return result
        except Exception as e:
            error_msg = f"工具 '{tool_name}' 执行出错：{e}"
            logger.error(error_msg, exc_info=True)
            return error_msg

    def get_planner_context(self) -> str:
        """Return compact runtime context for the LLM planner."""
        lines = ["运行时状态："]
        wechat_dir = getattr(self.extractor, "_wechat_dir", None) if self.extractor else None
        if wechat_dir:
            lines.append(f"- 微信数据目录：已配置（{wechat_dir}）")
            lines.append("- 用户要求查本地聊天、提取聊天或分析某联系人时，可直接调用 extract_chat。")
        else:
            lines.append("- 微信数据目录：未显式配置；工具会尝试自动查找默认目录。")
        lines.append(self.workspace.to_prompt_context())
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 各工具的执行实现
    # ------------------------------------------------------------------

    def _execute_extract_chat(self, args: Dict[str, Any]) -> str:
        """提取聊天记录。"""
        if not self.extractor:
            return "错误：数据提取模块未初始化。请先配置微信数据源。"

        contact = self._normalize_contact_name(args["contact"])
        start_date = args.get("start_date")
        end_date = args.get("end_date")

        # 先通过联系人管理器解析联系人ID
        contact_id = self._resolve_contact_id(contact)
        if contact_id is None:
            return f'未找到联系人"{contact}"。请检查名称是否正确，或使用 list_contacts 工具查看联系人列表。'

        messages = self.extractor.get_messages(
            contact_id=contact_id,
            start_date=start_date,
            end_date=end_date,
        )

        if not messages:
            date_hint = ""
            if start_date or end_date:
                date_hint = f"（时间范围：{start_date or '不限'} 至 {end_date or '不限'}）"
            return f'未找到与"{contact}"的聊天记录{date_hint}。'

        self._update_workspace(contact, contact_id, messages)
        return self._format_chat_extraction_report(contact, messages, start_date, end_date)

    def _update_workspace(self, contact: str, contact_id: str, messages: List[dict]) -> None:
        type_names = {
            1: "文字",
            3: "图片",
            34: "语音",
            43: "视频",
            49: "文件/链接",
            50: "通话",
            10000: "系统",
        }
        type_counts: Counter[str] = Counter()
        attachment_counts: Counter[str] = Counter()
        attachments: List[dict] = []
        times = [msg.get("CreateTimeStr") for msg in messages if msg.get("CreateTimeStr")]

        for msg in messages:
            msg_type = self._message_type(msg)
            type_counts[type_names.get(msg_type, f"其他({msg_type})")] += 1
            for item in msg.get("Attachments") or []:
                attachments.append(item)
                attachment_counts[self._attachment_type_label(item.get("type"))] += 1

        self.workspace.contact = contact
        self.workspace.contact_id = contact_id
        self.workspace.message_count = len(messages)
        self.workspace.start_time = times[0] if times else ""
        self.workspace.end_time = times[-1] if times else ""
        self.workspace.type_counts = dict(type_counts)
        self.workspace.attachment_counts = dict(attachment_counts)
        self.workspace.attachment_examples = [
            {
                "type": self._attachment_type_label(item.get("type")),
                "name": item.get("name") or os.path.basename(str(item.get("path") or "")),
                "path": item.get("path") or "",
                "size": item.get("size") or 0,
            }
            for item in attachments[:10]
        ]
        self.workspace.preview = self._format_message_preview(messages, limit=8)
        self.workspace.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _format_chat_extraction_report(
        self,
        contact: str,
        messages: List[dict],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> str:
        """Format extracted chat as a lawyer-facing evidence intake report."""
        type_names = {
            1: "文字",
            3: "图片",
            34: "语音",
            43: "视频",
            49: "文件/链接",
            50: "通话",
            10000: "系统",
        }
        type_counts: Counter[str] = Counter()
        attachment_counts: Counter[str] = Counter()
        attachments: List[dict] = []
        times = [msg.get("CreateTimeStr") for msg in messages if msg.get("CreateTimeStr")]

        for msg in messages:
            msg_type = self._message_type(msg)
            type_counts[type_names.get(msg_type, f"其他({msg_type})")] += 1
            for item in msg.get("Attachments") or []:
                attachments.append(item)
                attachment_counts[item.get("type") or "附件"] += 1

        lines: List[str] = [
            f'已读取与"{contact}"的本地聊天记录。',
            "",
            "证据材料概览",
            f"- 消息总数：{len(messages)} 条",
        ]
        if times:
            lines.append(f"- 时间范围：{times[0]} 至 {times[-1]}")
        if start_date or end_date:
            lines.append(f"- 本次筛选：{start_date or '不限'} 至 {end_date or '不限'}")

        ordered_types = ["文字", "图片", "视频", "文件/链接", "语音", "通话", "系统"]
        type_summary = [f"{name} {type_counts[name]} 条" for name in ordered_types if type_counts.get(name)]
        type_summary.extend(
            f"{name} {count} 条"
            for name, count in type_counts.items()
            if name not in ordered_types
        )
        if type_summary:
            lines.append(f"- 消息类型：{'，'.join(type_summary)}")

        if attachments:
            attachment_summary = "，".join(
                f"{self._attachment_type_label(kind)} {count} 个"
                for kind, count in attachment_counts.items()
            )
            lines.append(f"- 已定位本地附件：{len(attachments)} 个（{attachment_summary}）")
        else:
            lines.append("- 已定位本地附件：0 个")

        voice_count = type_counts.get("语音", 0)
        if voice_count:
            lines.append(f"- 语音消息：{voice_count} 条，当前版本先记录数量和位置，暂不做语音转写。")

        file_link_count = type_counts.get("文件/链接", 0)
        if file_link_count and not any((a.get("type") or "").startswith("file") for a in attachments):
            lines.append("- 文件/链接类消息中可能包含小程序、网页卡片或未缓存实体文件，后续会单独列入待核验材料。")

        if attachments:
            lines.extend(["", "附件样例"])
            for item in attachments[:8]:
                lines.append(
                    f"- {self._attachment_type_label(item.get('type'))}：{item.get('name') or os.path.basename(str(item.get('path') or ''))} "
                    f"({self._format_size(item.get('size'))})"
                )
                if item.get("path"):
                    lines.append(f"  {item['path']}")
            if len(attachments) > 8:
                lines.append(f"- 另有 {len(attachments) - 8} 个附件未在此处展开。")

        lines.extend(["", "聊天预览"])
        lines.extend(self._format_message_preview(messages, limit=15))
        if len(messages) > 15:
            lines.append(f"- 其余 {len(messages) - 15} 条未在窗口展开，后续分析或导出时会重新读取完整记录。")

        lines.extend(
            [
                "",
                "下一步可以直接说：",
                "- 分析这段聊天的证据链",
                "- 按借款/还款/承诺/催告等关键词筛选",
                "- 导出证据包并带上图片、视频、文件清单",
            ]
        )
        return "\n".join(lines)

    def _format_message_preview(self, messages: List[dict], limit: int = 15) -> List[str]:
        lines: List[str] = []
        for msg in messages[:limit]:
            time_str = msg.get("CreateTimeStr", "未知时间")
            sender_tag = "我" if msg.get("IsSender") else "对方"
            content = (msg.get("StrContent") or "").replace("\n", " ").strip()
            if not content:
                content = self._message_type_label(self._message_type(msg))
            if len(content) > 120:
                content = f"{content[:120]}..."
            attachments = self._format_attachments(msg.get("Attachments") or [])
            if attachments:
                content = f"{content} {attachments}"
            lines.append(f"- [{time_str}] {sender_tag}：{content}")
        return lines

    @staticmethod
    def _message_type(msg: dict) -> int:
        try:
            return int(msg.get("Type") or msg.get("type") or 0) & 0xFFFFFFFF
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _message_type_label(msg_type: int) -> str:
        return {
            1: "[文字]",
            3: "[图片]",
            34: "[语音]",
            43: "[视频]",
            49: "[文件/链接]",
            50: "[通话]",
            10000: "[系统消息]",
        }.get(msg_type, "[非文本消息]")

    @staticmethod
    def _attachment_type_label(kind: Optional[str]) -> str:
        return {
            "image": "图片",
            "thumbnail": "缩略图",
            "video": "视频",
            "file": "文件",
            "attachment": "附件",
        }.get(kind or "attachment", kind or "附件")

    @staticmethod
    def _format_size(value: Any) -> str:
        size = int(value or 0)
        if size >= 1024 * 1024:
            return f"{size / 1024 / 1024:.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"

    @staticmethod
    def _format_attachments(attachments: List[dict]) -> str:
        if not attachments:
            return ""
        parts: List[str] = []
        for item in attachments[:3]:
            size_text = ToolExecutor._format_size(item.get("size"))
            label = ToolExecutor._attachment_type_label(item.get("type"))
            parts.append(f"{label}:{item.get('path')} ({size_text})")
        more = f"，另有 {len(attachments) - 3} 个" if len(attachments) > 3 else ""
        return f"附件[{'; '.join(parts)}{more}]"

    def _execute_search_messages(self, args: Dict[str, Any]) -> str:
        """搜索消息。"""
        if not self.extractor:
            return "错误：数据提取模块未初始化。请先配置微信数据源。"

        keyword = args["keyword"]
        contact = args.get("contact") or self.workspace.contact
        msg_type = args.get("msg_type")

        # 如果指定了联系人，先解析
        contact_id = None
        if contact:
            contact_id = self._resolve_contact_id(contact)
            if contact_id is None:
                return f'未找到联系人"{contact}"。'

        # 获取消息并搜索
        if contact_id:
            messages = self.extractor.get_messages(contact_id=contact_id)
        else:
            # 无法不指定联系人搜索全库时，返回提示
            return (
                f'搜索关键词"{keyword}"：请指定联系人范围以缩小搜索范围。\n'
                "如果刚才已经提取过聊天，请先调用 get_workspace 查看当前联系人；"
                "否则使用 list_contacts 工具查看联系人列表，然后指定 contact 参数。"
            )

        # 在消息中搜索关键词
        results: List[dict] = []
        for msg in messages:
            content = msg.get("StrContent", "") or ""
            if keyword.lower() in content.lower():
                results.append(msg)

        if not results:
            scope = f'与"{contact}"的聊天记录' if contact else "所有聊天记录"
            return f'在{scope}中未找到包含"{keyword}"的消息。'

        lines: List[str] = [f'搜索"{keyword}"，找到 {len(results)} 条匹配消息：\n']
        display_limit = 30
        for msg in results[:display_limit]:
            time_str = msg.get("CreateTimeStr", "未知时间")
            sender_tag = "我" if msg.get("IsSender") else "对方"
            content = msg.get("StrContent", "")[:200]
            msg_id = msg.get("MsgSvrID", "")
            lines.append(f"  [{time_str}] {sender_tag}: {content}")
            lines.append(f"    消息ID: {msg_id}")

        if len(results) > display_limit:
            lines.append(f"\n  ... 还有 {len(results) - display_limit} 条匹配结果未显示")

        return "\n".join(lines)

    def _execute_generate_evidence_list(self, args: Dict[str, Any]) -> str:
        """生成证据清单。"""
        title = args["title"]
        case_type = args["case_type"]
        evidence_ids = args.get("evidence_ids", [])

        if self.evidence_generator and hasattr(self.evidence_generator, "generate"):
            try:
                result = self.evidence_generator.generate(
                    title=title,
                    case_type=case_type,
                    evidence_ids=evidence_ids,
                )
                return f"证据清单已生成：\n标题：{title}\n案由：{case_type}\n{result}"
            except Exception as e:
                return f"生成证据清单时出错：{e}"

        # 无证据生成器时，基于内存中的标记生成简易清单
        lines: List[str] = [
            f"【证据清单】",
            f"标题：{title}",
            f"案由：{case_type}",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]

        marks = []
        if self.memory:
            if evidence_ids:
                marks = [
                    m for m in self.memory.get_evidence_marks()
                    if m.msg_id in evidence_ids
                ]
            else:
                marks = self.memory.get_evidence_marks()

        if marks:
            lines.append(f"共 {len(marks)} 项证据：\n")
            for i, mark in enumerate(marks, 1):
                lines.append(f"  证据{i}：")
                lines.append(f"    消息ID：{mark.msg_id}")
                lines.append(f"    标签：{mark.label}")
                if mark.note:
                    lines.append(f"    说明：{mark.note}")
                lines.append(f"    标记时间：{mark.created_at}")
                lines.append("")
        else:
            lines.append("暂无已标记的证据。请先使用 mark_evidence 工具标记关键消息。")

        return "\n".join(lines)

    def _execute_analyze_case(self, args: Dict[str, Any]) -> str:
        """分析案件要点。"""
        focus = args.get("focus", "")

        if self.analyzer and hasattr(self.analyzer, "analyze"):
            try:
                result = self.analyzer.analyze(focus=focus)
                return result
            except Exception as e:
                return f"案件分析出错：{e}"

        # 无分析器时，基于记忆数据给出基础分析
        lines: List[str] = ["【案件分析】\n"]

        if focus:
            lines.append(f"分析重点：{focus}\n")

        if self.memory:
            case_info = self.memory.get_case_info()
            marks = self.memory.get_evidence_marks()

            if case_info.case_type:
                lines.append(f"案由：{case_info.case_type}")
            if case_info.plaintiff and case_info.defendant:
                lines.append(f"当事人：{case_info.plaintiff} 诉 {case_info.defendant}")

            if marks:
                lines.append(f"\n已标记证据 {len(marks)} 条：")
                label_groups: Dict[str, List[Any]] = {}
                for mark in marks:
                    label_groups.setdefault(mark.label, []).append(mark)
                for label, group in label_groups.items():
                    lines.append(f"  - {label}：{len(group)} 条")

                lines.append("\n证据链完整性评估：")
                lines.append("  提示：请确保关键事实均有对应证据支撑。")
                lines.append("  建议关注以下方面：")
                lines.append("    1. 证据的时间连续性")
                lines.append("    2. 证据之间的相互印证")
                lines.append("    3. 是否存在证据缺失的环节")
            else:
                lines.append("\n尚未标记任何证据，无法进行证据链分析。")
                lines.append("建议：先提取聊天记录并标记关键消息。")
        else:
            lines.append("案件信息未设置，请先提供案件基本情况。")

        return "\n".join(lines)

    def _execute_export_document(self, args: Dict[str, Any]) -> str:
        """导出文档。"""
        fmt = args["format"]
        filename = args.get("filename", f"evidence_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

        if self.evidence_generator and hasattr(self.evidence_generator, "export"):
            try:
                path = self.evidence_generator.export(format=fmt, filename=filename)
                return f"文档已导出：{path}"
            except Exception as e:
                return f"文档导出出错：{e}"

        return (
            f"文档导出功能准备就绪。\n"
            f"  格式：{fmt}\n"
            f"  文件名：{filename}.{fmt}\n"
            f"提示：证据生成模块尚未完全配置，请确认系统设置。"
        )

    def _execute_list_contacts(self, args: Dict[str, Any]) -> str:
        """列出联系人。"""
        if not self.extractor:
            return "错误：数据提取模块未初始化。请先配置微信数据源。"

        keyword = args.get("keyword")
        try:
            contacts = self.extractor.get_contacts()
        except Exception as e:
            return f"获取联系人列表失败：{e}"

        if keyword:
            # 模糊搜索
            keyword_lower = keyword.lower()
            contacts = [
                c for c in contacts
                if keyword_lower in (c.get("Remark", "") or "").lower()
                or keyword_lower in (c.get("NickName", "") or "").lower()
                or keyword_lower in (c.get("Alias", "") or "").lower()
                or keyword_lower in (c.get("UserName", "") or "").lower()
            ]

        if not contacts:
            hint = f"（关键词：{keyword}）" if keyword else ""
            return f"未找到匹配的联系人{hint}。"

        lines: List[str] = []
        if keyword:
            lines.append(f'搜索"{keyword}"，找到 {len(contacts)} 个联系人：\n')
        else:
            lines.append(f"共 {len(contacts)} 个联系人：\n")

        display_limit = 50
        for c in contacts[:display_limit]:
            remark = c.get("Remark", "")
            nick = c.get("NickName", "")
            alias = c.get("Alias", "")
            wxid = c.get("UserName", "")
            display = remark or nick or alias or wxid
            detail_parts = []
            if nick and nick != display:
                detail_parts.append(f"昵称:{nick}")
            if remark and remark != display:
                detail_parts.append(f"备注:{remark}")
            detail = f" ({', '.join(detail_parts)})" if detail_parts else ""
            lines.append(f"  - {display}{detail}")

        if len(contacts) > display_limit:
            lines.append(f"\n  ... 还有 {len(contacts) - display_limit} 个联系人未显示")

        return "\n".join(lines)

    def _execute_set_wechat_dir(self, args: Dict[str, Any]) -> str:
        """Set the WeChat data directory on the active extractor."""
        if not self.extractor:
            return "错误：数据提取模块未初始化。"

        from pathlib import Path

        raw_path = (args.get("path") or "").strip().strip('"')
        if not raw_path:
            return "请提供微信数据目录路径。"

        path = Path(raw_path).expanduser()
        try:
            if hasattr(self.extractor, "_resolve_wechat_dir"):
                resolved = self.extractor._resolve_wechat_dir(path)
            else:
                resolved = path
            self.extractor._wechat_dir = resolved
        except Exception as e:
            return f"设置微信数据目录失败：{e}"

        return f"微信数据目录已设置为：{resolved}"

    def _execute_mark_evidence(self, args: Dict[str, Any]) -> str:
        """标记证据。"""
        if not self.memory:
            return "错误：案件记忆模块未初始化。"

        msg_ids: List[int] = args["msg_ids"]
        label: str = args["label"]

        marked_count = 0
        for msg_id in msg_ids:
            self.memory.add_evidence_mark(msg_id=msg_id, label=label)
            marked_count += 1

        total = len(self.memory.get_evidence_marks())
        return (
            f'已成功标记 {marked_count} 条消息为"{label}"。\n'
            f"当前共有 {total} 条证据标记。"
        )

    def _execute_get_timeline(self, args: Dict[str, Any]) -> str:
        """获取时间线。"""
        if not self.extractor:
            return "错误：数据提取模块未初始化。请先配置微信数据源。"

        contact = args.get("contact") or self.workspace.contact

        if not contact:
            return "请指定联系人以获取对话时间线。使用 list_contacts 工具查看可用联系人。"

        contact_id = self._resolve_contact_id(contact)
        if contact_id is None:
            return f'未找到联系人"{contact}"。'

        messages = self.extractor.get_messages(contact_id=contact_id)
        if not messages:
            return f'与"{contact}"没有聊天记录，无法生成时间线。'

        # 按日期分组统计
        daily: Dict[str, int] = {}
        for msg in messages:
            time_str = msg.get("CreateTimeStr", "")
            if time_str:
                day = time_str[:10]
                daily[day] = daily.get(day, 0) + 1

        lines: List[str] = [
            f'与"{contact}"的对话时间线：',
            f"总消息数：{len(messages)}",
            f"时间跨度：{min(daily.keys())} 至 {max(daily.keys())}",
            f"活跃天数：{len(daily)} 天\n",
            "每日消息量：",
        ]

        for day in sorted(daily.keys()):
            bar = "█" * min(daily[day] // 2, 40)
            lines.append(f"  {day}  {daily[day]:>4}条  {bar}")

        return "\n".join(lines)

    def _execute_summarize_chat(self, args: Dict[str, Any]) -> str:
        """概要总结聊天。"""
        if not self.extractor:
            return "错误：数据提取模块未初始化。请先配置微信数据源。"

        contact = args.get("contact") or self.workspace.contact
        topic = args.get("topic")

        if not contact:
            return "当前还没有可总结的聊天材料。请先提取某个联系人的聊天记录。"

        contact_id = self._resolve_contact_id(contact)
        if contact_id is None:
            return f'未找到联系人"{contact}"。'

        messages = self.extractor.get_messages(contact_id=contact_id)
        if not messages:
            return f'与"{contact}"没有聊天记录，无法生成摘要。'

        # 基础统计摘要
        total = len(messages)
        sent = sum(1 for m in messages if m.get("IsSender"))
        received = total - sent

        # 消息类型统计
        type_counts: Dict[int, int] = {}
        for msg in messages:
            t = msg.get("Type", -1)
            type_counts[t] = type_counts.get(t, 0) + 1

        type_names = {
            1: "文本", 3: "图片", 34: "语音", 43: "视频",
            47: "表情", 49: "应用消息", 10000: "系统消息",
        }

        lines: List[str] = [
            f'与"{contact}"的聊天摘要：',
            f"  总消息数：{total} 条",
            f"  我发送：{sent} 条 | 对方发送：{received} 条",
            "",
            "  消息类型分布：",
        ]

        for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            name = type_names.get(t, f"类型{t}")
            lines.append(f"    {name}：{count} 条")

        if topic:
            # 筛选包含话题关键词的消息
            topic_msgs = [
                m for m in messages
                if topic.lower() in (m.get("StrContent", "") or "").lower()
            ]
            lines.append(f'\n  与"{topic}"相关的消息：{len(topic_msgs)} 条')
            for msg in topic_msgs[:10]:
                time_str = msg.get("CreateTimeStr", "")
                sender = "我" if msg.get("IsSender") else "对方"
                content = (msg.get("StrContent", "") or "")[:100]
                lines.append(f"    [{time_str}] {sender}: {content}")
            if len(topic_msgs) > 10:
                lines.append(f"    ... 还有 {len(topic_msgs) - 10} 条相关消息")

        return "\n".join(lines)

    def _execute_get_workspace(self, args: Dict[str, Any]) -> str:
        """Return the current agent workspace."""
        return self.workspace.to_prompt_context()

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _resolve_contact_id(self, contact: str) -> Optional[str]:
        """
        将联系人名称/备注解析为微信内部ID。

        优先精确匹配备注名，其次模糊匹配昵称和微信号。
        如果输入本身就是 wxid 格式则直接返回。

        Args:
            contact: 联系人名称、备注或 wxid。

        Returns:
            微信内部ID（UserName），未找到返回 None。
        """
        contact = self._normalize_contact_name(contact)

        # 如果已经是 wxid 格式，直接返回
        if contact.startswith("wxid_") or contact.endswith("@chatroom"):
            return contact

        if not self.extractor:
            return None

        try:
            contacts = self.extractor.get_contacts()
        except Exception:
            return None

        # 精确匹配备注名
        for c in contacts:
            if c.get("Remark") == contact:
                return c["UserName"]

        # 精确匹配昵称
        for c in contacts:
            if c.get("NickName") == contact:
                return c["UserName"]

        # 精确匹配微信号
        for c in contacts:
            if c.get("Alias") == contact:
                return c["UserName"]

        # 模糊匹配
        contact_lower = contact.lower()
        for c in contacts:
            fields = [
                c.get("Remark", ""),
                c.get("NickName", ""),
                c.get("Alias", ""),
            ]
            if any(contact_lower in (f or "").lower() for f in fields):
                return c["UserName"]

        return None

    @staticmethod
    def _normalize_contact_name(contact: str) -> str:
        text = str(contact or "").strip()
        text = re.sub(r"^(我)?(本地)?(和|与|跟)", "", text)
        text = re.sub(r"(的|聊天|记录|最近|部分)+$", "", text)
        return text.strip()

    def _guard_new_wechat_decrypt(self) -> Optional[str]:
        """Avoid long memory scans for newer WeChat layouts without a known key."""
        if os.environ.get("WECHAT_DB_KEY", "").strip():
            return None
        if not self.extractor:
            return None
        try:
            wechat_dir = getattr(self.extractor, "_wechat_dir", None)
            if wechat_dir is None:
                return None
            is_new_layout = getattr(self.extractor, "_is_new_windows_layout", None)
            if callable(is_new_layout) and is_new_layout(wechat_dir):
                return _WECHAT_KEY_REQUIRED_MESSAGE
        except Exception:
            return None
        return None
