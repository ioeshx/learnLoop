# 高级 Agent 技术在 LearnLoop 中的应用设计

本文把 [Agent 研究与工业实践的主要技术方向](research-and-industry-directions.md) 映射到 LearnLoop。目标是把当前以固定 LangGraph 为主的 LLM 工作流，逐步升级为可规划、可行动、可记忆、可委派和可学习的高级学习 Agent。

## 1. 当前能力定位

LearnLoop 第一阶段已经具备：

- 结构化 LLM Provider 和 Prompt 版本；
- LangGraph 状态图；
- Checkpoint、Interrupt 和恢复；
- 课程、练习、评分与补救节点；
- 本地资料导入和混合检索；
- 后台任务；
- 掌握度、复习调度和学习洞察；
- Agent Run、Tool Call 和 Model Call Trace。

这些能力构成了可靠的 Agent Harness，但当前两个核心图仍主要由开发者预定义路径：

- `daily_learning` 按固定顺序选择知识点、检索、授课、出题、评分和补救；
- `goal_planning` 按固定顺序澄清目标、诊断、生成知识图和等待审批；
- LLM 主要生成内容，尚未成为能够动态选择行动的策略主体；
- 检索由固定节点触发，尚不具备多轮检索与证据缺口判断；
- Checkpoint 保存运行状态，但还没有长期记忆的写入、更新和遗忘策略；
- 没有子 Agent 委派与独立上下文。

第二阶段的核心变化应当是：让模型在受控动作空间中，根据目标、计划、记忆和环境反馈动态选择下一步，同时保留确定性边界、预算和人工审批。

## 2. 建立动态学习 Agent Loop

### 目标结构

将固定日常学习流程抽象为通用循环：

```text
observe_state
→ decide_action
→ execute_tool
→ evaluate_observation
→ update_plan
→ continue / ask_user / finish
```

### `observe_state`

构建当前决策所需的结构化视图：

- 用户当前学习目标；
- 活跃计划与当前步骤；
- 知识节点和掌握度；
- 最近作答、错误模式和复习状态；
- 当前可用资料；
- 上一步工具结果；
- 剩余时间、Token、模型与工具调用预算。

### `decide_action`

模型只输出公开、可校验的决策对象，不保存隐藏思维：

```json
{
  "action": "resource.search",
  "reason_summary": "当前证据不足以解释 BFS 为什么需要队列",
  "arguments": {
    "query": "BFS 队列 先进先出 层序遍历"
  },
  "expected_observation": "找到包含队列顺序与逐层遍历关系的材料",
  "plan_step_id": "step-2"
}
```

### `evaluate_observation`

判断：

- 工具是否成功；
- 结果是否足够；
- 是否需要换工具或改写查询；
- 当前计划步骤是否已满足成功标准；
- 是否应该请求用户输入；
- 是否触发 Replan。

### 终止控制

每次 Run 必须设置：

- `max_steps`；
- `max_model_calls`；
- `max_tool_calls`；
- `max_total_tokens`；
- `deadline`；
- 单一动作重复上限；
- 连续失败阈值；
- 用户取消信号。

## 3. 将学习计划升级为可执行 Plan

建议新增独立的 Agent Plan 模型，而不是直接复用面向用户展示的 Study Plan。

```text
AgentPlan
├── objective
├── status
├── version
├── assumptions
├── constraints
├── budget
└── steps[]
    ├── id
    ├── objective
    ├── dependencies
    ├── status
    ├── success_criteria
    ├── assigned_agent
    ├── allowed_tools
    ├── evidence
    └── attempts
```

### Planner

根据用户目标、已有掌握度和时间约束生成 Agent Plan。Plan 必须通过 Schema 校验和确定性约束检查，例如无循环依赖、预算为正、所有依赖均存在。

### Executor

一次只选择一个 Ready Step 执行，避免模型试图在一轮中完成整个长期目标。

### Verifier

根据步骤类型选择验证器：

- 掌握度目标：使用诊断题或迁移题；
- 资料研究：检查证据覆盖和引用支持；
- 计划创建：检查知识依赖、时间预算和用户约束；
- 数据写入：检查数据库最终状态。

### Replanner

以下情况触发重新规划：

- 前置假设被用户否定；
- 资源不足；
- 连续练习未通过；
- 实际耗时明显超过预算；
- 发现新的先修知识缺口；
- 用户改变学习目标。

每次 Replan 都应生成新版本，并保留变更原因和旧计划，便于回放和评测。

## 4. 把 Application Service 重构为 Agent Tools

现有 Service 和 Repository 不需要删除。它们仍然负责业务规则和数据一致性；在其上增加面向模型的 Tool Contract。

建议的工具命名空间：

```text
goal.get_state
plan.create
plan.update_step
learner.get_profile
learner.get_mastery
learner.get_recent_attempts
resource.search
resource.read
exercise.generate
exercise.grade
teaching.select_strategy
review.schedule
memory.search
memory.propose_write
agent.delegate
user.request_input
```

