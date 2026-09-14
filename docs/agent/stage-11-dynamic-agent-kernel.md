# LearnLoop v2 Stage 11：Dynamic Agent Kernel 实现说明

本文说明 Stage 11 的实际实现、架构、控制边界和代码阅读路径，重点描述 Agent 相关的高级
技术，而不是重复产品页面或普通 CRUD。

## 1. 本阶段交付结果

Stage 11 将 v1 中“模型只负责生成内容”的固定工作流，扩展为一个受控的动态单 Agent：

```text
Session objective
  → Planner 生成短期 AgentPlan
  → ContextCompiler 为当前 Step 选择最小 Context 和 Tool Schema
  → Executor 决定一个公开 AgentAction
  → ToolExecutor 执行受控 Action
  → Observation 进入 durable state
  → Verifier 判断 Step 是否真正完成
  → 继续 / Interrupt / Replan / 有原因地终止
```

完成的工程能力包括：

- `fixed_v1` / `dynamic_v2` 双内核和显式选择；
- 同一资源多次 Run、`attempt_no`、`engine_version`、`terminal_reason`；
- 可持久化且版本化的 `AgentPlan`；
- Typed Tool Registry、allowlist、risk、timeout、幂等和标准错误；
- 原子 Budget accounting 和所有路径有界终止；
- 最小 Just-in-time Context Compiler；
- 公开 Action、Observation、Verification 和 Replan Trace；
- 三类 Interrupt：`answer / clarification / approval`；
- dynamic durable state 和跨进程 resume 所需协议；
- paired-run comparison API 和前端 Plan/Budget/Trace 面板；
- 默认只读 Shadow 模式，以及按配置开放的受控写 Tool。

生产真实流量 Canary 属于部署活动。本阶段实现其必要机制，但没有把“代码可 Canary”描述成
“已经完成线上放量”。

### 1.1 当前验收边界

本次交付完成的是 Stage 11 的 **kernel、protocol、guardrail、persistence、API 和
observability implementation**，并通过确定性测试及现有 v1 回归测试。以下项目依赖真实
模型、冻结数据集或生产流量，因此不在本次代码交付中伪造结论：

- Phase 10.5 规划的 24 个可执行 Scenario 和冻结的 v1 baseline report；
- 使用真实模型执行的 paired Shadow report，以及 `pass^k`、P50/P95 和估算费用；
- 内部用户放量、Canary 比例路由和线上 safety gate；
- “v2 相比 v1 成功率已提升”的统计结论。

因此当前状态是“Stage 11 engineering path 可执行”，不是“Stage 11 production exit gate 已
通过”。进入默认流量前，应先补齐 Scenario Runner 和 baseline，再利用本阶段提供的双内核、
Trace 与 comparison API 冻结门槛并运行 Shadow。

## 2. 总体架构

```text
FastAPI / SSE
      │
      ▼
AgentRuntime ─────────────── engine_version ──────────────┐
      │                                                   │
      ├── fixed_v1 → existing LangGraph                   │
      │                                                   │
      └── dynamic_v2 → DynamicAgentKernel                 │
                           │                              │
             ┌─────────────┼──────────────┐               │
             ▼             ▼              ▼               │
       ModelAgentPolicy  BudgetLedger  ContextCompiler
       Planner/Executor       │              │
       Replanner              │              ▼
             │                │        selected Tool Schema
             ▼                │
       typed AgentAction      │
             │                │
             ▼                ▼
       ToolExecutor ─── deterministic guards
             │
             ▼
       Application Service → Domain Rule → Repository
             │
             ▼
         Observation → DeterministicVerifier → Plan Version
             │
             ▼
       SqliteAgentRunStore / Trace / SSE replay
```

关键原则是把 **Policy** 与 **Harness** 分开：模型是 Policy，只提出 Plan 和 Action；Kernel、
Budget、Tool Contract、Verifier、Store 和 Domain Service 共同组成 Harness。Policy 不能修改
Harness 的权限和资源限制。

## 3. Stage 11A：执行协议、Tool Contract 与 Budget

