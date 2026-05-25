# 微信证据助手 LangGraph Agent 编排设计 Spec

日期：2026-05-22
状态：Draft v0.1
作者：Codex + 产品共创

## 1. 我们真正想做什么

这个产品不应该只是“一个能调用工具的聊天框”。律师用户的真实诉求是：

- 我说一句自然语言，你能理解我要处理哪个案件材料。
- 你能自己判断要不要读微信数据库、搜联系人、提取聊天、找图片文件、做证据分析。
- 你能记住当前案件上下文，而不是每一句都重新开始。
- 你能把聊天、图片、视频、文件附件组织成可交付的证据包。
- 你能在关键动作前让我确认，比如导出、复制附件、生成最终文档。
- 你能像一个可靠的办案助理，而不是像一个“问答机器人”。

所以目标是：

> LLM 主导意图判断和下一步决策，LangGraph 负责状态、节点编排、可恢复执行、人工确认和审计轨迹，底层工具负责确定性读取和生成。

## 2. 为什么不要继续手搓

当前实现已经出现了几个信号：

- GUI 里有正则 shortcut，LLM agent 里又有 ReAct loop，两套逻辑割裂。
- 工具返回大段中文文本，下一步很难基于结构化材料继续处理。
- 没有强工作区状态，只能靠聊天历史隐式记忆。
- DeepSeek thinking mode、tool_calls、reasoning_content、工作区 context 都要我们自己拼，越补越脆。
- “继续分析刚才那段”这种自然对话，需要持久状态和明确节点，而不是靠 prompt 祈祷。

LangGraph 的价值正好在这里：官方文档说明它提供图状态、checkpoint 持久化、human-in-the-loop、会话记忆、故障恢复等能力。我们应该用成熟框架承接这些工程复杂度，而不是继续把 `EvidenceAgent.chat()` 写成越来越大的状态机。

参考：

- LangGraph Python Persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph Human-in-the-loop: https://docs.langchain.com/oss/python/langgraph/human-in-the-loop
- LangGraph JS Checkpointing/Persistence Guide: https://langgraphjs.guide/persistence/

## 3. 设计原则

1. LLM 为主，不是流程为主
   - LLM 负责理解用户意图、制定小计划、选择工具或节点。
   - 图不是一堆 if/else，而是给 LLM 可调用、可恢复、可审计的工作台。

2. 状态显式化
   - 当前联系人、已提取消息、附件清单、证据标记、分析结果都进入 `EvidenceAgentState`。
   - 不再把关键上下文藏在自然语言历史里。

3. 工具结构化
   - 工具返回 Python dict / Pydantic model，再由 LLM 生成面向律师的解释。
   - 中文文本用于展示，不用于系统内部传递事实。

4. 高风险动作需要确认
   - 导出证据包、复制/转换附件、生成最终文档、批量哈希等动作进入 HITL 节点。
   - 用户确认后继续图执行。

5. 案件工作区优先
   - 一次聊天会话对应一个 thread / case workspace。
   - 允许后续恢复、继续分析、补充联系人、重新导出。

6. 先兼容 DeepSeek
   - 使用 OpenAI-compatible chat model，但要适配 DeepSeek thinking mode 的 `reasoning_content`。
   - 如果 LangChain wrapper 对 DeepSeek thinking 字段支持不稳，保留薄封装层。

## 4. 目标架构

```text
Browser UI
  |
  | /api/chat
  v
AgentSessionService
  |
  | graph.invoke / graph.stream
  v
LangGraph Evidence Graph
  |
  +-- Planner Node              LLM 判断意图和下一步
  +-- Contact Resolver Node     搜联系人 / 消歧
  +-- Chat Extraction Node      读取微信聊天、写入工作区
  +-- Attachment Node           定位图片、视频、文件
  +-- Search Node               当前聊天内关键词搜索
  +-- Evidence Analysis Node    LLM 证据链分析
  +-- Export Plan Node          生成导出计划
  +-- Human Review Node         用户确认敏感动作
  +-- Export Execute Node       生成 docx/html/pdf/附件包
  +-- Response Node             面向律师的最终回复
  |
  v
Checkpointer / Case Workspace
  |
  +-- SQLite checkpointer 本地持久化
  +-- artifacts/ 案件材料与导出文件
```

