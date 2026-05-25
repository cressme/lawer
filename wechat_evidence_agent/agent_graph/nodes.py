"""LangGraph nodes for first-phase evidence intake and case analysis."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict

from .image_evidence import format_image_evidence_for_llm, inspect_image_evidence
from .materials import format_materials_for_llm, resolve_contact, summarize_messages
from .state import EvidenceAgentState, merge_errors


PLANNER_SYSTEM = """你是微信证据助手的编排大脑。你负责理解律师用户的自然语言意图，并决定下一步动作。

第一期目标：判断律师意图，从微信聊天中提取文字和图片材料，并进入案情/证据分析。

重要业务边界：当前登录的微信账号通常是律师本人或律所工作微信。被提取的聊天联系人通常是客户、委托人、证据提供人或案件相关人员；案件真实纠纷往往是“客户/委托人与第三方”之间的纠纷。不要把“当前微信账号与聊天联系人之间的沟通”默认理解成律师与客户之间存在纠纷。

可选 next_action：
- extract_and_analyze：用户要查询某人的聊天、整理资料、分析案情、找证据，并且能识别联系人。
- analyze_current：用户要继续分析当前材料，并且当前已经有材料。
- clarify：联系人不清楚、候选太多，或缺少必要信息。
- answer：普通说明性问题，不需要读取微信。

只输出 JSON：
{
  "intent": "简短意图",
  "next_action": "extract_and_analyze|analyze_current|clarify|answer",
  "contact_query": "联系人姓名/备注/wxid；没有则空字符串",
  "reply": "如果需要直接回答或追问，写给用户的话；否则空字符串"
}

规则：
1. 用户说“查我本地跟X的聊天”“整理我和X的微信资料”“分析X聊天证据”，contact_query 填 X，next_action 用 extract_and_analyze。
2. 用户说“继续分析”“分析刚才那段”，如果当前材料已存在，用 analyze_current。
3. 不要要求用户提供微信目录，系统会处理本地目录。
4. 除非联系人确实缺失，否则不要先问案情；先提取材料，再进入分析。
5. 如果用户说“我和X的聊天”，这里的“我”优先解释为律师账号与联系人 X 的沟通渠道，而不是案件当事人身份。案情主体、原告被告、相对方需要从聊天内容中抽取或向律师追问。
"""


ANALYSIS_SYSTEM = """你是一名资深中国民事诉讼律师助理。你的任务是根据已经提取的微信聊天材料，帮助律师做第一期案情整理和证据分析。

角色边界必须牢记：当前登录微信账号通常是律师本人或律所工作微信；聊天联系人通常是客户、委托人、证据提供人或案件相关人员。聊天里标记为“我”的消息只代表律师账号发出的消息，“对方”只代表当前联系人发出的消息。不要默认当前微信账号与联系人之间就是纠纷双方，也不要把律师当作案件当事人。真实纠纷主体可能是客户与第三方，需要从聊天材料中识别；识别不出来时要明确标注“待确认”。

材料包含：
- 文字聊天片段
- 图片消息记录
- 本地图片/缩略图附件路径和数量
- 图片证据识别结果：解码路径、格式尺寸、SHA256、OCR 文本或失败原因
- 其他消息类型统计

要求：
1. 先说明已经整理到哪些材料，尤其要单独说明图片材料数量、已解码数量、OCR 识别情况和本地路径情况。
2. 从现有文字和图片消息中提炼可能的案件事实；图片内容只能依据 OCR 文本或明确元数据表述，不能臆测图片画面。
3. 先区分“沟通关系”和“案件关系”：沟通关系是律师账号与联系人；案件关系要单独列出“客户/委托人、案件相对方、第三方、待确认人员”。如果无法确认，不要硬编。
4. 输出案情初筛：可能涉及的法律关系、关键时间线、关键证据、证据缺口。
5. 给出下一步可执行动作：关键词筛选、图片 OCR/查看、导出证据包、补充案由/诉求。
6. 如果图片失败原因包含“新版图片 AES Key”，必须用普通用户能理解的话提示：
   “请在微信客户端中打开/预览最近一张或目标聊天图片，保持微信不关闭，然后回到本软件重新识别图片/重新分析。”
   同时说明这是为了让微信把图片解密 key 加载到内存，不是让用户手工找文件。
