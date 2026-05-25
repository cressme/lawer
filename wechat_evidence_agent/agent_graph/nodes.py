"""LangGraph nodes for first-phase evidence intake and case analysis."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict

from .image_evidence import format_image_evidence_for_llm, inspect_image_evidence
from .materials import format_materials_for_llm, resolve_contact, summarize_messages
from .state import EvidenceAgentState, merge_errors


logger = logging.getLogger(__name__)


PLANNER_SYSTEM = """你是微信证据助手的编排大脑。你负责理解律师用户的自然语言意图，并决定下一步动作。

第一期目标：判断律师意图，从微信聊天中提取文字和图片材料，并进入案情/证据分析。

重要业务边界：当前登录的微信账号通常是律师本人或律所工作微信。被提取的聊天联系人通常是客户、委托人、证据提供人或案件相关人员；案件真实纠纷往往是“客户/委托人与第三方”之间的纠纷。不要把“当前微信账号与聊天联系人之间的沟通”默认理解成律师与客户之间存在纠纷。

可选 next_action：
- extract_only：用户只是要查询、调取、看看某人的聊天记录或微信资料，并且能识别联系人。先提取材料和图片，不直接做案情分析。
- extract_and_analyze：用户明确要分析案情、证据链、证明事实、法律关系、诉讼策略，或要求在提取某人聊天后直接分析。
- analyze_current：用户要继续分析当前材料，并且当前已经有材料。
- clarify：用户想让系统执行某个任务，但联系人、材料范围、案情目标或必要事实不足，需要先追问。
- answer：闲聊、产品能力说明、一般法律常识/法条解释、操作说明、无需读取微信材料的问题。

只输出 JSON：
{
  "intent": "简短意图",
  "next_action": "extract_only|extract_and_analyze|analyze_current|clarify|answer",
  "contact_query": "联系人姓名/备注/wxid；没有则空字符串",
  "reply": "如果需要直接回答或追问，写给用户的话；否则空字符串"
}

规则：
1. 用户说“查我本地跟X的聊天”“看看我和X的对话”“调取X聊天记录”“整理我和X的微信资料”，但没有明确要求案情/证据分析时，contact_query 填 X，next_action 用 extract_only。
2. 用户说“分析X聊天证据”“整理证据链”“看能证明什么”“分析案情”“提取并分析”，contact_query 填 X，next_action 用 extract_and_analyze。
3. 用户说“继续分析”“分析刚才那段”，如果当前材料已存在，用 analyze_current。
4. 不要要求用户提供微信目录，系统会处理本地目录。
5. 除非联系人确实缺失，否则不要先问案情；先提取材料，再由用户决定是否进入分析。
6. 如果用户说“我和X的聊天”，这里的“我”优先解释为律师账号与联系人 X 的沟通渠道，而不是案件当事人身份。案情主体、原告被告、相对方需要从聊天内容中抽取或向律师追问。
7. 不要因为出现“资料”“整理”就默认分析；没有“分析/案情/证据链/证明/诉讼/法律关系/起诉/抗辩/违约/借款/欠款/合同”等分析信号时，优先 extract_only。
8. 用户只是打招呼、闲聊、问软件能做什么、问一般法律知识或法条含义，不要进入微信提取流程，使用 answer。
9. 用户想分析案件但没有给任何材料，也没有当前会话材料时，用 clarify，追问最少必要信息。
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


RELATIONSHIP_SYSTEM = """你是一名严谨的律师证据助理。用户正在问：从当前微信聊天材料能否看出“当前微信账号”和联系人之间是什么关系。

回答边界：
1. 这里的“我”优先指当前登录微信账号，不要自动当成案件当事人。
2. 只根据聊天文字和已知图片/OCR元数据判断关系，不要臆测图片内容。
3. 直接回答关系判断，不要输出完整案情分析报告。
4. 如果只能判断“沟通/工作/熟人/委托咨询”等可能性，要明确说“倾向于”“证据不足以确认”。
5. 必须列出支持判断的聊天片段或事实依据。
6. 如果无法确认律师、客户、朋友、同事、合作方等身份，要明确写“不能确认”。

输出结构：
- 结论：一句话回答最可能的关系。
- 依据：列出 3-8 条聊天依据。
- 不能确认的地方：说明缺口。
- 下一步建议：为了确认关系，建议继续筛选哪些关键词或查看哪些图片。
"""


