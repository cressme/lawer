"""Evidence material helpers for chat and image extraction."""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


TYPE_NAMES = {
    1: "文字",
    3: "图片",
    34: "语音",
    43: "视频",
    49: "文件/链接",
    50: "通话",
    10000: "系统",
}


@dataclass
class ContactMatch:
    contact_id: str
    display_name: str
    score: int
    raw: Dict[str, Any]


def normalize_contact_name(contact: str) -> str:
    import re

    text = str(contact or "").strip()
    text = re.sub(r"^(我)?(本地)?(和|与|跟)", "", text)
    text = re.sub(r"(的|聊天|记录|最近|部分)+$", "", text)
    return text.strip()


def resolve_contact(extractor: Any, query: str) -> tuple[Optional[ContactMatch], List[ContactMatch]]:
    query = normalize_contact_name(query)
    if not query:
        return None, []
    if query.startswith("wxid_") or query.endswith("@chatroom"):
        return ContactMatch(query, query, 100, {"UserName": query}), []

    contacts = extractor.get_contacts()
    matches: List[ContactMatch] = []
    query_lower = query.lower()

    for contact in contacts:
        username = contact.get("UserName") or ""
        fields = {
            "Remark": contact.get("Remark") or "",
            "NickName": contact.get("NickName") or "",
            "Alias": contact.get("Alias") or "",
            "UserName": username,
        }
        display = (fields["Remark"] or fields["NickName"] or fields["Alias"] or username).strip()
        score = 0
        if fields["Remark"] == query:
            score = 100
        elif fields["NickName"] == query:
            score = 90
        elif fields["Alias"] == query:
            score = 85
        elif fields["UserName"] == query:
            score = 80
        elif any(query_lower in value.lower() for value in fields.values() if value):
            score = 60
        if score:
            matches.append(ContactMatch(username, display, score, contact))

    matches.sort(key=lambda item: item.score, reverse=True)
    best = matches[0] if matches and (matches[0].score >= 80 or len(matches) == 1) else None
    return best, matches[:10]


def summarize_messages(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    type_counts: Counter[str] = Counter()
    attachment_counts: Counter[str] = Counter()
    image_messages: List[Dict[str, Any]] = []
    image_attachments: List[Dict[str, Any]] = []
    text_preview: List[Dict[str, Any]] = []
    times = [m.get("CreateTimeStr") for m in messages if m.get("CreateTimeStr")]

    for msg in messages:
        msg_type = message_type(msg)
        type_name = TYPE_NAMES.get(msg_type, f"其他({msg_type})")
        type_counts[type_name] += 1

        content = (msg.get("StrContent") or "").strip()
        if content and len(text_preview) < 40:
            text_preview.append(format_message_brief(msg, max_len=180))

        attachments = msg.get("Attachments") or []
        for item in attachments:
            label = attachment_type_label(item.get("type"))
            attachment_counts[label] += 1
            if item.get("type") in ("image", "thumbnail"):
                image_attachments.append(format_attachment(item, msg))

        if msg_type == 3:
            image_messages.append({
                "time": msg.get("CreateTimeStr", ""),
                "sender": "我" if msg.get("IsSender") else "对方",
                "content": content or "[图片]",
                "msg_id": str(msg.get("MsgSvrID") or msg.get("MsgLocalID") or ""),
                "attachments": [format_attachment(item, msg) for item in attachments],
            })

    return {
        "message_count": len(messages),
        "time_range": {
            "start": times[0] if times else "",
            "end": times[-1] if times else "",
        },
        "message_type_counts": dict(type_counts),
        "attachment_counts": dict(attachment_counts),
        "text_preview": text_preview,
        "image_messages": image_messages,
        "image_attachments": image_attachments,
        "material_summary": {
            "message_count": len(messages),
            "text_count": type_counts.get("文字", 0),
            "image_message_count": type_counts.get("图片", 0),
            "located_image_file_count": sum(
                1 for item in image_attachments if item.get("kind") == "图片"
            ),
            "located_thumbnail_count": sum(
                1 for item in image_attachments if item.get("kind") == "缩略图"
            ),
            "file_link_count": type_counts.get("文件/链接", 0),
            "video_count": type_counts.get("视频", 0),
            "voice_count": type_counts.get("语音", 0),
        },
    }


def message_type(msg: Dict[str, Any]) -> int:
    try:
        return int(msg.get("Type") or 0) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return 0


def format_message_brief(msg: Dict[str, Any], max_len: int = 180) -> Dict[str, Any]:
    content = (msg.get("StrContent") or "").replace("\n", " ").strip()
    if len(content) > max_len:
        content = f"{content[:max_len]}..."
    return {
        "time": msg.get("CreateTimeStr", ""),
        "sender": "我" if msg.get("IsSender") else "对方",
        "content": content or TYPE_NAMES.get(message_type(msg), "[非文本消息]"),
        "msg_id": str(msg.get("MsgSvrID") or msg.get("MsgLocalID") or ""),
    }


def format_attachment(item: Dict[str, Any], msg: Dict[str, Any]) -> Dict[str, Any]:
    path = item.get("path") or ""
    return {
        "type": item.get("type") or "attachment",
        "kind": attachment_type_label(item.get("type")),
        "path": path,
        "name": item.get("name") or os.path.basename(path),
        "size": int(item.get("size") or 0),
        "mime": item.get("mime") or "",
        "time": msg.get("CreateTimeStr", ""),
        "sender": "我" if msg.get("IsSender") else "对方",
        "msg_id": str(msg.get("MsgSvrID") or msg.get("MsgLocalID") or ""),
    }


def attachment_type_label(kind: Optional[str]) -> str:
    return {
        "image": "图片",
        "thumbnail": "缩略图",
        "video": "视频",
        "file": "文件",
        "attachment": "附件",
    }.get(kind or "attachment", kind or "附件")


def format_materials_for_llm(state: Dict[str, Any], limit: int = 80) -> str:
    lines = [
        "角色说明：当前登录微信账号通常是律师本人或律所工作微信；下面聊天中的“我”仅表示律师账号发出的消息，“对方”仅表示当前联系人发出的消息。不要据此默认双方存在纠纷，案件主体需要从聊天内容中另行识别。",
        f"联系人：{state.get('contact_name') or state.get('contact_query')}",
        f"消息总数：{state.get('message_count', 0)}",
        f"时间范围：{(state.get('time_range') or {}).get('start', '')} 至 {(state.get('time_range') or {}).get('end', '')}",
        f"消息类型：{state.get('message_type_counts') or {}}",
        f"附件统计：{state.get('attachment_counts') or {}}",
        "",
        "文字聊天片段：",
    ]
    for item in (state.get("text_preview") or [])[:limit]:
        lines.append(f"- [{item.get('time')}] {item.get('sender')}：{item.get('content')}")

    image_messages = state.get("image_messages") or []
    if image_messages:
        lines.extend(["", "图片消息材料："])
        for item in image_messages[:30]:
            lines.append(
                f"- [{item.get('time')}] {item.get('sender')}：{item.get('content')} "
                f"附件数 {len(item.get('attachments') or [])}"
            )
            for attachment in (item.get("attachments") or [])[:3]:
                lines.append(
                    f"  - {attachment.get('kind')} {attachment.get('name')} "
                    f"{attachment.get('path')} ({attachment.get('size')} bytes)"
                )
    return "\n".join(lines)
