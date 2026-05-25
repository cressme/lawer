"""State models for the LangGraph evidence agent."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class EvidenceAgentState(TypedDict, total=False):
    """Shared graph state for one evidence-assistant conversation."""

    user_input: str
    response: str
    intent: str
    next_action: str

    case_id: str
    thread_id: str
    wechat_dir: str

    contact_query: str
    contact_id: str
    contact_name: str
    contact_candidates: List[Dict[str, Any]]

    messages: List[Dict[str, Any]]
    message_count: int
    time_range: Dict[str, str]
    message_type_counts: Dict[str, int]
    text_preview: List[Dict[str, Any]]

    image_messages: List[Dict[str, Any]]
    image_attachments: List[Dict[str, Any]]
    image_evidence: List[Dict[str, Any]]
    pending_confirmations: List[Dict[str, Any]]
    last_confirmation: Dict[str, Any]
    attachment_counts: Dict[str, int]

    material_summary: Dict[str, Any]
    analysis: Dict[str, Any]

    errors: List[str]


def merge_errors(state: EvidenceAgentState, error: str) -> Dict[str, Any]:
    errors = list(state.get("errors") or [])
    errors.append(error)
    return {"errors": errors}


def current_contact(state: EvidenceAgentState) -> Optional[str]:
    return state.get("contact_name") or state.get("contact_query") or None
