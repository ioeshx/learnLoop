# LearnLoop v2 详细实施计划

本文是 [第二阶段高级 Agent 开发路线图](phase-two-roadmap.md) 的执行层补充。
应用设计文档回答“系统最终应具备什么能力”，路线图回答“能力按什么顺序进入”，
本文回答“每个里程碑具体开发什么、如何验证、何时可以进入下一阶段”。

## 1. 结论与范围

现有两份文档足以确定方向，但还不足以直接进入长期开发，原因是尚未明确：

- 阶段内的依赖顺序和最小可交付切片；
- v1 固定图与 v2 动态内核如何共存、比较和回滚；
- Agent Plan、预算、Action、Observation 等对象的持久化归属；
- 每阶段需要增加的 API、Trace、前端和数据库变化；
- 单元测试、轨迹测试、离线评测和线上观察各自负责什么；
- 阶段的进入条件、退出条件和停止条件。

第二阶段应采用“先建立可比较基线，再以影子模式引入动态内核，最后逐步接管流量”的方式。
阶段 11～14 是 v2 的核心交付；阶段 15～17 是通过评测门禁后才进入的扩展能力。

### v2 首个可用版本

首个可用版本只要求：

```text
一个学习 Session
→ 生成短期可执行计划
→ 在白名单内动态选择读取、检索、授课、出题和请求用户输入
→ 对每个步骤做验证
→ 在预算内完成、暂停或明确失败
→ 全轨迹可回放
```

它不要求长期 Memory、外部网络搜索、子 Agent、Reflection 或 RL。这样可以先证明
动态决策相对于固定 `daily_learning` 图有真实收益。

## 2. 当前代码基线与主要缺口

当前仓库已有可复用底座：

- `app/agent/graphs/` 中的 `daily_learning` 和 `goal_planning` 固定图；
- `app/agent/execution/` 中的 Checkpoint、Run、SSE 和 Trace；
- `app/agent/tools/learning.py` 对 Application Service 的薄封装；
- `app/infrastructure/rag/` 中的本地混合检索；
- `backend/evals/` 中的版本化数据集和确定性指标；
- Application Service、领域规则、SQLite、后台任务和前端运行详情页。

开始 v2 前需要正视以下缺口：

1. `GraphKind` 只有两个固定图，运行时没有通用 Agent Loop。
2. `AgentRun` 对 `(graph_kind, resource_id)` 唯一，同一资源不能自然地重复运行、做
   `pass^k` 或新旧版本对照。
3. Run 状态缺少 `cancelled`、`budget_exhausted` 等终止语义，也没有取消接口。
4. Tool 只有 Python 方法，没有统一注册表、输入输出 Schema、风险、幂等和错误分类。
5. Trace 没有 Plan Version、Action、Observation、Verification、Replan 和 Context Snapshot。
6. 现有评测主要计算静态 JSON 样本，尚不能执行并重放完整 Agent 轨迹。
7. Prompt 由节点分别组装，无法统一计算上下文预算和来源。

这些不是附属优化，而是动态 Agent 可控、可评测和可回滚的前提。

## 3. 开发顺序与依赖

```text
阶段 10.5：v2 基线与协议
  ↓
阶段 11A：执行模型、Tool Contract、预算
  ↓
阶段 11B：只读动态 Loop（Shadow）
  ↓
阶段 11C：验证、Replan、Interrupt 与受控写入
  ↓
阶段 11D：新旧内核对照与小流量接管
  ↓
阶段 12：完整 Context Engine
  ↓
阶段 13：跨 Session Memory
  ↓
阶段 14：Agentic RAG / Research Tutor
  ↓
阶段 15：Subagent-as-Tool（条件阶段）
  ↓
阶段 16：Reflection / Skill Library（条件阶段）
  ↓
阶段 17：策略优化 / Agentic RL（研究阶段）
```

阶段 11 需要一个最小 Context Builder，但消息压缩、来源映射和长上下文恢复属于阶段 12。
阶段 13 和 14 可以在阶段 12 后分别开发，但应先完成 Memory 的可信边界，再允许检索内容
进入长期记忆。阶段 15 以后不得与 11～14 同时作为关键路径。

## 4. 跨阶段的工程约定

### 4.1 双内核运行

在阶段 11D 通过门禁前，保留现有两个固定图：