### 3.1 AgentPlan 与 DAG

核心对象位于 `backend/app/agent/dynamic/models.py`：

- `AgentPlan`：objective、version、assumptions、constraints 和 2～6 个 Step；
- `PlanStep`：dependencies、success criteria、allowed tools、status、evidence、attempts；
- `AgentAction`：一次且仅一次公开动作；
- `Observation`：环境反馈，不等同于模型推断；
- `VerificationResult`：`passed / failed / inconclusive`；
- `ReplanProposal`：只描述未完成部分的新结构。

`AgentPlan.validate_dag()` 使用 DFS color marking 检查 reference integrity 和 cycle，并限制
同一时刻最多一个 active Step。Replan 不原地覆盖旧计划，而是产生新 version。

### 3.2 公开 Action space

模型只允许输出五种动作：

| Action | 含义 | 能否写领域数据 |
|---|---|---:|
| `call_tool` | 调用当前 Step allowlist 中的 Tool | 取决于 Tool Contract |
| `present_content` | 展示讲解、提示、练习或结果 | 否 |
| `request_input` | 创建结构化 Interrupt | 否 |
| `complete_step` | 提交 evidence，等待 Verifier | 否 |
| `finish_run` | 请求结束，必须通过 Plan Verification | 否 |

`reason_summary` 只记录简短公开理由，不保存 hidden chain-of-thought。

### 3.3 Tool Registry

`backend/app/agent/dynamic/tools.py` 实现：

```text
model arguments
  → Pydantic validation
  → registry/Plan allowlist
  → approval policy
  → deterministic idempotency key
  → asyncio timeout
  → Application Service
  → bounded result
  → standard ToolError
  → Trace + Observation
```

首批 Tool：

- 读：`goal.get_state`、`session.get_state`、`learner.get_mastery`、
  `resource.search`、`exercise.get`；
- 受控写：`exercise.grade`、`review.schedule`、`session.complete`。

`exercise.grade` 不让模型提供 idempotency key。Executor 根据
`run_id + step_id + tool_name + canonical arguments` 生成稳定 SHA-256 key。相同动作在 crash
后重放仍命中同一领域写入；修改答案会产生不同 key。

### 3.4 Budget Ledger

`BudgetLedger` 在调用前做 preflight，在调用后按真实 usage 记账。限制包括：

- Loop Step；
- Model Call / Tool Call；
- input/output/total Token；
- wall-clock deadline；
- same action；
- consecutive failure；
- Replan 次数。

模型只能在 Context 中读取 Budget，不能修改。超限统一形成
`budget_exhausted / deadline_exceeded / verification_failed` 等 `terminal_reason`。
等待用户输入的时间会在 resume 时从 deadline 中扣除，避免 human latency 消耗执行预算。

## 4. Stage 11B：只读 Dynamic Loop 与 Shadow

`DynamicAgentKernel.execute()` 每轮只执行一个 Action，并在下一轮前保存
`DynamicAgentState`。主要循环如下：

```text
poll cancel
→ budget preflight
→ select ready Step
→ compile Context
→ model decide
→ validate Action
→ repeat detection
→ execute one Action
→ save Observation/Budget/State
→ verify/replan/continue
```

默认 `.env.example` 中：

```text
LEARNLOOP_AGENT_DYNAMIC_WRITES_ENABLED=false
```

此时 Planner 只能看到 read-only Tool Schema。即使模型猜到写 Tool name，Kernel 仍会在
Registry/Step/Shadow 三层检查中拒绝，而不是仅依赖 Prompt 约束。

### Minimal Context Compiler

Stage 11 的 `MinimalContextCompiler` 仅编译：

- 当前 objective；
- 当前 Plan version 和 Step；
- 最近 12 个 Observation；
- Budget 与 usage；
- 当前 Step allowlist 过滤后的 Tool Schema。

它提供保守 Token estimation、Observation 裁剪计数和 source id。完整 tokenizer、compaction、
Artifact 和 source mapping 属于 Stage 12，当前实现没有越界假装完成这些能力。