## 5. 核心状态模型

建议新增：

`wechat_evidence_agent/agent_graph/state.py`

```python
from typing import Annotated, Literal, TypedDict
from langgraph.graph.message import add_messages

class EvidenceAgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    thread_id: str
    case_id: str

    user_intent: str
    plan: list[dict]
    next_action: str

    wechat_dir: str
    current_contact_query: str
    current_contact_id: str
    current_contact_name: str
    contact_candidates: list[dict]

    chat_bundle_id: str
    message_count: int
    time_range: dict
    message_type_counts: dict

    attachments: list[dict]
    attachment_counts: dict
    unresolved_attachments: list[dict]

    search_results: list[dict]
    evidence_marks: list[dict]
    timeline: list[dict]
    analysis: dict

    pending_confirmation: dict
    export_plan: dict
    export_result: dict

    errors: list[dict]
```

重要点：

- `messages` 是对话消息。
- `chat_bundle_id` 指向本地 artifact，不把 3000 条聊天塞进 LLM 上下文。
- `attachments` 保存清单，但大文件只保存路径、大小、哈希、类型。
- `pending_confirmation` 用于 HITL 暂停。

## 6. 节点设计

### 6.1 Planner Node

职责：

- 读用户消息、当前 state、可用工具说明。
- 判断 intent：
  - `extract_chat`
  - `search_current_chat`
  - `analyze_evidence`
  - `export_evidence`
  - `configure`
  - `clarify`
  - `smalltalk`
- 输出下一步节点。

关键要求：

- 用户说“查我本地跟 X 的聊天”：直接进入 `contact_resolver` 或 `chat_extraction`。
- 用户说“继续分析刚才那段”：如果已有 `chat_bundle_id`，进入 `evidence_analysis`。
- 用户说“导出”：进入 `export_plan`，再进 `human_review`。

### 6.2 Contact Resolver Node

职责：

- 调用现有 `WeChatDBExtractor.get_contacts()`。
- 根据备注、昵称、alias、wxid 做精确/模糊匹配。
- 单一高置信候选直接写入 state。
- 多候选进入 `clarify_response`，让用户选。

输出：

```json
{
  "current_contact_id": "wxid_xxx",
  "current_contact_name": "蔚青",
  "contact_candidates": []
}
```

### 6.3 Chat Extraction Node

职责：

- 调用现有 `get_messages(contact_id=...)`。
- 将完整消息保存为 artifact：
  - `output/cases/{case_id}/bundles/{bundle_id}/messages.jsonl`
  - `summary.json`
- state 只保存概览。

输出：

```json
{
  "chat_bundle_id": "bundle_20260522_193000",
  "message_count": 356,
  "time_range": {"start": "...", "end": "..."},
  "message_type_counts": {"text": 251, "image": 32}
}
```

### 6.4 Attachment Node

职责：

- 基于当前 bundle 定位图片、视频、文件。
- 记录本地路径、大小、mime、哈希。
- 暂不做语音转写，但记录语音数量和占位。

未来可扩展：

- 图片 `.dat` 转换。
- 视频复制。
- 文件哈希。
- OCR。

### 6.5 Evidence Analysis Node

职责：

- 从 bundle 中抽取可控上下文：
  - 时间线窗口
  - 关键词命中
  - 附件消息
  - 撤回/转账/承诺/催告等特征
- 让 LLM 做证据链分析。

输出结构：

```json
{
  "case_facts": [],
  "timeline": [],
  "key_evidence": [],
  "weak_points": [],
  "next_questions": []
}
```

### 6.6 Export Plan Node

职责：

