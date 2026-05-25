"""LangGraph-powered evidence agent."""

from __future__ import annotations

from typing import Any, Dict, Optional

from langgraph.graph import END, START, StateGraph

from ..config import Config
from .model import ChatModelAdapter
from .nodes import (
    make_analysis_node,
    make_chat_extraction_node,
    make_contact_resolver_node,
    make_image_inspection_node,
    make_planner_node,
    response_node,
    route_after_contact,
    route_after_extract,
    route_after_planner,
)
from .state import EvidenceAgentState


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
        self.model = ChatModelAdapter(
            api_key=config.openai_api_key,
            model=config.openai_model,
            base_url=config.openai_base_url,
            temperature=0.2,
        )
        self._state: EvidenceAgentState = {
            "thread_id": thread_id,
            "wechat_dir": config.wechat_dir or "",
            "errors": [],
        }
        self.graph = self._build_graph()

    def chat(self, user_input: str) -> str:
        if not user_input or not user_input.strip():
            return "请输入您的问题或指令。"
        if not self.config.openai_api_key:
            return "还没有配置大模型 API Key，请先在左侧配置。"

        input_state: EvidenceAgentState = {
            **self._state,
            "user_input": user_input.strip(),
            "response": "",
            "errors": [],
        }
        result = self.graph.invoke(input_state)
        self._state.update(result)
        return str(result.get("response") or "处理完成。")

    def reset(self) -> None:
        self._state = {
            "thread_id": self.thread_id,
            "wechat_dir": self.config.wechat_dir or "",
            "errors": [],
        }

    def configure(self, config: Config) -> None:
        self.config = config
        self.model = ChatModelAdapter(
            api_key=config.openai_api_key,
            model=config.openai_model,
            base_url=config.openai_base_url,
            temperature=0.2,
        )
        self._state["wechat_dir"] = config.wechat_dir or ""
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
        builder.add_node("response", response_node)

        builder.add_edge(START, "planner")
        builder.add_conditional_edges(
            "planner",
            route_after_planner,
            {
                "contact_resolver": "contact_resolver",
                "analysis": "analysis",
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
                "analysis": "analysis",
                "response": "response",
            },
        )
        builder.add_edge("image_inspection", "analysis")
        builder.add_edge("analysis", "response")
        builder.add_edge("response", END)
        return builder.compile()

    @property
    def state(self) -> Dict[str, Any]:
        return dict(self._state)