- `fixed_v1`：现有 LangGraph，作为回退和基线；
- `dynamic_v2`：新的有界循环；
- 通过配置或创建 Run 时的 `engine_version` 选择，不在运行中隐式切换；
- 同一输入允许生成多个 Run，Run 必须记录模型、Prompt、Tool Registry、数据集和内核版本。

不要直接把现有固定节点逐个改成动态节点。这会让基线消失，也难以判断回归来自哪里。

### 4.2 数据归属

在阶段 10.5 通过 ADR 最终确认，默认建议为：

- `learnloop.db`：Agent Run 元数据、Agent Plan、Step、预算账本、Memory、Skill 和可查询 Trace；
- `checkpoints.db`：LangGraph Checkpoint 本体；
- Artifact Store：大体积 Tool 结果、Context 全文和压缩前内容；
- Trace 只保存脱敏摘要、哈希、引用和 Token 统计，不默认复制全部 Prompt 与用户资料。

原因是业务可查询数据需要 Alembic 迁移、外键和一致性约束，而 Checkpoint 数据应保持运行时实现细节。
如果暂时继续复用 `checkpoints.db`，也必须引入显式 Schema 版本和迁移脚本，不能长期依赖
`CREATE TABLE IF NOT EXISTS` 演进。

### 4.3 完成定义

一个里程碑只有同时满足以下条件才算完成：

- Schema、状态机和错误语义有测试；
- 数据迁移可以从现有开发数据库升级，并有回滚说明；
- API 与 SSE 事件文档同步；
- Fake Model / Fake Tool 可稳定重放成功、失败、超时和 Interrupt；
- 新增指标进入版本化评测报告；
- 不破坏 v1 固定图的回归测试；
- 关键配置有默认值、边界校验和 `.env.example` 说明。

## 5. 阶段 10.5：v2 基线、ADR 与可执行评测

### 目标

在改变 Agent 行为前，得到可重复的 v1 轨迹基线，并冻结 v2 的核心协议。

### 10.5.1 决策文档

新增 ADR，明确：

1. v1/v2 双内核和回滚方式；
2. Agent 执行数据的数据库归属和迁移方式；
3. `AgentPlan` 与面向用户的 `StudyPlan` 是两个模型，前者不能替代后者；
4. Tool 风险分级、审批规则和不可信内容边界；
5. Trace 的脱敏、保留期和 Artifact 引用策略。

### 10.5.2 Run 协议升级

调整 Run 身份和生命周期：

- 移除“一种图和一个资源只能有一个 Run”的假设；
- 增加 `engine_version`、`parent_run_id`、`attempt_no`、`terminal_reason`；
- 状态至少支持 `created / running / awaiting_input / completed / failed / cancelled`；
- `budget_exhausted`、`deadline_exceeded`、`verification_failed` 作为明确终止原因；
- 增加乐观并发控制或状态迁移校验，避免重复 resume；
- 增加 `POST /agent/runs/{run_id}/cancel`，取消信号写入持久层并由运行循环轮询。

### 10.5.3 轨迹数据集

把当前静态评测扩展为可执行 Scenario：

```text
Scenario
├── id / tags / dataset_version
├── initial_domain_state
├── user_turns
├── fake_model_responses
├── fake_tool_behaviors
├── expected_terminal_state
├── expected_required_actions
├── forbidden_actions
└── metric_thresholds
```

第一批包含 24 个场景：

- 正常完成 4 个；
- 信息不足并请求输入 3 个；
- 检索无结果或证据不足 3 个；
- Tool 暂时失败、永久失败和超时各 2 个；
- 用户修改目标或约束 2 个；
- 连续答错并补救 2 个；
- Checkpoint 恢复和重复 resume 2 个；
- Prompt Injection 与越权写入 2 个。

同一场景支持运行 `k` 次，输出任务成功率、合法终止率、轨迹一致性、调用数、Token、延迟和费用。

### 10.5.4 交付物

- ADR 与 v2 Event/Run Schema 文档；
- Run 数据迁移和状态机测试；
- 可执行 Scenario Runner、Fake Model 和 Fake Tool Script；
- v1 基线报告，记录均值、P50/P95 和失败分类；
- CI 中增加快速确定性集，完整 `pass^k` 集留给定时或手动运行。

### 退出门禁