每个 Tool 应包含：

- Pydantic 输入输出 Schema；
- 对模型友好的名称和用途描述；
- 成功、暂时失败、永久失败、权限失败等标准错误；
- 是否只读、是否可逆、风险等级；
- 幂等键；
- 结果 Token 上限和分页；
- Tool 级超时；
- 所需审批策略。

模型不能直接调用 Repository 或拼接 SQL。所有写操作仍需经过领域服务验证，危险或影响范围较大的动作继续使用 Interrupt 请求审批。

## 5. 新增 Context Engine

建议建立独立 `ContextBuilder` 或 `ContextCompiler`，每次模型调用前动态生成上下文，而不是由节点手工拼接所有内容。

### 上下文分区

```text
Context
├── system_policy
├── current_objective
├── active_plan_summary
├── current_step
├── latest_observations
├── learner_memory
├── retrieved_evidence
├── selected_tools
└── output_contract
```

### Token 预算

示例预算策略：

| 区域 | 预算比例 |
| --- | ---: |
| 系统规则和输出约束 | 15% |
| 当前目标与计划 | 15% |
| 最近交互和观测 | 20% |
| 检索证据 | 30% |
| 长期记忆 | 10% |
| 工具定义与预留输出 | 10% |

实际比例应由评测调整，而不是永久写死。

### Compaction

当上下文接近预算时：

1. 删除可重新读取的完整 Tool 结果，只保留引用 ID；
2. 合并重复观察；
3. 将已完成 Plan Step 压缩为结果、证据和决定；
4. 保留未解决问题和失败原因；
5. 把完整内容留在外部 Artifact Store；
6. 保存 Compaction 版本和来源映射。

### 动态工具选择

不要在每次推理时暴露全部工具。例如：

- 研究步骤只加载 `resource.*`、`memory.search` 和 `agent.delegate`；
- 练习步骤只加载 `exercise.*`、`learner.*` 和 `user.request_input`；
- 写操作工具只有在计划允许且用户权限满足时才加载。

## 6. 建立四层学习 Memory

### Working Memory

继续使用 LangGraph State 和 Checkpoint，保存当前 Run、当前步骤、临时观测和预算。

### Episodic Memory

保存具体学习经历：

- 某次作答错误及后续补救；
- 某种教学策略是否有效；
- 某次检索路径为何失败；
- 用户在哪个环节请求更多解释。

### Semantic Memory

保存经过多次证据支持的稳定事实：

- 学习偏好；
- 已掌握与薄弱概念；
- 长期时间约束；
- 经常出现的误解；
- 目标之间的关系。

掌握度领域模型仍然是真实来源，Memory 不能绕过领域规则直接声称用户已掌握某知识点。

### Procedural Memory

保存可复用教学 Skill：

- 诊断特定误解的题目序列；
- 某类概念的类比解释模板；
- 从多份资料生成循序练习的方法；
- 成功的多步检索配方；
- Tool 调用策略。

### 写入流程

```text
Session/Run 完成
→ 提取候选记忆
→ 检查证据与敏感性
→ 去重和冲突检测
→ 人工审批高影响记忆
→ 写入并建立来源关系
```

建议字段：

```json
{
  "memory_type": "learner_preference",
  "content": "用户在图示解释后的迁移题表现更好",
  "confidence": 0.78,
  "evidence_ids": ["session-12", "session-18"],
  "valid_from": "2026-09-01T00:00:00Z",
  "expires_at": null,
  "supersedes_id": null
}
```

### 召回策略

召回评分至少综合：

- 语义相关性；
- 时间相关性；
- 重要度；
- 来源可信度；
- 当前 Plan Step；
- 是否与当前事实冲突。

## 7. 将现有 RAG 升级为 Research Tutor

现有混合检索继续作为基础 Retriever，在其上增加 Agentic Retrieval Loop。

```text
analyze_question
→ decide_retrieval
→ decompose_queries
→ search_resources
→ grade_evidence
→ identify_gaps
→ refine_or_expand_search
→ synthesize
→ verify_citations
```

### 必要能力

- 判断是否真的需要检索；
- 为多跳问题拆分 Query；
- 在个人资料、知识图谱和允许的外部来源之间路由；
- 评价相关性、覆盖度、可信度和新颖性；
- 避免重复检索同一内容；
- 每条重要结论关联 Chunk 和 Resource；
- 证据不足时明确拒答或请求更多资料。

建议把 Citation Verifier 设计成独立验证阶段：它只接收结论和候选证据，输出 `supported / unsupported / partially_supported`，不负责改写最终答案。

## 8. 引入 Subagent-as-Tool

第一版采用单一 Lead Learning Agent，并将子 Agent 作为受控工具调用，不采用自由群聊。

### 建议角色

