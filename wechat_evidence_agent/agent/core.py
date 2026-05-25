"""
证据助手 Agent 核心模块

基于 OpenAI ChatCompletion API（含 function calling）实现的
法律证据分析智能助手。负责对话管理、工具调度和上下文维护。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .memory import CaseMemory
from .tools import OPENAI_TOOLS, ToolExecutor

logger = logging.getLogger(__name__)


def _strip_invalid_unicode(value: Any) -> Any:
    """Remove lone surrogate characters that JSON/HTTP clients cannot encode."""
    if isinstance(value, str):
        return value.encode("utf-8", "ignore").decode("utf-8")
    if isinstance(value, list):
        return [_strip_invalid_unicode(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_invalid_unicode(item)
            for key, item in value.items()
        }
    return value


# ---------------------------------------------------------------------------
# 系统提示词
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是一位面向律师用户的微信电子证据办案助手。你的核心任务不是闲聊，而是把本地微信聊天记录、图片、视频、文件等材料提取出来，组织成可审查、可分析、可导出的证据材料。

【你的专业背景】
- 精通《中华人民共和国民事诉讼法》中关于电子数据证据的相关规定
- 熟悉《最高人民法院关于民事诉讼证据的若干规定》对电子证据的要求
- 了解微信聊天记录作为电子证据的采信标准和司法实践
- 掌握常见民事纠纷（借贷、合同、劳动争议等）的证据要求

【你的工作原则】
1. 证据优先：用户要求“查聊天、提取聊天、看某联系人、找本地记录、完整内容、证据分析”时，先调用工具读取本地数据，不要先问案情。
2. 完整材料：提取聊天时要默认关注文字、图片、视频、文件附件。语音可提示暂未处理，不要把附件简化成一个占位符。
3. 少说废话：不要反复解释“我不能访问本地文件”。只要用户已配置微信目录，就直接尝试工具；失败时报告真实错误和下一步。
4. 工作流意识：一次提取后，应主动给出“已提取范围、附件命中情况、可继续做的分析/导出动作”，而不是只贴聊天流水。
5. 法律视角：分析时围绕待证事实、时间线、付款/交付/承诺/违约/催告/确认等证据要点组织，不要泛泛聊天。
6. 谨慎追问：只有在无法确定联系人、案由或分析重点会明显影响结果时才追问；能先做的提取动作必须先做。
7. LLM 主导：你负责判断用户意图、制定下一步、选择工具。不要等用户使用固定命令，也不要依赖固定话术。
8. 角色边界：当前登录微信账号通常是律师本人或律所工作微信；聊天联系人通常是客户、委托人、证据提供人或案件相关人员。不要默认“当前微信账号与联系人”就是纠纷双方，也不要把律师当作案件当事人。真实纠纷主体往往是客户/委托人与第三方，必须从聊天内容中识别；识别不出来就标注“待确认”。

【证据审查要点】
- 完整性：证据链是否完整，关键环节有无缺失
- 真实性：消息是否有被篡改的风险（如撤回后重发等）
- 关联性：证据与待证事实是否具有关联性
- 合法性：证据的取得方式是否合法
- 时间连续性：关键事件的时间线是否连贯

【默认工作流】
1. 定位联系人：用户给出姓名、备注、昵称或 wxid 时，优先 list_contacts / extract_chat。
2. 完整提取：提取文字消息，同时保留图片、视频、文件附件路径、大小和类型。
3. 汇总材料：说明消息总数、附件数量、图片/文件/视频命中情况、未处理材料（如语音）。
4. 证据分析：用户要求分析时，先区分“沟通关系”（律师账号与联系人）和“案件关系”（客户/委托人、案件相对方、第三方、待确认人员），再基于已提取材料梳理时间线和关键证据，最后指出缺口。
5. 导出准备：用户要求给律师/法院使用时，建议生成证据清单、附件目录、哈希校验和导出文档。

【工具使用要求】
- 当用户提供本地微信数据目录路径（例如 WeChat Files 或 xwechat_files）时，必须先调用 set_wechat_dir 工具设置目录，然后再调用 list_contacts 或 extract_chat。
- 不要声称自己无法访问用户已经提供的本机路径；应优先通过工具尝试读取，并把工具返回的真实错误告诉用户。
- 当用户说“所有聊天内容、完整聊天、聊天证据、证据材料”时，extract_chat 的结果应视为第一步；随后要提醒还可以继续做“证据分析/导出/附件复制/哈希校验”。
- 当用户说“继续、刚才那段、当前材料、分析一下、导出”等承接性表达时，先根据当前工作区判断是否已有聊天材料；必要时调用 get_workspace。
- search_messages、summarize_chat、get_timeline 如果用户没有重新指定联系人，可以基于当前工作区继续处理。
- 如果运行时状态显示微信数据目录已配置，用户说“查我本地跟某人的聊天”时，不要再询问目录，直接调用 extract_chat，contact 填该联系人姓名或备注。
- 不要只输出寒暄或泛泛建议；每次回复都应推动案件材料处理向前一步。

请始终使用中文回复。"""


