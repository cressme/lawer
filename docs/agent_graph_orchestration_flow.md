# Agent Graph Orchestration Flow

本文档用于说明微信证据助手第一期 Agent 编排。它对应当前代码中的 LangGraph 实现，方便产品、工程和后续调试时快速判断“用户一句话会走到哪里”。

## 代码位置

- 编排入口：`wechat_evidence_agent/agent_graph/graph.py`
- 节点实现：`wechat_evidence_agent/agent_graph/nodes.py`
- 状态结构：`wechat_evidence_agent/agent_graph/state.py`
- 材料整理：`wechat_evidence_agent/agent_graph/materials.py`
- 图片证据识别：`wechat_evidence_agent/agent_graph/image_evidence.py`
- Web 接口调用：`wechat_evidence_agent/web_client.py` 的 `/api/chat`
- 会话状态落盘：`output/agent_sessions/<session_id>.json`

## 第一阶段目标

第一期不是做一个流程表单，而是做一个以 LLM 判断意图为主的律师证据助手：

1. 理解律师输入。
2. 判断是否需要读取微信材料。
3. 自动解析联系人和聊天记录。
4. 提取文字、图片、附件等材料。
5. 在用户明确需要时，进入案情整理和证据链分析。
6. 对于闲聊、产品说明、一般法律常识、信息不足的案件咨询，不强行进入微信材料流程。

重要边界：当前登录微信账号通常是律师本人或律所工作微信；聊天联系人通常是客户、委托人或证据提供人。不能默认“当前微信账号”和“聊天联系人”就是纠纷双方。

## 当前节点

### planner

LLM 意图识别节点。

输入：

- 用户输入
- 当前是否已有材料
- 当前联系人
- 当前消息数量和图片数量

输出：

- `extract_only`：只查聊天、提取材料，不做法律分析
- `extract_and_analyze`：提取聊天后直接做案情/证据分析
- `analyze_current`：基于当前已有材料继续分析
- `clarify`：联系人缺失或候选不唯一，需要追问
- `answer`：普通说明性回答，不读微信

意图分层：

| 用户意图 | next_action | 后续节点 |
| --- | --- | --- |
| 闲聊、打招呼 | `answer` | `direct_answer` |
| 产品能力、怎么使用 | `answer` | `direct_answer` |
| 一般法律常识/法条解释 | `answer` | `direct_answer` |
| 案情咨询但事实不足 | `clarify` 或 `answer` | `response` 或 `direct_answer` |
| 查询某联系人聊天 | `extract_only` | 联系人解析、聊天提取、图片识别、材料概览 |
| 提取并分析某联系人聊天 | `extract_and_analyze` | 联系人解析、聊天提取、图片识别、分析 |
| 基于当前材料继续分析 | `analyze_current` | 分析 |

### contact_resolver

联系人解析节点。

职责：

- 根据备注名、昵称、微信号或 `wxid_` 查找联系人。
- 如果匹配不到，返回候选或错误说明。
- 如果唯一匹配，写入 `contact_id` 和 `contact_name`。

### chat_extraction

聊天提取节点。

职责：

- 根据 `contact_id` 读取微信聊天记录。
- 生成材料统计：
  - 消息总数
  - 时间范围
  - 消息类型统计
  - 文字预览
  - 图片消息列表
  - 附件列表

### image_inspection

图片证据识别节点。

职责：

- 对图片附件和缩略图进行解码。
- 尝试生成可预览图片。
- 尝试 OCR。
- 保留失败原因，例如新版微信图片 AES key 尚未获取。

### direct_answer

通用回答节点。

适用场景：

- 用户闲聊或打招呼。
- 用户问软件能力、操作步骤。
- 用户问一般法律常识或法条含义。
- 用户给出的案情信息不足，需要先引导补充。

职责：

- 不读取微信材料。
- 不假装已经分析证据。
- 对法律问题只给一般性说明和工作思路。
- 如果缺少必要事实，用 1-3 个问题引导用户补充。

### material_summary

材料概览节点。

适用场景：

- 用户只是说“查一下我和某人的聊天”
- 用户只是想先看看材料是否提取成功

职责：

- 返回提取概览。
- 展示消息数量、时间范围、图片数量、OCR 情况。
- 给出少量文字片段。
- 提醒用户下一步可以进入证据链分析。

该节点不做法律结论，不猜案件事实。

### analysis

案情和证据分析节点。

适用场景：