- v1 的核心场景可重复执行并生成同结构报告；
- 连续运行和重复 resume 不产生重复领域写入；
- 每个 Run 都能解释为何结束；
- 旧数据可升级，v1 API 和前端仍可工作；
- 未取得这些结果前，不开始让模型动态选择写操作。

## 6. 阶段 11：动态单 Agent 内核

阶段 11 拆成四个可独立验收的里程碑，避免一次提交 Planner、Tools、Loop 和 Replanner。

### 11A：执行模型、Tool Contract 与预算

#### 领域对象

实现以下 Pydantic/领域模型及其状态迁移：

- `AgentPlan`、`PlanStep`、`PlanVersion`；
- `AgentAction`、`ActionArguments`、`Observation`；
- `VerificationResult`、`ReplanDecision`；
- `RunBudget`、`BudgetUsage`、`BudgetDecision`；
- `ToolSpec`、`ToolResult`、`ToolError`、`ApprovalPolicy`。

关键不变量：

- Plan 依赖必须存在且无环；
- 一个执行周期最多只有一个 active Step；
- 已完成 Step 的证据不可被 Replan 静默删除；
- Plan 更新生成新版本，不原地覆盖历史版本；
- 预算扣减由代码完成，模型只能读取，不能修改；
- 写 Tool 必须有幂等键，参数经 Schema 验证后才能进入 Application Service。

#### Tool Registry

不要立刻暴露 `LearningTools` 全部方法。先建立 `ToolRegistry` 和统一执行器，第一批只注册：

| Tool | 类型 | 风险 | 用途 |
| --- | --- | --- | --- |
| `goal.get_state` | 读 | 低 | 读取目标和约束 |
| `learner.get_mastery` | 读 | 低 | 读取掌握度 |
| `resource.search` | 读 | 低 | 检索本地资料 |
| `resource.read` | 读 | 低 | 按引用读取有限内容 |
| `exercise.get` | 读 | 低 | 读取当前练习 |
| `exercise.grade` | 确定性写 | 中 | 判分并保持幂等 |
| `review.schedule` | 可逆写 | 中 | 写复习安排 |
| `user.request_input` | Interrupt | 中 | 请求答案、澄清或审批 |

统一执行顺序：参数校验 → 权限/计划白名单 → 审批检查 → 幂等检查 → 超时执行 → 结果裁剪
→ 标准错误映射 → Trace。

#### 预算控制

在配置中增加并校验：

- `max_steps`、`max_model_calls`、`max_tool_calls`；
- `max_input_tokens`、`max_output_tokens`、`max_total_tokens`；
- `deadline_seconds`、单 Tool 超时；
- `max_same_action`、`max_consecutive_failures`；
- 可选 `max_cost_usd`，没有可靠价格表时只记录、不作为硬门禁。

在每次模型调用和 Tool 调用前做预检，完成后原子记账。不能只在 Run 结束后统计。

#### 测试与退出门禁

- 所有状态转换、Plan DAG、预算边界和错误分类均有单元测试；
- Tool Registry 拒绝未注册、未授权和错误参数；
- 写 Tool 的超时重试不会造成重复写入；
- Trace 能关联 `run_id → plan_version → step_id → action_id → tool_call_id`；
- 本阶段不要求模型做动态决策。

### 11B：只读动态 Loop 与 Shadow Mode

#### 最小图

建立新的通用图或运行循环：

```text
load_run
→ observe
→ compile_minimal_context
→ decide
→ validate_action
→ execute_read_tool / request_input / propose_finish
→ verify
→ continue / pause / terminate
```

第一版只允许读 Tool、`user.request_input` 和 `propose_finish`。模型输出严格的 `AgentAction`
结构，不记录隐藏推理，只保存简短 `reason_summary`、预期 Observation 和 Plan Step。

公开动作类型固定为：

- `call_tool`：调用当前 Step 白名单内的 Tool；
- `present_content`：向用户展示讲解、提示、练习或阶段结果；
- `request_input`：生成结构化 Interrupt，等待澄清、答案或审批；
- `complete_step`：提交当前 Step 的候选完成证据，由 Verifier 决定是否完成；
- `finish_run`：仅在所有必要 Step 已验证或出现合法终止原因时结束。

`present_content` 不是绕过 Tool 的自由写入口：它不能修改领域数据，也不能自行宣告掌握度、
判分结果或计划已经完成。

#### Planner 范围