> 后续状态：上述 Stage 11 最小实现已在 Stage 12 升级为完整 `ContextCompiler`，包括
> Artifact、Compaction、Token partition 和 Context Snapshot。参见
> [Stage 12 Context Engine 实现说明](stage-12-context-engine.md)。

## 5. Stage 11C：Verifier、Replanner、Interrupt 与写入

### 5.1 Deterministic-first Verification

`complete_step` 不能靠模型一句“完成了”通过。Verifier 要求：

1. evidence id 必须引用真实 Observation；
2. Observation 必须属于当前 Plan Step；
3. evidence 中不能包含失败结果；
4. 所有必要 Step 完成后才能结束 Run。

目前学习 Session 的结构、Tool 成功和领域最终值优先由确定性代码验证。需要语义 Rubric 的
Model Verifier 可后续插入，但 `inconclusive` 永远不能当作成功。

### 5.2 Constrained Replanner

永久 Tool failure 或连续失败触发 Replanner。`apply_replan()` 强制：

- completed Step 必须继续存在；
- completed Step 的 objective 和 evidence 不可修改；
- 新 Plan 重新执行 DAG 和 Tool availability 校验；
- version 单调增加；
- Replan 次数受 Budget 限制。

这避免模型通过“改写历史”隐藏失败，也让 Plan diff 和轨迹能够审计。

### 5.3 Interrupt / Resume

Interrupt 持久化字段包括 id、type、prompt、plan step、allowed actions 和 created time。
Resume 时验证：

- Run 仍是 `awaiting_input`；
- 可选 `interrupt_id` 与当前 Interrupt 匹配；
- 进程内 Lock 和 Run version 阻止 concurrent resume；
- 用户输入转换为 `user.input` Observation；
- dynamic state 清除 pending Interrupt 后继续同一个 Plan。

### 5.4 Controlled writes

写 Tool 只能在配置开放且 Plan allowlist 明确允许时执行。写入仍经过 Application Service：

- `exercise.grade`：确定性评分，并原子写 Attempt、Mastery 和 Review；
- `review.schedule`：验证评分产生的复习日期；
- `session.complete`：只有已有持久化 Attempt 才能完成 Session。

所以 Dynamic Agent 不能直接声明 Mastery，也不能绕过领域事务。

## 6. Stage 11D：双内核、对照和回滚

创建 Session Run：

```http
POST /api/v1/agent/study-sessions/{session_id}/runs?engine_version=fixed_v1
POST /api/v1/agent/study-sessions/{session_id}/runs?engine_version=dynamic_v2
```

同一 Session 可创建多个独立 Run，`attempt_no` 按 Engine 递增。对照 API：

```http
GET /api/v1/agent/comparisons?fixed_run_id=...&dynamic_run_id=...
```

它要求两个 Run 的 graph/resource 相同，输出完成状态、终止原因、Action、拒绝动作、重复 Tool、
Model/Tool 调用、Token 和总耗时 delta。任务质量仍应由 Scenario expected end-state 判断，不能
只根据 Token 少就宣布 v2 更好。

回滚不需要迁移执行中的 Run：新 Session 创建时把 `engine_version` 改回 `fixed_v1` 即可。
两个 Engine 不会在同一个 Run 内隐式切换。

## 7. Durable state 与 Trace

### Run protocol

Run 新增：

- `engine_version`；
- `parent_run_id`（为后续 Subagent 预留，但 Stage 11 不创建子 Agent）；
- `attempt_no`；
- `terminal_reason`；
- `cancel_requested`；
- optimistic `version`。

状态为：`created / running / awaiting_input / completed / failed / cancelled`。

### SQLite 表

| 表 | 用途 |
|---|---|
| `learnloop_agent_runs` | Run identity、Engine、生命周期、取消和并发版本 |
| `learnloop_agent_events` | 可重放 SSE Event |
| `learnloop_tool_calls` | Tool 参数摘要、状态、耗时和错误 |
| `learnloop_model_calls` | Prompt/model/version/Token/耗时 |
| `learnloop_dynamic_agent_states` | resume 所需完整 durable state |
| `learnloop_agent_plan_versions` | 不可覆盖的 Plan 历史版本 |