- 用户明确说“分析”
- 用户要求“整理证据链”
- 用户问“能证明什么”
- 用户要求“按借款/合同/违约等纠纷整理”

职责：

- 基于已经提取的材料进行第一期案情整理。
- 区分沟通关系和案件关系。
- 输出可能法律关系、关键时间线、证据价值、证据缺口和下一步动作。

分析节点必须先回答用户的具体问题，不能无条件输出完整案情报告。

当前特殊分流：

- 用户问“我和某人的关系”“能看出是什么关系吗”“是不是客户/朋友/同事/合作方”等，进入关系判断回答模式。
- 关系判断模式只输出结论、依据、不能确认的地方和下一步建议，不输出完整案情报告。
- 只有用户明确要求“完整分析报告”“案情整理”“证据链分析”时，才输出完整案情/证据分析结构。

### response

最终响应节点。

职责：

- 如果有错误，优先输出错误。
- 如果已有节点生成了 `response`，直接返回。
- 否则返回兜底完成提示。

## 会话状态

前端左侧 Chat history 是浏览器本地历史，用于展示对话内容。Agent 是否“记得已经提取过材料”，取决于后端 LangGraph state。

当前实现中，前端每次调用 `/api/chat` 都会带上当前 `session_id`，后端用它作为 LangGraph `thread_id`：

```text
browser chat session id
  -> /api/chat session_id
  -> LangGraphEvidenceAgent.chat(thread_id=session_id)
  -> output/agent_sessions/<session_id>.json
```

这样切换回某条历史会话后继续问“分析刚才那段”“筛一下还款承诺”，后端可以加载该会话之前提取过的联系人、消息、图片识别结果。

如果用户新建对话或重置对话，只清理当前 `session_id` 对应的后端状态，不影响其他历史会话。

日志中会记录两类关键信息：

- `Web chat request`：前端传入的 `session_id` 和用户输入。
- `Planner decision` / `Graph agent turn`：planner 决定的动作、联系人、当前是否已有材料、消息数量和图片数量。

## 路由图

### 只查聊天

用户示例：

- “查一下我与明文的对话”
- “看看我和蔚青的聊天”
- “调取张三的微信记录”

流程：

```text
START
  -> planner
  -> contact_resolver
  -> chat_extraction
  -> image_inspection
  -> material_summary
  -> response
  -> END
```

结果：

- 返回材料提取概览。
- 不直接进入法律分析。

### 提取并分析

用户示例：

- “分析一下我和明文的聊天能证明什么”
- “按借款纠纷整理蔚青的证据链”
- “提取张三聊天并分析案情”

流程：

```text
START
  -> planner
  -> contact_resolver
  -> chat_extraction
  -> image_inspection
  -> analysis
  -> response
  -> END
```

结果：

- 提取文字和图片材料。
- 进入第一期案情整理和证据分析。

### 基于当前材料继续分析

用户示例：

- “继续分析”
- “按合同纠纷整理”
- “筛一下有没有还款承诺”

流程：

```text
START
  -> planner
  -> analysis
  -> response
  -> END
```

前提：

- 当前会话 state 中已经有 `messages`。

### 直接回答 / 引导补充

用户示例：

- “你好”
- “你能做什么”
- “民间借贷诉讼时效是多久”
- “我这个案子能赢吗”

流程：

```text
START
  -> planner
  -> direct_answer
  -> response
  -> END
```

如果用户问“能不能赢”但没有事实和证据，系统不应硬分析，而应追问：

- 案由或争议类型
- 已有证据材料
- 想证明的核心事实

## 设计原则

1. LLM 负责判断意图，但不能让所有意图都直接进入分析。
2. “查材料”和“分析材料”必须分开。
3. 系统先自动找微信目录和联系人，找不到再提示用户。
4. 未确认案件主体时，不把律师账号和聊天联系人当作纠纷双方。
5. 图片证据即使解码失败，也要保留失败原因，让用户知道下一步怎么做。
6. 闲聊、法律常识、产品说明、信息不足的案件咨询，走通用回答或澄清，不进入微信数据库提取。

## 后续增强方向

1. 增加真实进度事件流，让前端显示正在解析联系人、提取聊天、识别图片、生成分析。
2. 增加查询式材料筛选，例如按关键词、时间范围、金额、转账、承诺等重排材料包。
3. 将材料摘要从“前若干条预览”升级为“时间线 + 关键事实 + 附件索引”。
4. 对每次工具调用记录 trace，方便定位某一轮对话为什么走了某条路径。