- 只生成当前 Session 的 2～6 个短期步骤；
- 每步必须有成功标准、依赖和 Tool 白名单；
- 由确定性校验器检查 DAG、预算和允许动作；
- 无效输出允许一次结构修复，再失败则明确终止或回到 v1；
- 此时不规划跨周课程，不替代 `StudyPlan`。

#### Shadow 运行

对同一 Scenario 同时执行：

- v1 正常产生用户可见结果；
- v2 读取相同初始快照，但禁止领域写入且结果不展示给用户；
- 比较其 Tool 选择、计划、终止状态、成本和潜在答案；
- Shadow 必须使用隔离事务或完全只读端口，不能意外影响 v1。

#### Trace 事件

至少新增：

- `plan_created`、`plan_rejected`；
- `action_decided`、`action_rejected`；
- `observation_recorded`；
- `verification_completed`；
- `budget_updated`；
- `run_paused`、`run_cancelled`。

SSE 对事件采用可向后兼容的增量字段，前端先以通用时间线展示未知事件。

#### 测试与退出门禁

- 所有 Scenario 都在硬预算内合法终止；
- 不允许 Tool 的实际调用次数为零；
- 重复动作检测能打断相同 Tool 和等价参数的循环；
- 在 Fake Model 下轨迹可完全重放；
- Shadow v2 在目标任务上的“潜在成功率”达到预设门槛后才开放写 Tool。

### 11C：Verifier、Replanner、Interrupt 与受控写入

#### Verifier

按结果类型实现分层验证：

1. Schema、状态和数据库最终值等确定性验证；
2. 客观题评分和计划依赖等领域验证；
3. 引用支持检查；
4. 仅在前三类不足时使用带 Rubric 的模型验证。

Verifier 输出 `passed / failed / inconclusive`，并列出证据 ID、失败代码和建议动作。
`inconclusive` 不能被当成成功。

#### Replanner

只允许下列结构变化：

- 修改未开始步骤；
- 插入缺失的先修步骤；
- 标记阻塞并请求输入；
- 在预算允许时替换失败 Tool 或检索 Query；
- 终止不再可达的分支。

保留旧版本与 diff。默认最多 Replan 两次，超过后请求用户或以明确原因终止。

#### Interrupt 和恢复

统一澄清、答题、审批三类 Interrupt Schema。恢复前重新校验：

- Run 仍处于 `awaiting_input`；
- 输入对应当前 Interrupt ID；
- Plan、资源和领域实体版本未产生冲突；
- 剩余预算仍允许至少一次决策；
- 已完成写操作的幂等记录存在。

#### 受控写入

按风险逐个开放：客观题判分 → Mastery 更新 → Review 安排 → Session 完成。
每开放一个写 Tool，都要补充幂等、事务回滚、权限、取消和重放测试。

#### 测试与退出门禁

- 暂时失败会按策略重试或换方案，永久失败不会盲目重试；
- 用户改变约束后生成新 Plan Version，旧的完成证据保留；
- 进程在写入后、Trace 前崩溃时，恢复不会重复领域副作用；
- Prompt Injection 内容只能作为数据，不会改变 Tool 白名单或审批策略；
- 动态日常学习闭环在确定性场景集上端到端通过。

### 11D：对照评测、小流量接管与回滚

#### 接管顺序

1. 开发/测试环境手动选择 `dynamic_v2`；
2. 本地真实模型 Shadow；
3. 仅对内部测试用户开放动态日常学习；
4. 按配置比例 Canary；
5. 达到门禁后作为新 Session 默认，v1 保留至少一个发布周期；
6. `goal_planning` 是否迁移由独立评测决定，不与日常学习捆绑切换。

#### 比较指标

- 最终任务成功率和合法终止率；
- `pass^k`、Plan Step 完成率；
- Tool 选择/参数准确率和重复调用数；
- 用户澄清次数、人工接管率；
- Token、Tool 调用数、P50/P95 延迟和估算费用；
- 越权、无证据结论和错误写入数量。

#### 建议门禁

数值应在基线报告后冻结，不应在看到 v2 结果后临时修改。默认原则：

- 安全和错误写入不得比 v1 差；
- 任务成功率有统计上稳定的提升，或在持平时显著降低人工接管；
- P95 延迟、平均 Token 和费用不超过产品可接受上限；
- 任一硬安全指标失败，自动关闭 v2 路由而不是等待综合分抵消。

### 阶段 11 最终交付物