# ---------------------------------------------------------------------------
# Agent 核心类
# ---------------------------------------------------------------------------

class EvidenceAgent:
    """
    微信证据分析 Agent

    基于 LLM 的对话式证据分析助手，通过 function calling
    机制调度底层工具完成证据提取、搜索、标记、分析等操作。

    用法示例::

        agent = EvidenceAgent(config={
            "api_key": "sk-xxx",
            "model": "gpt-4o",
        })
        response = agent.chat("帮我提取与张三的聊天记录")
        print(response)
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """
        初始化证据分析 Agent。

        Args:
            config: 配置字典，支持以下字段：
                - api_key (str): OpenAI API 密钥，必填。
                - model (str): 模型名称，默认 "gpt-4o"。
                - base_url (str): API 基础 URL，可选（用于兼容其他服务商）。
                - temperature (float): 生成温度，默认 0.3。
                - max_tokens (int): 最大生成 token 数，默认 4096。
                - max_tool_rounds (int): 单次对话中最大工具调用轮数，默认 10。

        Raises:
            ValueError: 缺少必要配置项。
        """
        self._model: str = config.get("model", "gpt-4o")
        self._temperature: float = config.get("temperature", 0.3)
        self._max_tokens: int = config.get("max_tokens", 4096)
        self._max_tool_rounds: int = config.get("max_tool_rounds", 10)

        # 初始化 OpenAI 客户端
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("需要安装 openai 库: pip install openai")

        self._openai_cls = OpenAI
        self._client = None
        self._api_key_configured = False
        self.configure_llm(
            api_key=config.get("api_key") or "",
            model=self._model,
            base_url=config.get("base_url"),
        )

        # 对话历史
        self._messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        # 案件记忆
        self._memory = CaseMemory()

        # 工具执行器（底层模块可后续注入）
        self._tool_executor = ToolExecutor(memory=self._memory)
        self._workspace_context_index: Optional[int] = None

        logger.info(
            "EvidenceAgent 初始化完成: model=%s, temperature=%.1f",
            self._model, self._temperature,
        )

    def configure_llm(
        self,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        """Update the LLM client without rebuilding the whole application."""
        api_key = api_key or ""
        self._api_key_configured = bool(api_key)
        client_kwargs: Dict[str, Any] = {
            "api_key": api_key or "__missing_api_key__",
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = self._openai_cls(**client_kwargs)
        if model:
            self._model = model

    # ------------------------------------------------------------------
    # 模块注入
    # ------------------------------------------------------------------

    def set_modules(
        self,
        extractor: Any = None,
        processor: Any = None,
        evidence_generator: Any = None,
        analyzer: Any = None,
    ) -> None:
        """
        注入底层功能模块。

        Args:
            extractor: 数据提取层实例。
            processor: 数据处理层实例。
            evidence_generator: 证据生成器实例。
            analyzer: 案件分析器实例。
        """
        if extractor is not None:
            self._tool_executor.extractor = extractor
        if processor is not None:
            self._tool_executor.processor = processor
        if evidence_generator is not None:
            self._tool_executor.evidence_generator = evidence_generator
        if analyzer is not None:
            self._tool_executor.analyzer = analyzer
        logger.info("底层模块已注入")

    # ------------------------------------------------------------------
    # 核心对话方法
    # ------------------------------------------------------------------

    def chat(self, user_input: str) -> str:
        """
        处理用户输入并返回 Agent 回复。

        实现 ReAct 风格的工具调用循环：
        1. 将用户输入追加到对话历史
        2. 调用 LLM 获取回复
        3. 如果 LLM 返回工具调用，执行工具并将结果追加到历史
        4. 重复步骤 2-3 直到 LLM 返回文本回复
        5. 返回最终文本回复

        Args:
            user_input: 用户的自然语言输入。

        Returns:
            Agent 的文本回复。
        """
        if not user_input or not user_input.strip():
            return "请输入您的问题或指令。"

        if not self._api_key_configured:
            return (
                "还没有配置 LLM API Key。请在配置文件中填写 openai_api_key，"
                "或设置 OPENAI_API_KEY 环境变量；DeepSeek 可同时设置 "
                "openai_base_url=https://api.deepseek.com 和 "
                "openai_model=deepseek-chat。"
            )

        # 追加用户消息
        self._refresh_workspace_context()
        self._messages.append({
            "role": "user",
            "content": _strip_invalid_unicode(user_input),
        })

        # 工具调用循环
        for round_idx in range(self._max_tool_rounds):
            try:
                response = self._call_llm()
            except Exception as e:
                error_msg = f"调用语言模型时出错：{e}"
                logger.error(error_msg, exc_info=True)
                return error_msg

            message = response.choices[0].message

            # 情况一：LLM 返回纯文本回复，对话结束
            if message.tool_calls is None or len(message.tool_calls) == 0:
                assistant_content = message.content or ""
                assistant_msg = {
                    "role": "assistant",
                    "content": assistant_content,
                }
                self._attach_reasoning_content(assistant_msg, message)
                self._messages.append(assistant_msg)
                return assistant_content

            # 情况二：LLM 请求工具调用
            # 先将 assistant 消息（含 tool_calls）追加到历史
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ],
            }
            self._attach_reasoning_content(assistant_msg, message)
            self._messages.append(assistant_msg)

            # 执行每个工具调用
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}
                    logger.warning(
                        "工具参数解析失败: %s", tool_call.function.arguments
                    )

                logger.info(
                    "Agent 调用工具: %s (round %d), 参数: %s",
                    tool_name, round_idx + 1, arguments,
                )

                # 执行工具
                tool_result = self._tool_executor.execute(tool_name, arguments)

                # 将工具结果追加到历史
                self._messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

        # 达到最大工具调用轮数，强制结束
        logger.warning("达到最大工具调用轮数 (%d)", self._max_tool_rounds)
        return "抱歉，处理过程过于复杂。请尝试将问题拆解为更具体的步骤。"

    @staticmethod
    def _attach_reasoning_content(
        assistant_msg: Dict[str, Any],
        message: Any,
    ) -> None:
        """Preserve DeepSeek thinking-mode reasoning_content across turns."""
        reasoning_content = getattr(message, "reasoning_content", None)
        if reasoning_content:
            assistant_msg["reasoning_content"] = reasoning_content

    # ------------------------------------------------------------------
    # LLM 调用
    # ------------------------------------------------------------------

    def _call_llm(self) -> Any:
        """
        调用 OpenAI ChatCompletion API。

        Returns:
            API 响应对象。
        """
        return self._client.chat.completions.create(
            model=self._model,
            messages=_strip_invalid_unicode(self._messages),
            tools=OPENAI_TOOLS,
            tool_choice="auto",
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

    def _refresh_workspace_context(self) -> None:
        """Inject a compact, replaceable workspace summary for the planner."""
        context = self._tool_executor.get_planner_context()
        message = {
            "role": "system",
            "content": context,
        }
        if self._workspace_context_index is not None:
            if self._workspace_context_index < len(self._messages):
                self._messages[self._workspace_context_index] = message
                return
            self._workspace_context_index = None
        self._messages.insert(1, message)
        self._workspace_context_index = 1

    # ------------------------------------------------------------------
    # 对话管理
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """
        重置对话历史，保留系统提示词。

        不会清除案件记忆（CaseMemory），如需同时重置记忆请调用
        ``self.memory.reset()``。
        """
        self._messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
        self._workspace_context_index = None
        self._tool_executor.workspace.reset()
        logger.info("对话历史已重置")

    def set_context(self, case_info: Dict[str, Any]) -> None:
        """
        设置案件上下文信息。

        将案件信息存入记忆，并在系统提示词中注入上下文摘要，
        帮助 Agent 在后续对话中保持案件背景意识。

        Args:
            case_info: 案件信息字典，支持字段：
                       case_type, plaintiff, defendant, claim_amount,
                       court, case_number, key_dates, description 等。
        """
        self._memory.set_case_info(**case_info)

        # 在对话历史中注入上下文
        context_summary = self._memory.get_case_summary()
        context_msg = {
            "role": "system",
            "content": f"当前案件上下文：\n{context_summary}",
        }

        insert_at = 2 if self._workspace_context_index == 1 else 1
        if (
            len(self._messages) > insert_at
            and self._messages[insert_at].get("role") == "system"
            and str(self._messages[insert_at].get("content", "")).startswith("当前案件上下文")
        ):
            self._messages[insert_at] = context_msg
        else:
            self._messages.insert(insert_at, context_msg)

        logger.info("案件上下文已设置")

    def get_history(self) -> List[Dict[str, Any]]:
        """
        获取完整对话历史。

        Returns:
            消息字典列表，包含 role、content 等字段。
        """
        return list(self._messages)

    # ------------------------------------------------------------------
    # 属性访问
    # ------------------------------------------------------------------

    @property
    def memory(self) -> CaseMemory:
        """获取案件记忆管理器。"""
        return self._memory

    @property
    def tool_executor(self) -> ToolExecutor:
        """获取工具执行器。"""
        return self._tool_executor

    def __repr__(self) -> str:
        msg_count = len(self._messages) - 1  # 排除系统提示词
        return (
            f"<EvidenceAgent model={self._model!r} "
            f"messages={msg_count} "
            f"memory={self._memory!r}>"
        )