- 根据当前材料生成导出计划。
- 明确会导出哪些东西：
  - 聊天 HTML / DOCX
  - 附件清单
  - 图片/视频/文件复制
  - 哈希校验表
  - 证据目录

不直接执行，先进入 `human_review`。

### 6.7 Human Review Node

职责：

- 暂停图执行，返回给 UI 一个确认卡片。
- 用户可：
  - 批准
  - 修改导出范围
  - 取消

LangGraph 的 interrupt/checkpoint 能让这个暂停状态可恢复。

### 6.8 Export Execute Node

职责：

- 执行导出计划。
- 生成 artifact。
- 写入 `export_result`。

### 6.9 Response Node

职责：

- 把结构化 state 变成律师能读懂的中文回复。
- 不做底层数据读取。
- 不虚构工具结果。

## 7. 工具设计

先保留现有底层类，但把 agent tool 从“返回中文字符串”改成“返回结构化对象”。

建议新增：

`wechat_evidence_agent/agent_graph/tools.py`

核心工具：

- `resolve_contacts(query: str) -> ContactResolution`
- `extract_chat_bundle(contact_id: str, start_date?: str, end_date?: str) -> ChatBundle`
- `locate_attachments(bundle_id: str) -> AttachmentResult`
- `search_bundle(bundle_id: str, keyword: str) -> SearchResult`
- `build_timeline(bundle_id: str) -> TimelineResult`
- `draft_evidence_analysis(bundle_id: str, focus?: str) -> AnalysisDraft`
- `plan_export(bundle_id: str, format: str) -> ExportPlan`
- `execute_export(export_plan_id: str) -> ExportResult`

重要：工具层不应该负责“说人话”，只负责返回事实。

## 8. UI 配合方式

前端不需要理解 LangGraph DAG，但应该能展示 agent 运行状态：

- 正在理解意图
- 正在查找联系人
- 正在读取聊天记录
- 正在定位图片/文件/视频
- 等待你确认导出计划
- 正在生成文档

建议 `/api/chat` 支持 stream：

```text
event: node_start
data: {"node": "chat_extraction", "label": "正在读取聊天记录"}

event: node_end
data: {"node": "chat_extraction", "summary": "已读取 356 条消息"}

event: final
data: {"response": "..."}
```

第一版也可以不做 SSE，先同步返回，但服务端内部使用 graph state。

## 9. 持久化策略

本地 exe 面向普通律师用户，所以第一阶段用 SQLite：

- `output/state/checkpoints.sqlite`
- `output/cases/{case_id}/`
- `output/cases/{case_id}/bundles/{bundle_id}/messages.jsonl`
- `output/cases/{case_id}/attachments/`
- `output/cases/{case_id}/exports/`

LangGraph checkpointer 负责图状态。

Artifact 文件负责大材料。

不要把完整聊天塞进 checkpoint，否则会慢、会大、也不适合 LLM 上下文。

## 10. DeepSeek 适配

问题：

- DeepSeek thinking mode 要求历史里保留 `reasoning_content`。
- LangChain 的标准 message abstraction 未必完整透传该字段。

方案：

1. 优先测试 `langchain-openai` + `ChatOpenAI(base_url=...)` 是否支持 DeepSeek thinking mode。
2. 如果不稳定，写一个 `DeepSeekChatModelAdapter`：
   - 复用 OpenAI SDK。
   - 保留 `reasoning_content`。
   - 转换 LangGraph messages。
3. 在 graph 层只依赖统一模型接口。

## 11. 迁移路线

### Phase 0：Spec 与依赖确认

- 加入依赖：
  - `langgraph`
  - `langchain-core`
  - `langchain-openai`
  - `pydantic`
- 写一个最小 graph demo。

### Phase 1：并行引入 LangGraph，不替换旧入口

新增目录：

```text
wechat_evidence_agent/agent_graph/
  __init__.py
  state.py
  graph.py
  nodes.py
  tools.py
  model.py
  storage.py
```

新增 `LangGraphEvidenceAgent`，但旧 `EvidenceAgent` 先保留。