Store 启动迁移会 table-rebuild v1 Run 表，移除 `(graph_kind, resource_id)` unique constraint，
并把旧数据标记为 `fixed_v1`。旧固定图和事件表继续可读。

### 新 Trace Event

`plan_created`、`plan_rejected`、`plan_replanned`、`action_decided`、`action_rejected`、
`content_presented`、`observation_recorded`、`verification_completed`、`context_compiled`、
`budget_updated`、`run_paused`、`run_cancelled`。

Observation Event 只保存摘要和 data keys；答案参数在 Tool Trace 中只保存数量。用于 resume 的
完整值保存在 dynamic state，而不是复制到每条 Trace。

## 8. API、配置与 UI

新增或扩展：

- `POST /agent/study-sessions/{id}/runs?engine_version=...`；
- `POST /agent/runs/{id}/resume`，支持 `interrupt_id`；
- `POST /agent/runs/{id}/cancel`；
- `GET /agent/runs/{id}/plans`；
- `GET /agent/runs/{id}/trace`，包含 dynamic state 和 Plan versions；
- `GET /agent/comparisons`。

前端 Agent Trace 页面显示 Engine、attempt、terminal reason、当前 Plan、Step status、Tool
allowlist、evidence 数量以及 Step/Model/Tool/Replan Budget。

所有 Agent 配置均使用 `LEARNLOOP_AGENT_` 前缀，默认值和边界见 `.env.example` 与
`backend/app/config.py`。

## 9. 安全与 failure handling

- Prompt Injection：Prompt 明确把 Tool/Resource 内容标为 untrusted data；真正权限由代码
  allowlist 控制，因此恶意文本不能增加 Tool。
- Invalid model output：StructuredModel 只允许一次 bounded repair，仍失败则 Run 明确失败。
- Tool timeout：映射为 retryable `ToolErrorKind.TIMEOUT`，受 consecutive failure 和 Replan
  上限约束。
- Repeated action：使用 canonical Action signature 检测，不依赖模型承认循环。
- Cancellation：持久化 `cancel_requested`；Dynamic Loop 在决策和 Tool 前轮询。
- Side Effect：写 Tool 使用稳定 idempotency key，Application Service 负责 transaction。
- Hidden reasoning：不保存，只记录 `reason_summary` 和公开 evidence。

## 10. 测试与代码阅读顺序

建议按下列顺序审查和学习：

1. `dynamic/models.py`：理解 Agent 的公开协议和不变量；
2. `dynamic/budget.py`：理解确定性资源边界；
3. `dynamic/tools.py`：理解 Tool Contract 与 Side Effect isolation；
4. `dynamic/context.py`：理解 Just-in-time Context；
5. `dynamic/verifier.py`：理解 evidence-driven completion 和 Replan invariant；
6. `dynamic/policy.py` 与 `prompts/dynamic_agent.py`：理解模型 Policy；
7. `dynamic/kernel.py`：串联完整 Agent Loop；
8. `execution/store.py` 与 `runtime.py`：理解 durable execution 和双内核；
9. `tests/test_dynamic_agent_core.py`：从确定性场景理解 happy path 与 Interrupt。

测试覆盖 Plan cycle、Replan evidence、Budget、Tool allowlist/schema、完整 verified Loop、
Interrupt/resume 和 paired-run 输入约束。数据库/Checkpoint 回归继续由已有测试覆盖。

## 11. 明确不属于 Stage 11 的内容

- 长 Session compaction、Artifact Store 和精确 tokenizer：Stage 12；
- 跨 Session Memory：Stage 13；
- Gap-driven multi-hop RAG 和 Citation Verifier：Stage 14；
- Subagent、并行委派：Stage 15；
- Reflection、Skill Library：Stage 16；
- SFT、Preference Optimization、Agentic RL：Stage 17。

这些能力不会提前混入 Kernel，避免无法通过消融判断复杂度是否真正带来收益。