- 可持久化、可版本化的 Agent Plan；
- Typed Tool Registry 和标准执行器；
- 有界动态 Loop、预算账本、Verifier 和 Replanner；
- 统一 Interrupt、取消、恢复和终止语义；
- v1/v2 对照报告、配置开关和回滚手册；
- 前端 Run 页面展示 Plan、当前 Step、预算和公开决策时间线。

## 7. 阶段 12：Context Engine

### 12A：统一编译接口

建立 `ContextCompiler`，输入必须是结构化引用，而不是节点拼接后的字符串：

```text
ContextRequest
├── run_id / plan_version / step_id
├── objective
├── recent_observation_ids
├── memory_query（阶段 13 前为空）
├── evidence_ids
├── candidate_tool_names
└── token_budget
```

输出 `ContextPackage`，包含每个分区的 Token 数、来源 ID、截断原因、工具集合和输出预留。
首先接管 v2，v1 保持不动。

### 12B：Token 预算和动态 Tool 选择

- 使用实际模型 tokenizer；无 tokenizer 时采用保守估算并记录误差；
- 先保留系统政策、当前 Step、未解决问题和输出空间；
- 再按优先级加入最近 Observation、证据和历史步骤；
- Tool Schema 根据 Step 白名单和权限交集选择；
- Context 超限时明确失败或压缩，禁止静默截断关键约束。

### 12C：Compaction 与 Artifact

- 完整 Tool 结果写 Artifact，Context 中只留摘要和引用；
- 已完成 Step 压缩为结果、证据、决策和失败尝试；
- 重复 Observation 合并但保留来源映射；
- Compaction 生成版本、输入哈希、输出哈希和覆盖范围；
- 恢复时检查引用对象是否存在及其版本是否变化。

### 12D：可解释性和前端

- Trace 记录 `context_snapshot_created`，默认只暴露元数据；
- Run 页面展示分区 Token、被裁剪来源和所用 Tool Schema；
- 敏感全文仅在本地调试开关开启时显示，并遵循保留期。

### 退出门禁

- 长场景跨多个 Context Window 后仍保留所有未解决事项；
- 压缩前后关键事实、引用和约束一致；
- 相比完整历史，Token 显著降低且成功率不下降到门槛以下；
- Context 中的每个事实可以追溯到来源或明确标记为模型假设；
- Tool 裁剪不会让当前 Step 的唯一合法动作消失。

## 8. 阶段 13：Agent Memory

### 13A：Memory 数据模型和治理 API

建立 `MemoryRecord`、`MemoryEvidence`、`MemoryRevision`：

- 类型、内容、结构化属性、置信度和重要度；
- 用户、目标和知识点作用域；
- 来源 Run/Session/Attempt；
- `valid_from`、`expires_at`、`supersedes_id`；
- `candidate / active / rejected / expired` 状态；
- 来源信任级别和是否需要审批。

提供搜索、查看来源、更正、停用和删除 API。删除用户数据时必须级联清理 Memory 和索引。

### 13B：候选提取和写入策略

只在 Run 完成或明确 Handoff 时提取候选，不在每一步直接永久化。按顺序执行：

```text
候选提取
→ PII/敏感性检查
→ 来源可信度检查
→ 去重
→ 冲突/替代判断
→ 高影响审批
→ 写入
```

掌握度、复习安排和客观作答继续以领域模型为真实来源；Memory 只能引用或总结，不能覆盖。

### 13C：检索、重排和 Context 接入

- 先做元数据过滤和关键词/向量召回；
- 再按相关性、时间、重要度、来源和当前 Step 重排；
- 返回支持证据和冲突项，不只返回最终文本；
- 没有可靠结果时返回空集合；
- 将 Memory 作为 Context 独立分区，可单独关闭做消融。

### 13D：评测与治理界面

构建跨 Session 数据集：事实回忆、时间更新、冲突、无答案、恶意内容、删除和过期。
前端提供“系统记住了什么”、来源、纠正和删除入口。

### 退出门禁

- 更新后的偏好能够替代旧记录，旧记录不再进入 Context；
- 不可信资料中的指令不能成为 Semantic/Procedural Memory；
- 无记忆时不编造，删除后无法通过检索召回；
- 开启 Memory 对目标集有可测收益，并且错误召回率低于冻结门槛。

## 9. 阶段 14：Agentic RAG 与 Research Tutor

### 14A：证据模型和单轮质量门