DIRECT_ANSWER_SYSTEM = """你是微信证据助手中的通用对话节点，面向律师用户。

职责：
1. 回答闲聊、产品能力、操作说明、一般法律常识、法条解释等不需要读取微信材料的问题。
2. 如果用户的问题需要具体案情或证据才能判断，要明确说明还缺哪些信息，并用 1-3 个问题引导用户补充。
3. 不要假装已经读取微信材料；只有当前状态明确有材料时，才能提及“当前会话已有材料”。
4. 法律问题只能做一般性信息说明和工作思路，不要承诺结论，不要替代正式法律意见。
5. 回复要短、直接、可执行，避免套完整报告。
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
        current_contact = state.get("contact_name") or state.get("contact_query") or ""

        relationship_question = is_relationship_question(user_input)

        if fallback_contact and next_action not in {"extract_only", "extract_and_analyze", "analyze_current"}:
            next_action = infer_contact_action(user_input)
            contact_query = fallback_contact
        elif not next_action:
            next_action = "answer"

        if next_action == "answer" and not data.get("reply"):
            data["reply"] = ""

        if relationship_question:
            if contact_query or fallback_contact:
                next_action = "extract_and_analyze"
                contact_query = contact_query or fallback_contact
            elif current_contact:
                next_action = "extract_and_analyze" if not state.get("messages") else "analyze_current"
                contact_query = current_contact
            elif state.get("messages"):
                next_action = "analyze_current"

        if (
            contact_query
            and next_action == "extract_and_analyze"
            and infer_contact_action(user_input) == "extract_only"
            and not relationship_question
        ):
            next_action = "extract_only"

        if next_action == "analyze_current" and not state.get("messages"):
            if current_contact:
                next_action = "extract_and_analyze"
                contact_query = contact_query or current_contact
            else:
                next_action = "clarify"
                data["reply"] = (
                    "当前这个会话里还没有可分析的聊天材料。"
                    "请先告诉我要查询哪位联系人，例如“查一下我与明文的对话”。"
                )

        if next_action == "analyze_current":
            contact_query = state.get("contact_query", "")

        if next_action in {"extract_only", "extract_and_analyze"} and not contact_query:
            next_action = "clarify"
            data["reply"] = "请告诉我要整理哪位联系人或备注名的微信聊天。"

        logger.info(
            "Planner decision: action=%s contact=%s has_materials=%s current_contact=%s input=%s",
            next_action,
            contact_query or "",
            bool(state.get("messages")),
            state.get("contact_name") or state.get("contact_query") or "",
            user_input[:120],
        )

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
    run_ocr: bool = False,
) -> Callable[[EvidenceAgentState], Dict[str, Any]]:
    def image_inspection(state: EvidenceAgentState) -> Dict[str, Any]:
        try:
            results = inspect_image_evidence(
                state.get("image_attachments") or [],
                media_extractor=media_extractor,
                media_processor=media_processor,
                output_dir=output_dir,
                run_ocr=run_ocr,
            )
        except Exception as exc:
            return merge_errors(state, f"图片证据识别失败：{exc}")

        updates: Dict[str, Any] = {"image_evidence": results}
        if not run_ocr:
            updates["pending_confirmations"] = build_image_ocr_confirmations(state, results)
        return updates

    return image_inspection


def build_image_ocr_confirmations(
    state: EvidenceAgentState,
    image_evidence: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    decoded = [
        item for item in image_evidence
        if item.get("status") in {"decoded", "thumbnail_decoded"} and image_ocr_source_path(item)
    ]
    if not decoded:
        return []

    recommended = select_recommended_ocr_images(decoded, state.get("user_input", ""), limit=8)
    if not recommended:
        recommended = decoded[: min(8, len(decoded))]

    return [{
        "id": "image_ocr",
        "type": "image_ocr",
        "title": "是否对图片进行 OCR 识别？",
        "message": (
            f"已发现 {len(image_evidence)} 条图片记录，其中 {len(decoded)} 张可预览。"
            "为避免一次性处理过多图片，当前还没有自动 OCR。"
        ),
        "total_count": len(image_evidence),
        "available_count": len(decoded),
        "recommended_count": len(recommended),
        "recommended_paths": [image_ocr_source_path(item) for item in recommended],
        "options": [
            {"action": "recommended", "label": f"OCR 推荐图片（{len(recommended)} 张）"},
            {"action": "all", "label": f"OCR 全部可预览图片（{len(decoded)} 张）"},
            {"action": "skip", "label": "暂不 OCR"},
        ],
    }]


def select_recommended_ocr_images(
    image_evidence: list[Dict[str, Any]],
    user_input: str,
    limit: int = 8,
) -> list[Dict[str, Any]]:
    keywords = [
        "转账", "收款", "付款", "借款", "欠款", "还款", "金额", "合同",
        "协议", "收据", "发票", "凭证", "截图", "证据", "聊天", "微信",
    ]
    scored: list[tuple[int, Dict[str, Any]]] = []
    for index, item in enumerate(image_evidence):
        haystack = " ".join([
            str(item.get("source_name") or ""),
            str(item.get("source_path") or ""),
            str(item.get("time") or ""),
            str(user_input or ""),
        ])
        score = sum(3 for keyword in keywords if keyword in haystack)
        if item.get("source_type") == "thumbnail" or item.get("thumbnail_used"):
            score -= 1
        scored.append((score * 1000 - index, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]


def image_ocr_source_path(item: Dict[str, Any]) -> str:
    decoded_path = str(item.get("decoded_path") or "")
    if decoded_path:
        return decoded_path
    thumbnail = item.get("thumbnail_evidence") or {}
    if isinstance(thumbnail, dict):
        return str(thumbnail.get("decoded_path") or "")
    return ""


def make_direct_answer_node(model: Any) -> Callable[[EvidenceAgentState], Dict[str, Any]]:
    def direct_answer(state: EvidenceAgentState) -> Dict[str, Any]:
        user_input = state.get("user_input", "")
        current = {
            "has_materials": bool(state.get("messages")),
            "current_contact": state.get("contact_name") or state.get("contact_query") or "",
            "message_count": state.get("message_count", 0),
            "image_count": (state.get("material_summary") or {}).get("image_message_count", 0),
        }
        planner_reply = (state.get("response") or "").strip()
        if planner_reply and len(planner_reply) >= 8:
            return {"response": planner_reply}

        user = (
            f"当前状态：{json.dumps(current, ensure_ascii=False)}\n\n"
            f"用户问题：{user_input}\n\n"
            "请直接回答该问题；如果信息不足，请追问最少必要信息。"
        )
        text = model.complete(DIRECT_ANSWER_SYSTEM, user, max_tokens=1200)
        return {"response": text}

    return direct_answer


def make_analysis_node(model: Any) -> Callable[[EvidenceAgentState], Dict[str, Any]]:
    def analysis(state: EvidenceAgentState) -> Dict[str, Any]:
        if not state.get("messages"):
            return {"response": "当前还没有提取到聊天材料。请先告诉我要整理哪位联系人的微信聊天。"}

        user_input = state.get("user_input", "")
        if is_relationship_question(user_input):
            materials = format_relationship_materials(state)
            user = (
                f"用户问题：{user_input}\n\n"
                "请只回答这个关系判断问题，不要写完整案情报告。\n\n"
                f"{materials}"
            )
            text = model.complete(RELATIONSHIP_SYSTEM, user, max_tokens=1800)
            return {
                "analysis": {"text": text, "mode": "relationship"},
                "response": text,
            }

        materials = format_materials_for_llm(state)
        image_evidence = format_image_evidence_for_llm(state.get("image_evidence") or [])
        user = (
            f"用户问题：{user_input}\n\n"
            "请先回答用户的具体问题；只有用户明确要求完整案情报告时，才输出完整报告。\n"
            "请基于以下微信材料完成第一期案情整理和证据分析。\n"
            "注意：你看到的是供 LLM 分析的材料包/片段，不一定是全量逐条消息；不要声称已经逐条分析全部文字消息，除非材料中明确列出了全部内容。\n"
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


def material_summary_node(state: EvidenceAgentState) -> Dict[str, Any]:
    return {"response": build_material_summary_response(state)}


def response_node(state: EvidenceAgentState) -> Dict[str, Any]:
    if state.get("errors"):
        return {"response": "\n".join(state["errors"])}
    if state.get("response"):
        return {"response": state["response"]}
    return {"response": "我已经处理完成。"}


def route_after_planner(state: EvidenceAgentState) -> str:
    action = state.get("next_action")
    if action in {"extract_only", "extract_and_analyze"}:
        return "contact_resolver"
    if action == "analyze_current":
        return "analysis"
    if action == "answer":
        return "direct_answer"
    return "response"


def route_after_contact(state: EvidenceAgentState) -> str:
    if state.get("errors") or state.get("next_action") == "clarify":
        return "response"
    return "chat_extraction"


def route_after_extract(state: EvidenceAgentState) -> str:
    if state.get("errors"):
        return "response"
    return "image_inspection"


def route_after_image_inspection(state: EvidenceAgentState) -> str:
    if state.get("errors"):
        return "response"
    if state.get("next_action") == "extract_and_analyze":
        return "analysis"
    return "material_summary"


def infer_contact_action(text: str) -> str:
    normalized = re.sub(r"\s+", "", text or "")
    if is_relationship_question(normalized):
        return "extract_and_analyze"
    analysis_words = (
        "分析",
        "证据链",
        "案情",
        "证明",
        "判断关系",
        "关系判断",
        "什么关系",
        "沟通关系",
        "诉讼",
        "起诉",
        "抗辩",
        "法律关系",
        "违约",
        "借款",
        "欠款",
        "合同",
        "胜诉",
        "风险",
    )
    return "extract_and_analyze" if any(word in normalized for word in analysis_words) else "extract_only"


def build_material_summary_response(state: EvidenceAgentState) -> str:
    contact = state.get("contact_name") or state.get("contact_query") or "该联系人"
    time_range = state.get("time_range") or {}
    type_counts = state.get("message_type_counts") or {}
    attachment_counts = state.get("attachment_counts") or {}
    material_summary = state.get("material_summary") or {}
    image_evidence = state.get("image_evidence") or []
    decoded_count = sum(
        1 for item in image_evidence if item.get("status") in {"decoded", "thumbnail_decoded"}
    )
    ocr_count = sum(1 for item in image_evidence if item.get("ocr_text"))
    failed_count = sum(1 for item in image_evidence if item.get("status") == "decode_failed")

    lines = [
        f"已找到与「{contact}」的聊天材料，先为你做了材料提取和初步整理，暂不直接下法律结论。",
        "",
        "本次提取概览：",
        f"- 消息总数：{state.get('message_count', 0)} 条",
        f"- 时间范围：{time_range.get('start') or '未知'} 至 {time_range.get('end') or '未知'}",
        f"- 消息类型：{format_count_dict(type_counts)}",
        f"- 附件统计：{format_count_dict(attachment_counts)}",
        f"- 图片消息：{material_summary.get('image_message_count', 0)} 条，已生成可预览/解码结果 {decoded_count} 张，OCR 有文本 {ocr_count} 张，解码失败 {failed_count} 张",
    ]

    preview = state.get("text_preview") or []
    if preview:
        lines.extend(["", "前几条文字片段："])
        for item in preview[:8]:
            lines.append(f"- [{item.get('time')}] {item.get('sender')}：{item.get('content')}")

    image_messages = state.get("image_messages") or []
    if image_messages:
        lines.extend(["", "图片材料提示："])
        lines.append(
            f"- 共识别到 {len(image_messages)} 条图片消息。下方图片预览区会展示可解码的图片；未能解码的图片会保留失败原因。"
        )
        if any("新版图片 AES Key" in str(item.get("error", "")) for item in image_evidence):
            lines.append(
                "- 如仍有图片无法解码，请在微信客户端打开/预览目标聊天里的最近图片，保持微信不关闭，然后回到本软件重新分析。"
            )

    lines.extend([
        "",
        "下一步你可以直接说：",
        "- “分析这段聊天能证明什么”",
        "- “按借款纠纷整理证据链”",
        "- “筛选里面关于还款/金额/承诺的内容”",
    ])
    return "\n".join(lines)


def format_count_dict(data: Dict[str, Any]) -> str:
    if not data:
        return "无"
    return "，".join(f"{key} {value}" for key, value in data.items())


def is_relationship_question(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text or "")
    if not normalized:
        return False
    relation_words = ("关系", "什么关系", "认识", "熟不熟", "客户", "委托", "朋友", "同事", "合作")
    return any(word in normalized for word in relation_words)


def format_relationship_materials(state: EvidenceAgentState, limit: int = 90) -> str:
    messages = state.get("messages") or []
    text_messages = [message for message in messages if (message.get("StrContent") or "").strip()]
    selected: list[Dict[str, Any]] = []
    seen: set[str] = set()

    def add_items(items: list[Dict[str, Any]]) -> None:
        for message in items:
            key = str(message.get("MsgSvrID") or message.get("localId") or id(message))
            if key in seen:
                continue
            seen.add(key)
            selected.append(message)

    relation_keywords = (
        "同事",
        "朋友",
        "客户",
        "委托",
        "律师",
        "案件",
        "工作",
        "页面",
        "翻译",
        "账号",
        "密码",
        "到了吗",
        "在干嘛",
        "帮",
        "改",
        "钱",
        "还",
        "借",
        "工资",
        "房贷",
    )
    keyword_hits = [
        message
        for message in text_messages
        if any(keyword in str(message.get("StrContent") or "") for keyword in relation_keywords)
    ]

    add_items(text_messages[:25])
    add_items(keyword_hits[:35])
    add_items(text_messages[-30:])
    selected = selected[:limit]

    lines = [
        "关系判断材料：",
        "角色说明：下面的“我”仅表示当前登录微信账号发出的消息，“对方”表示当前联系人发出的消息。",
        f"联系人：{state.get('contact_name') or state.get('contact_query')}",
        f"消息总数：{state.get('message_count', 0)}",
        f"时间范围：{(state.get('time_range') or {}).get('start', '')} 至 {(state.get('time_range') or {}).get('end', '')}",
        f"消息类型：{state.get('message_type_counts') or {}}",
        "",
        f"以下是为判断关系抽取的 {len(selected)} 条代表性文字片段：",
    ]
    for message in selected:
        content = str(message.get("StrContent") or "").replace("\n", " ").strip()
        if len(content) > 220:
            content = f"{content[:220]}..."
        sender = "我" if message.get("IsSender") else "对方"
        lines.append(f"- [{message.get('CreateTimeStr', '')}] {sender}：{content}")

    image_evidence = state.get("image_evidence") or []
    if image_evidence:
        decoded_count = sum(
            1 for item in image_evidence if item.get("status") in {"decoded", "thumbnail_decoded"}
        )
        lines.extend([
            "",
            "图片材料概览：",
            f"- 图片识别结果 {len(image_evidence)} 项，可预览/解码 {decoded_count} 项。",
            "- 图片画面内容只有在 OCR 文本存在时才能作为关系判断依据；没有 OCR 文本时只能作为待人工查看材料。",
        ])
        ocr_items = [item for item in image_evidence if item.get("ocr_text")]
        for item in ocr_items[:8]:
            text = str(item.get("ocr_text") or "").replace("\n", " ").strip()
            lines.append(f"- [{item.get('time', '')}] 图片OCR：{text[:220]}")
    return "\n".join(lines)


def extract_contact_fallback(text: str) -> str:
    normalized = re.sub(r"\s+", "", text or "")
    stop_words = (
        "的聊天",
        "聊天记录",
        "微信消息",
        "的消息",
        "消息",
        "微信资料",
        "微信记录",
        "证据",
        "资料",
        "并分析",
        "分析",
        "对话",
    )
    patterns = [
        r"(?:跟|和|与)([\u4e00-\u9fa5A-Za-z0-9_^.-]{2,32})(?:的)?(?:聊天|消息|微信|记录|资料|证据|对话)",
        r"(?:查|查询|提取|整理|分析|看看|看一下|导入|读取)(?:了|一下|下)?(?:我)?(?:本地)?(?:跟|和|与)?([\u4e00-\u9fa5A-Za-z0-9_^.-]{2,32})(?:的)?(?:聊天|消息|微信|记录|资料|证据|对话)",
        r"(wxid_[A-Za-z0-9_\\-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        contact = match.group(1).strip()
        for word in stop_words:
            contact = contact.replace(word, "")
        contact = re.sub(r"(的|聊天|消息|微信|记录|资料|证据|对话|并分析|分析)+$", "", contact)
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