- **Researcher**：多轮检索并返回带引用证据。
- **Curriculum Designer**：把目标和知识依赖转化为候选课程结构。
- **Tutor**：选择解释、提问和提示策略。
- **Evaluator**：根据 Rubric、答案和证据检查结果。

### 委派契约

```json
{
  "task_id": "subtask-7",
  "objective": "比较三份资料对 BFS 时间复杂度的解释",
  "scope": ["resource-1", "resource-2", "resource-3"],
  "allowed_tools": ["resource.search", "resource.read"],
  "expected_output_schema": "EvidenceComparison",
  "max_tool_calls": 10,
  "max_tokens": 12000,
  "deadline_seconds": 60
}
```

每个 Subagent 使用独立 Context，只向 Lead Agent 返回：

- 结构化结论；
- 证据引用；
- 未解决问题；
- 置信度；
- 实际消耗。

### 并行化规则

适合并行：

- 分析互相独立的资料；
- 生成多个候选教学方案；
- 针对不同知识缺口分别研究；
- 独立评价同一个最终产物。

不适合并行：

- 后一步依赖前一步用户回答；
- 多个 Agent 同时修改同一计划；
- 必须共享完整对话才能正确判断；
- 同一数据库实体存在写冲突。

## 9. 加入 Verifier、Reflection 和 Skill Library

### 先验证，再反思

可靠闭环应为：

```text
Agent 产出
→ 确定性验证或独立评价
→ 得到客观反馈
→ Reflection 总结可操作原因
→ 更新 Plan 或提出 Memory 写入
```

反馈来源优先级：

1. 标准答案、数据库状态、Schema、代码测试等确定性信号；
2. 带引用的证据核对；
3. 用户明确反馈；
4. 带 Rubric 的模型评价；
5. 无外部依据的自我评价。

### Skill Library

当某个策略在多个任务上成功时，提取为 Skill：

```text
Skill
├── name
├── applicable_conditions
├── required_tools
├── procedure
├── verification
├── source_runs
├── success_rate
└── version
```

使用 Skill 前应检查适用条件；使用后更新成功率。低成功率、过期或存在安全问题的 Skill 应被降权或停用。

## 10. 支持跨 Context 和长程目标

在现有 Checkpoint 上增加：

- 持久化 Agent Plan；
- Context Snapshot 与 Compaction Record；
- Run Handoff Artifact；
- 未解决问题列表；
- 下一步建议；
- 环境和数据版本；
- 预算消耗；
- 恢复前的一致性检查。

每个执行周期只推进有限步骤，并在退出前生成机器可读交接：

```json
{
  "completed_steps": ["step-1", "step-2"],
  "active_step": "step-3",
  "verified_facts": [],
  "open_questions": [],
  "failed_attempts": [],
  "next_recommended_action": "learner.get_mastery",
  "remaining_budget": {}
}
```

恢复时不能直接相信旧摘要，还应检查目标、资源、计划版本和数据库状态是否已经变化。

## 11. 为未来 Agent Learning 准备轨迹

保留现有 Trace 数据层，并逐步增加：

- 计划版本和步骤；
- 每次公开决策；
- Context 组成和 Token 数，不保存敏感全文；
- Tool 选择、参数和结果摘要；
- 子 Agent 委派关系；
- Verifier 结果；
- 最终 Reward 分解；
- 用户纠正与审批。

学习系统的 Reward 不能只使用即时答题正确率。建议组合：

```text
即时正确率
+ 延迟测试保持率
+ 迁移题表现
+ 用户目标完成度
- 提示次数
- 无效工具调用
- Token/时间成本
- 直接泄露答案等策略违规
```

初期使用离线回放、A/B 测试和 Contextual Bandit 比较教学策略；只有在轨迹、Reward 和安全边界成熟后，再引入 SFT 或 Agentic RL。

## 12. 保留确定性边界

高级 Agent 不意味着把所有业务判断交给 LLM。下列能力应继续由确定性代码控制：

- 身份、权限和数据隔离；
- 数据库事务与领域不变量；
- 客观题评分；
- 幂等写入；
- 预算、超时、重试上限；
- 危险动作审批；
- 最终状态验证；
- 审计日志和数据删除。

模型适合处理开放问题、计划、语义判断和策略选择；代码负责硬边界和可验证规则。

## 目标架构

```text
User
  ↓
Lead Learning Agent
  ├── Context Compiler
  ├── Planner / Replanner
  ├── Dynamic Tool Policy
  ├── Memory Manager
  ├── Researcher Subagent
  ├── Curriculum Subagent
  ├── Tutor Subagent
  └── Evaluator Subagent
          ↓
Domain Services / RAG / Database / Worker
          ↓
Verifier + Trace + Evaluation Dataset
```

这一架构保留第一阶段可靠的后端底座，同时把模型从固定节点中的内容生成器提升为受控的决策主体。