先建立 `EvidenceItem`、`Claim`、`CitationLink` 和 `EvidenceGrade`，统一资源、Chunk、页码、
来源版本和内容哈希。给现有单轮检索增加相关性、来源质量、重复度和覆盖度判断。

### 14B：检索决策和多跳分解

- 判断 `no_retrieval / single_retrieval / multi_step_research`；
- 把复杂问题拆成可验证子问题；
- 记录 Query 与子问题关系，避免重复 Query；
- 第一版只路由个人本地资料；外部来源作为单独安全评审后的扩展。

### 14C：Gap-driven Loop

```text
检索
→ 证据评分
→ 声明覆盖检查
→ 识别缺口
→ 改写 Query / 扩展来源 / 请求资料
→ 达标或预算终止
```

限制最大轮数、Query 数、来源数、读取字符数和 Token。低分证据保留在 Trace，但不进入答案上下文。

### 14D：合成和 Citation Verifier

- 先从证据生成原子 Claim，再组织答案；
- Citation Verifier 只判定 `supported / partially_supported / unsupported`；
- unsupported 重要 Claim 必须删除、降级表述或明确说明不确定；
- 最终答案保留 Claim → Chunk → Resource 的映射。

### 退出门禁

- 多跳集的 Recall、答案正确率和引用支持率优于阶段 12 的单轮检索；
- 无需检索的场景不会产生额外检索；
- 证据不足时不会补写无来源结论；
- 恶意资料不能改变系统政策、Tool 权限或 Memory 写入策略；
- 收益能够覆盖多轮检索增加的延迟和 Token。

## 10. 阶段 15：Subagent-as-Tool

### 进入条件

只有当单 Agent 的失败聚类明确显示“上下文相互污染、独立研究耗时或专业验证”是主要瓶颈，
并且同预算的拆分实验有收益时才进入。不能仅因为架构设计中存在 Multi-agent 就实现。

### 实施里程碑

1. 建立通用 `DelegationRequest/DelegationResult`、父子 Run、预算划拨和取消传播。
2. 只实现 `Researcher`，串行委派，返回结论、证据、未解决问题和消耗。
3. 加入 Lead 对委派结果的 Schema、证据和范围验证。
4. 再按真实失败类型选择 `Curriculum`、`Tutor` 或 `Evaluator`，不一次全部上线。
5. 只有互相独立、只读且无共享写入的任务允许并行。
6. 加入重复委派检测、子 Agent 最大数、总预算和墙钟 Deadline。

### 退出门禁

- 简单任务的委派率接近零；
- 子 Agent 不能获得范围外资料和 Tool；
- 父 Run 取消后子 Run 能停止，预算不会继续增长；
- 同预算对照中质量或墙钟时间有稳定改善；
- 多 Agent 失败时 Lead 能降级为单 Agent 或请求用户，而不是无限再委派。

## 11. 阶段 16：Reflection 与 Skill Library

### 16A：结构化 Reflection

只对已经有外部验证信号的成功/失败 Run 生成 Reflection，字段包含问题类别、证据、根因、
可操作改进和适用范围。Reflection 是候选经验，不是真实领域事实。

### 16B：候选 Skill 与审核

从多条相似且成功的轨迹提取候选 Skill：适用条件、前置条件、步骤、Tool、验证器、来源 Run、
版本和风险等级。初期人工审核后发布。

### 16C：召回、执行和降级

- Planner 只召回满足适用条件的 Skill；
- Skill 不能扩大当前 Tool 权限和预算；
- 使用后记录成功率、成本和失败类型；
- 低成功率、数据分布变化或安全问题触发降权、隔离和重新验证。

### 退出门禁

- 使用/不使用 Skill 的消融实验表明调用数下降或成功率提升；
- 错误 Skill 不会越权且可以即时全局停用；
- Reflection 的每一项结论能追溯到 Observation 或 Verifier；
- Skill 更新有版本、测试集和回滚路径。

## 12. 阶段 17：策略优化与 Agentic RL

本阶段不以“接入训练框架”为起点，而按风险从低到高推进：

1. 轨迹失败聚类，修复 Harness、Tool 描述和确定性规则；
2. Prompt 与 Context Policy 的离线搜索和 A/B；
3. 对教学策略使用 Contextual Bandit；
4. 经人工和 Verifier 筛选的成功轨迹用于 SFT；
5. 有稳定偏好数据后再做偏好优化；
6. 最后才研究多步 Credit Assignment 和 Agentic RL。