### Phase 2：打通最小主链路

目标对话：

```text
用户：帮我查一下我本地跟蔚青的聊天
系统：
  planner -> contact_resolver -> chat_extraction -> attachment_node -> response
```

验收：

- LLM 自己决定调用提取。
- 读到 356 条消息。
- 附件统计进入 state。
- 最终回复不再问路径。

### Phase 3：打通承接分析

目标对话：

```text
用户：继续分析这段聊天里的证据链
系统：
  planner -> evidence_analysis -> response
```

验收：

- 不重新问联系人。
- 不重新读完整数据库，优先使用 bundle。
- 能给出时间线、关键证据、缺口。

### Phase 4：导出计划 + 人工确认

目标对话：

```text
用户：导出证据包
系统：展示导出计划，等待确认
用户：确认
系统：执行导出
```

验收：

- 未确认不执行文件复制/生成。
- 确认后生成导出目录。
- 结果可恢复。

### Phase 5：替换 GUI 入口

- `/api/chat` 调用 LangGraph agent。
- 旧 `EvidenceAgent` 降级为兼容层或删除。

## 12. 第一版最小代码形态

`graph.py` 伪代码：

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

def build_evidence_graph(deps):
    graph = StateGraph(EvidenceAgentState)

    graph.add_node("planner", planner_node(deps.llm))
    graph.add_node("contact_resolver", contact_resolver_node(deps.extractor))
    graph.add_node("chat_extraction", chat_extraction_node(deps.extractor, deps.storage))
    graph.add_node("attachment_index", attachment_index_node(deps.extractor, deps.storage))
    graph.add_node("analysis", analysis_node(deps.llm, deps.storage))
    graph.add_node("export_plan", export_plan_node(deps.storage))
    graph.add_node("human_review", human_review_node())
    graph.add_node("export_execute", export_execute_node(deps.exporter, deps.storage))
    graph.add_node("response", response_node(deps.llm))

    graph.set_entry_point("planner")
    graph.add_conditional_edges("planner", route_from_plan)
    graph.add_edge("contact_resolver", "chat_extraction")
    graph.add_edge("chat_extraction", "attachment_index")
    graph.add_edge("attachment_index", "response")
    graph.add_edge("analysis", "response")
    graph.add_edge("export_plan", "human_review")
    graph.add_conditional_edges("human_review", route_review)
    graph.add_edge("export_execute", "response")
    graph.add_edge("response", END)

    return graph.compile(checkpointer=SqliteSaver.from_conn_string("output/state/checkpoints.sqlite"))
```

## 13. 主要风险

1. DeepSeek thinking mode 与 LangChain message 格式兼容
   - 需要最先 spike。

2. 大聊天记录上下文爆炸
   - 必须 artifact 化，LLM 只看摘要和检索片段。

3. 附件格式复杂
   - 图片 `.dat`、视频、文件缓存路径各版本不同，需要单独 extractor 测试。

4. 普通律师用户不理解 agent 状态
   - UI 要展示“正在做什么”和“为什么卡住”。

5. exe 体积和依赖
   - LangGraph/LangChain 会增加包体积，但相比 OCR/Whisper/Paddle 不是最大头。

## 14. 我们下一步怎么一起做

建议下一步不是继续修 prompt，而是做一个 spike：

1. 安装 LangGraph 依赖到当前 venv。
2. 新建 `agent_graph/`。
3. 写最小 `EvidenceAgentState`。
4. 写 `planner -> contact_resolver -> chat_extraction -> response` 四节点。
5. 只接一个测试入口，不动 GUI。
6. 用真实句子测试：
   - “帮我查一下我本地跟蔚青的聊天”
   - “继续分析这段聊天里的证据链”
7. 如果 DeepSeek thinking mode 在 LangChain 下不稳，就先写模型 adapter。

这一步成功后，再把 GUI 切过去。这样我们不是推翻重来，而是把成熟编排层并行长出来，等它能跑通真实任务，再替换旧 agent。

