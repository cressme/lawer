"""LangGraph-powered evidence agent."""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from langgraph.graph import END, START, StateGraph

from ..config import Config
from .model import ChatModelAdapter
from .nodes import (
    make_analysis_node,
    make_chat_extraction_node,
    make_contact_resolver_node,
    make_direct_answer_node,
    make_image_inspection_node,
    material_summary_node,
    make_planner_node,
    response_node,
    route_after_contact,
    route_after_extract,
    route_after_image_inspection,
    route_after_planner,
)
from .image_evidence import inspect_single_image
from .state import EvidenceAgentState


logger = logging.getLogger(__name__)


class LangGraphEvidenceAgent:
    """First-phase LangGraph agent for evidence intake and case analysis."""

    def __init__(
        self,
        config: Config,
        extractor: Any,
        media_extractor: Any = None,
        media_processor: Any = None,
        thread_id: str = "default",
    ) -> None:
        self.config = config
        self.extractor = extractor
        self.media_extractor = media_extractor
        self.media_processor = media_processor
        self.thread_id = thread_id
        self._sessions: Dict[str, EvidenceAgentState] = {}
        self._sessions_lock = threading.RLock()
        self.model = ChatModelAdapter(
            api_key=config.openai_api_key,
            model=config.openai_model,
            base_url=config.openai_base_url,
            temperature=0.2,
        )
        self._state: EvidenceAgentState = self._load_state(thread_id)
        self.graph = self._build_graph()

    def chat(self, user_input: str, thread_id: Optional[str] = None) -> str:
        if not user_input or not user_input.strip():
            return "请输入您的问题或指令。"
        if not self.config.openai_api_key:
            return "还没有配置大模型 API Key，请先在左侧配置。"

        active_thread_id = self._normalize_thread_id(thread_id or self.thread_id)
        with self._sessions_lock:
            current_state = self._load_state(active_thread_id)
        input_state: EvidenceAgentState = {
            **current_state,
            "user_input": user_input.strip(),
            "thread_id": active_thread_id,
            "wechat_dir": self.config.wechat_dir or current_state.get("wechat_dir", ""),
            "response": "",
            "errors": [],
        }
        result = self.graph.invoke(input_state)
        with self._sessions_lock:
            self._sessions[active_thread_id] = result
            self._state = result
            self._save_state(active_thread_id, result)
        logger.info(
            "Graph agent turn: thread=%s action=%s contact=%s messages=%s images=%s",
            active_thread_id,
            result.get("next_action"),
            result.get("contact_name") or result.get("contact_query") or "",
            result.get("message_count", 0),
            (result.get("material_summary") or {}).get("image_message_count", 0),
        )
        return str(result.get("response") or "处理完成。")

    def handle_confirmation(
        self,
        confirmation_id: str,
        action: str,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        active_thread_id = self._normalize_thread_id(thread_id or self.thread_id)
        with self._sessions_lock:
            state = self._load_state(active_thread_id)
            if confirmation_id == "image_ocr":
                result = self._handle_image_ocr_confirmation(state, action)
            else:
                result = {
                    "ok": False,
                    "response": "未找到可处理的确认任务。",
                }
            self._sessions[active_thread_id] = state
            self._state = state
            self._save_state(active_thread_id, state)
            return result

    def reset(self, thread_id: Optional[str] = None) -> None:
        active_thread_id = self._normalize_thread_id(thread_id or self.thread_id)
        with self._sessions_lock:
            self._sessions[active_thread_id] = self._initial_state(active_thread_id)
            self._state = self._sessions[active_thread_id]
            state_path = self._state_path(active_thread_id)
            if state_path.exists():
                state_path.unlink()

    def configure(self, config: Config) -> None:
        self.config = config
        self.model = ChatModelAdapter(
            api_key=config.openai_api_key,
            model=config.openai_model,
            base_url=config.openai_base_url,
            temperature=0.2,
        )
        with self._sessions_lock:
            self._state["wechat_dir"] = config.wechat_dir or ""
            for state in self._sessions.values():
                state["wechat_dir"] = config.wechat_dir or ""
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(EvidenceAgentState)
        builder.add_node("planner", make_planner_node(self.model))
        builder.add_node("contact_resolver", make_contact_resolver_node(self.extractor))
        builder.add_node("chat_extraction", make_chat_extraction_node(self.extractor))
        builder.add_node(
            "image_inspection",
            make_image_inspection_node(
                media_extractor=self.media_extractor,
                media_processor=self.media_processor,
                output_dir=self.config.output_path / "image_evidence",
            ),
        )
        builder.add_node("analysis", make_analysis_node(self.model))
        builder.add_node("direct_answer", make_direct_answer_node(self.model))
        builder.add_node("material_summary", material_summary_node)
        builder.add_node("response", response_node)

        builder.add_edge(START, "planner")
        builder.add_conditional_edges(
            "planner",
            route_after_planner,
            {
                "contact_resolver": "contact_resolver",
                "analysis": "analysis",
                "direct_answer": "direct_answer",
                "response": "response",
            },
        )
        builder.add_conditional_edges(
            "contact_resolver",
            route_after_contact,
            {
                "chat_extraction": "chat_extraction",
                "response": "response",
            },
        )
        builder.add_conditional_edges(
            "chat_extraction",
            route_after_extract,
            {
                "image_inspection": "image_inspection",
                "response": "response",
            },
        )
        builder.add_conditional_edges(
            "image_inspection",
            route_after_image_inspection,
            {
                "analysis": "analysis",
                "material_summary": "material_summary",
                "response": "response",
            },
        )
        builder.add_edge("material_summary", "response")
        builder.add_edge("analysis", "response")
        builder.add_edge("direct_answer", "response")
        builder.add_edge("response", END)
        return builder.compile()

    @property
    def state(self) -> Dict[str, Any]:
        return dict(self._state)

    def get_state(self, thread_id: Optional[str] = None) -> Dict[str, Any]:
        with self._sessions_lock:
            return dict(self._load_state(thread_id or self.thread_id))

    def _handle_image_ocr_confirmation(
        self,
        state: EvidenceAgentState,
        action: str,
    ) -> Dict[str, Any]:
        image_ocr_confirmation = next(
            (
                item for item in state.get("pending_confirmations", [])
                if item.get("id") == "image_ocr"
            ),
            None,
        )
        confirmations = [
            item for item in state.get("pending_confirmations", [])
            if item.get("id") != "image_ocr"
        ]
        state["pending_confirmations"] = confirmations

        if action == "skip":
            state["last_confirmation"] = {"id": "image_ocr", "action": "skip"}
            return {
                "ok": True,
                "response": "已暂不执行图片 OCR。你仍然可以查看图片预览；后续需要时再点击 OCR 即可。",
            }

        items = list(state.get("image_evidence") or [])
        if not items:
            return {"ok": False, "response": "当前会话里还没有可 OCR 的图片。"}

        paths: set[str] | None = None
        if action == "recommended":
            paths = {
                str(path)
                for path in (image_ocr_confirmation or {}).get("recommended_paths", [])
                if path
            }
            if not paths:
                paths = None

        selected_indexes = self._select_image_indexes_for_ocr(items, paths)
        if not selected_indexes:
            return {"ok": False, "response": "当前没有可 OCR 的已解码图片。"}

        output_dir = Path(self.config.output_path) / "image_evidence"
        processed = 0
        failed = 0
        for index in selected_indexes:
            item = items[index]
            source_path = self._ocr_source_path(item)
            if not source_path:
                continue
            attachment = {
                "path": source_path,
                "name": item.get("source_name") or Path(str(source_path)).name,
                "msg_id": item.get("msg_id", ""),
                "time": item.get("time", ""),
                "sender": item.get("sender", ""),
                "kind": item.get("source_kind", ""),
                "type": Path(str(source_path)).suffix.lstrip(".") or "image",
            }
            refreshed = inspect_single_image(
                attachment,
                media_extractor=None,
                media_processor=self.media_processor,
                output_dir=output_dir,
                run_ocr=True,
            )
            for key, value in refreshed.items():
                if value not in ("", None):
                    item[key] = value
            if item.get("ocr_status") == "failed":
                failed += 1
            else:
                processed += 1

        ocr_results = [
            self._image_ocr_result_payload(items[index])
            for index in selected_indexes
            if index < len(items)
        ]
        state["image_evidence"] = items
        state["last_confirmation"] = {
            "id": "image_ocr",
            "action": action,
            "selected_indexes": selected_indexes,
            "processed": processed,
            "failed": failed,
            "ocr_results": ocr_results,
        }
        response = (
            f"图片 OCR 已处理 {processed} 张"
            + (f"，失败 {failed} 张" if failed else "")
            + "。下方只展示本次选择的图片和识别结果；OCR 文字仅供核对，关键内容建议结合原图人工确认。"
        )
        return {"ok": True, "response": response, "images": ocr_results}

    def _select_image_indexes_for_ocr(
        self,
        items: Iterable[Dict[str, Any]],
        paths: set[str] | None,
    ) -> list[int]:
        selected: list[int] = []
        for index, item in enumerate(items):
            decoded_path = self._ocr_source_path(item)
            if item.get("status") not in {"decoded", "thumbnail_decoded"}:
                continue
            if not decoded_path:
                continue
            if item.get("ocr_status") == "ok" and item.get("ocr_text"):
                continue
            if paths is not None and decoded_path not in paths:
                continue
            selected.append(index)
        return selected

    @staticmethod
    def _ocr_source_path(item: Dict[str, Any]) -> str:
        decoded_path = str(item.get("decoded_path") or "")
        if decoded_path:
            return decoded_path
        thumbnail = item.get("thumbnail_evidence") or {}
        if isinstance(thumbnail, dict):
            return str(thumbnail.get("decoded_path") or "")
        return ""

    def _image_ocr_result_payload(self, item: Dict[str, Any]) -> Dict[str, Any]:
        path = self._ocr_source_path(item)
        return {
            "path": path,
            "name": item.get("source_name") or (Path(path).name if path else "图片证据"),
            "time": item.get("time", ""),
            "sender": item.get("sender", ""),
            "status": item.get("status", ""),
            "ocr_status": item.get("ocr_status", ""),
            "ocr_text": item.get("ocr_text", ""),
            "ocr_error": item.get("ocr_error") or item.get("error") or "",
        }

    def _initial_state(self, thread_id: str) -> EvidenceAgentState:
        return {
            "thread_id": thread_id,
            "wechat_dir": self.config.wechat_dir or "",
            "errors": [],
        }

    def _load_state(self, thread_id: str) -> EvidenceAgentState:
        active_thread_id = self._normalize_thread_id(thread_id)
        if active_thread_id in self._sessions:
            return self._sessions[active_thread_id]

        state_path = self._state_path(active_thread_id)
        if state_path.exists():
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data["thread_id"] = active_thread_id
                    data["wechat_dir"] = self.config.wechat_dir or data.get("wechat_dir", "")
                    self._sessions[active_thread_id] = data
                    return data
            except Exception as exc:
                logger.warning("Failed to load graph agent state %s: %s", state_path, exc)

        state = self._initial_state(active_thread_id)
        self._sessions[active_thread_id] = state
        return state

    def _save_state(self, thread_id: str, state: EvidenceAgentState) -> None:
        state_path = self._state_path(thread_id)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._serializable_state(state)
        state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _state_path(self, thread_id: str) -> Path:
        name = self._normalize_thread_id(thread_id)
        return Path(self.config.output_path) / "agent_sessions" / f"{name}.json"

    def _normalize_thread_id(self, thread_id: Optional[str]) -> str:
        raw = str(thread_id or self.thread_id or "default").strip() or "default"
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)[:120] or "default"

    def _serializable_state(self, state: EvidenceAgentState) -> Dict[str, Any]:
        ignored = {"response", "errors", "user_input"}
        return {
            key: value
            for key, value in state.items()
            if key not in ignored
        }