每次实验必须固定训练/验证/保留测试集，记录模型、数据、代码、Prompt 和 Reward 版本。
Reward 分项分别报告，安全惩罚使用硬门禁，不允许被任务分数抵消。

### 退出门禁

- 未见任务上的提升可复现；
- 与只改 Prompt、Context 或 Harness 的基线做过消融；
- 延迟保持和迁移表现提升，不只优化即时答题；
- 安全、成本和最坏情况指标未恶化；
- 模型、策略和数据版本均能回滚。

## 13. 前端与 API 的渐进交付

前端不单独排在所有后端完成之后，而跟随可观测能力逐步增加：

| 阶段 | API/SSE | 前端最小交付 |
| --- | --- | --- |
| 10.5 | 多 Run、取消、终止原因 | Run 列表区分版本和状态 |
| 11A | Plan、预算、Tool 元数据 | Plan/Step 与预算面板 |
| 11B | Action/Observation/Verification 事件 | 通用轨迹时间线 |
| 11C | 统一 Interrupt、Replan diff | 澄清/答题/审批卡片，计划变更视图 |
| 11D | Engine 配置和对照指标 | 内部调试开关与对照报告 |
| 12 | Context Snapshot 元数据 | Context 分区和 Token 视图 |
| 13 | Memory 查询、更正、删除 | 用户 Memory 管理页 |
| 14 | Claim 与 Citation | 可点击证据和证据不足提示 |
| 15 | 父子 Run | 委派树与子任务消耗 |
| 16 | Skill 来源和版本 | 管理/停用界面，普通用户不必暴露内部细节 |

所有调试视图都必须区分普通用户和开发模式，避免暴露系统 Prompt、敏感资料或内部策略。

## 14. 推荐的第一批开发 Backlog

下面的顺序可以直接转成 Issue；每项应形成独立、可评审提交：

1. 编写 v2 双内核、执行存储和 Trace 数据策略 ADR。
2. 修改 Run 标识和状态机，支持同资源多次运行及 `engine_version`。
3. 增加持久化取消、终止原因和并发 resume 防护。
4. 定义版本化 Scenario Schema，建立 24 个核心场景。
5. 让 Scenario Runner 真正执行 v1，并生成冻结基线报告。
6. 定义 `AgentPlan/PlanStep` 和 DAG、版本、不变量测试。
7. 定义 `RunBudget/BudgetUsage`，接入模型与 Tool 调用前后记账。
8. 建立 `ToolSpec/ToolRegistry/ToolExecutor` 和标准错误协议。
9. 将 5 个只读能力迁移到新 Tool Registry。
10. 建立最小 `ContextCompiler` 和严格 `AgentAction` 输出。
11. 实现只读动态 Loop、重复动作检测和预算终止。
12. 增加 Action、Observation、Verification 和 Budget Trace/SSE。
13. 在 Scenario Runner 中并排运行 v1 与 Shadow v2。
14. 实现第一批确定性 Verifier 和有上限 Replanner。
15. 统一 Interrupt Schema 和恢复一致性检查。
16. 按“判分 → Mastery → Review → 完成 Session”逐个开放写 Tool。
17. 完成端到端动态日常学习闭环和失败注入测试。
18. 前端增加 Plan、Step、预算和轨迹时间线。
19. 运行真实模型 Shadow，冻结 Canary 门槛。
20. 小流量接管并验证一键回退到 `fixed_v1`。

前 5 项完成前不要开始第 11 项；第 13 项完成前不要开放写 Tool；第 20 项完成前不要删除 v1 图。

## 15. 每阶段评审模板

每个阶段结束时提交一页评审记录：

```text
阶段与版本：
输入基线：数据集 / 模型 / Prompt / Tool Registry / 代码版本
完成内容：
未完成与延期内容：
成功指标：基线值 → 当前值
成本指标：Token / Tool / 延迟 / 费用
安全指标：越权 / 错误写入 / Injection / 隐私
Top 5 失败类型：
是否满足退出门禁：
回滚方式：
下一阶段建议：继续 / 补救 / 停止
```

如果质量提升无法覆盖复杂度和成本，允许阶段停止并保留较简单架构。路线图是能力假设，评测结果才是
是否继续增加复杂度的依据。