7. 使用中文，面向普通律师用户，务实、清楚。
"""


def make_planner_node(model: Any) -> Callable[[EvidenceAgentState], Dict[str, Any]]:
    def planner(state: EvidenceAgentState) -> Dict[str, Any]:
        current = {
            "has_materials": bool(state.get("messages")),
            "current_contact": state.get("contact_name") or state.get("contact_query") or "",
            "message_count": state.get("message_count", 0),
            "image_count": (state.get("material_summary") or {}).get("image_message_count", 0),
        }
        user_input = state.get("user_input", "")
        user = (
            f"当前状态：{json.dumps(current, ensure_ascii=False)}\n\n"
            f"用户输入：{user_input}"
        )

        fallback_contact = extract_contact_fallback(user_input)
        try:
            data = model.complete_json(PLANNER_SYSTEM, user, max_tokens=800)
        except Exception:
            data = {}

        next_action = data.get("next_action") or ""
        contact_query = data.get("contact_query") or fallback_contact

        if fallback_contact and next_action not in {"extract_and_analyze", "analyze_current"}:
            next_action = "extract_and_analyze"
            contact_query = fallback_contact
        elif not next_action:
            next_action = "answer"

        if next_action == "extract_and_analyze" and not contact_query:
            next_action = "clarify"
            data["reply"] = "请告诉我要整理哪位联系人或备注名的微信聊天。"

        return {
            "intent": data.get("intent", ""),
            "next_action": next_action,
            "contact_query": contact_query or state.get("contact_query", ""),
            "response": data.get("reply", ""),
        }

    return planner


def make_contact_resolver_node(extractor: Any) -> Callable[[EvidenceAgentState], Dict[str, Any]]:
    def contact_resolver(state: EvidenceAgentState) -> Dict[str, Any]:
        query = state.get("contact_query", "")
        try:
            best, candidates = resolve_contact(extractor, query)
        except Exception as exc:
            return merge_errors(state, f"联系人解析失败：{exc}")

        if not best:
            return {
                "contact_candidates": [
                    {
                        "contact_id": item.contact_id,
                        "display_name": item.display_name,
                        "score": item.score,
                    }
                    for item in candidates
                ],
                "response": build_contact_clarification(query, candidates),
                "next_action": "clarify",
            }

        return {
            "contact_id": best.contact_id,
            "contact_name": best.display_name,
            "contact_candidates": [],
        }

    return contact_resolver


def make_chat_extraction_node(extractor: Any) -> Callable[[EvidenceAgentState], Dict[str, Any]]:
    def chat_extraction(state: EvidenceAgentState) -> Dict[str, Any]:
        contact_id = state.get("contact_id")
        if not contact_id:
            return merge_errors(state, "缺少联系人 ID，无法提取聊天。")
        try:
            messages = extractor.get_messages(contact_id=contact_id)
            summary = summarize_messages(messages)
        except Exception as exc:
            return merge_errors(state, f"聊天提取失败：{exc}")

        return {
            "messages": messages,
            **summary,
        }

    return chat_extraction


def make_image_inspection_node(
    media_extractor: Any = None,
    media_processor: Any = None,
    output_dir: Any = None,
) -> Callable[[EvidenceAgentState], Dict[str, Any]]:
    def image_inspection(state: EvidenceAgentState) -> Dict[str, Any]:
        try:
            results = inspect_image_evidence(
                state.get("image_attachments") or [],
                media_extractor=media_extractor,
                media_processor=media_processor,
                output_dir=output_dir,
            )
        except Exception as exc:
            return merge_errors(state, f"图片证据识别失败：{exc}")
        return {"image_evidence": results}

    return image_inspection


def make_analysis_node(model: Any) -> Callable[[EvidenceAgentState], Dict[str, Any]]:
    def analysis(state: EvidenceAgentState) -> Dict[str, Any]:
        if not state.get("messages"):
            return {"response": "当前还没有提取到聊天材料。请先告诉我要整理哪位联系人的微信聊天。"}

        materials = format_materials_for_llm(state)
        image_evidence = format_image_evidence_for_llm(state.get("image_evidence") or [])
        user = (
            "请基于以下微信材料完成第一期案情整理和证据分析。\n"
            "注意：图片只能依据 OCR 文本、解码文件、格式尺寸和哈希等识别结果分析，不能臆测图片画面。\n\n"
            f"{materials}\n\n"
            f"{image_evidence}"
        )
        text = model.complete(ANALYSIS_SYSTEM, user, max_tokens=2600)
        return {
            "analysis": {"text": text},
            "response": text,
        }

    return analysis


def response_node(state: EvidenceAgentState) -> Dict[str, Any]:
    if state.get("errors"):
        return {"response": "\n".join(state["errors"])}
    if state.get("response"):
        return {"response": state["response"]}
    return {"response": "我已经处理完成。"}


def route_after_planner(state: EvidenceAgentState) -> str:
    action = state.get("next_action")
    if action == "extract_and_analyze":
        return "contact_resolver"
    if action == "analyze_current":
        return "analysis"
    return "response"


def route_after_contact(state: EvidenceAgentState) -> str:
    if state.get("errors") or state.get("next_action") == "clarify":
        return "response"
    return "chat_extraction"


def route_after_extract(state: EvidenceAgentState) -> str:
    if state.get("errors"):
        return "response"
    return "image_inspection"


def extract_contact_fallback(text: str) -> str:
    normalized = re.sub(r"\s+", "", text or "")
    stop_words = (
        "的聊天",
        "聊天记录",
        "微信资料",
        "微信记录",
        "证据",
        "资料",
        "并分析",
        "分析",
    )
    patterns = [
        r"(?:跟|和|与)([\u4e00-\u9fa5A-Za-z0-9_^.-]{2,32})(?:的)?(?:聊天|微信|记录|资料|证据)",
        r"(?:查|查询|提取|整理|分析)(?:一下|下)?(?:我)?(?:本地)?(?:跟|和|与)?([\u4e00-\u9fa5A-Za-z0-9_^.-]{2,32})(?:的)?(?:聊天|微信|记录|资料|证据)",
        r"(wxid_[A-Za-z0-9_\\-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        contact = match.group(1).strip()
        for word in stop_words:
            contact = contact.replace(word, "")
        contact = re.sub(r"(的|聊天|微信|记录|资料|证据|并分析|分析)+$", "", contact)
        contact = contact.strip("，。,.：:")
        if contact:
            return contact
    return ""


def build_contact_clarification(query: str, candidates: list[Any]) -> str:
    if not candidates:
        return f'没有找到联系人“{query}”。请确认备注名、昵称或微信号是否正确。'
    lines = [f'没有唯一确定联系人“{query}”，请从下面候选里确认：']
    for item in candidates[:8]:
        lines.append(f"- {item.display_name}（{item.contact_id}）")
    return "\n".join(lines)
