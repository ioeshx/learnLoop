# LearnLoop v2 Stage 15：Subagent-as-Tool 实现说明

本文记录 Stage 15 的实际实现，重点解释 Lead Learning Agent 如何把一个复杂、只读的研究任务委派
给隔离的 Researcher child Agent，同时保持 scope、Tool、预算、取消和 Evidence 的控制权。核心目标
不是增加 Agent 数量，而是建立可审计的 Delegation Boundary。

## 1. 交付结果

本阶段交付以下闭环：

1. 通用 `DelegationRequest / DelegationResult / DelegationBudget / DelegationUsage` Contract；
2. `Lead Run → Delegation → Researcher child Run` 持久化关系；
3. 串行 `delegate.research` Tool、trusted invocation metadata 和最小 Context 隔离；
4. Token reservation、Query/Source/Deadline、最大 child 数和 duplicate fingerprint；
5. 父子取消传播、Deadline、失败映射和 Replanner fallback 信号；
6. Lead-side Claim/Evidence/Citation re-validation；
7. child Run/Delegation API Trace、前端 Delegation Tree 和冻结评测集。

整体路径如下：

```text
Lead Dynamic Agent
  Plan Step + Tool allowlist
           │
           ▼
    delegate.research({objective})
           │ ToolExecutor injects trusted run_id / step_id
           ▼
      DelegationService
  ┌────────┼──────────────────────────────────────────────┐
  │ scope  │ route/duplicate/child-count/budget/deadline │
  │ check  │             Policy Enforcement             │
  └────────┼──────────────────────────────────────────────┘
           │
           ▼
 Researcher child Run
 {task, goal/node scope, research.search, sub-budget}
           │
           ▼
 Stage 14 Research Tutor → Evidence/Claim/Citation Trace
           │
           ▼
 Lead-side graph verification + compression
           │
           ▼
 {summary, supported Claims, cited Evidence, gaps, usage, child_run_id}
           │
           ▼
 Lead Observation → verify / continue / fallback / request_input
```

## 2. 为什么只启用 Researcher

路线图列出 Researcher、Curriculum、Tutor 和 Evaluator 四种角色；详细实施计划要求第一版只实现
Researcher，再依据真实失败聚类选择后续角色。本阶段在 `SubagentRole` 中冻结四类通用身份，但只
注册 `delegate.research`。

这是有意的 capability boundary：

- Researcher 只读，适合最先验证 Context isolation 和 delegation cost；
- Curriculum 会影响长期学习结构；
- Tutor 会直接影响教学对话；
- Evaluator 可能影响成绩和 Mastery projection。

后三者在没有专用 Verifier、权限和失败集之前不进入 Tool Registry。模型即使输出这些 role，也没有
可调用 Tool。这比依靠 Prompt 写“暂时不要调用”更可靠。

## 3. Lead Agent 与 Subagent-as-Tool

系统仍只有一个拥有用户交互和学习流程控制权的 Lead Agent。Researcher 对 Lead 来说是一个有
Schema、有超时、有结果裁剪的只读 Tool：

- Lead 决定是否委派、如何使用结果和是否请求用户；
- Researcher 不能修改 Plan、Session、Mastery、Review 或 Memory；
- Researcher 不能再调用 `delegate.*`，因此不会递归生成 Agent tree；
- child failure 作为 `ToolError` 回到既有 Replanner；
- child success 也不能直接完成 Lead Step，仍需 Stage 11 Verifier。

简单资料问题继续使用 `research.ask`。`DelegationService` 会用 Stage 14 router 重新检查 objective，非
`multi_step_research` 请求直接拒绝，从 Harness 层保证简单任务的 delegation rate，而不是完全相信
Planner 自律。

## 4. Trusted ToolInvocation 与 scope isolation

`delegate.research` 的模型输入只有：

```json
{"objective": "比较 BFS 与 DFS，并解释各自限制"}
```

它不接受 `parent_run_id`、`goal_id`、`knowledge_node_id`、Tool allowlist 或 Budget。`ToolExecutor` 在
Schema validation 之后创建 `ToolInvocation`，注入真实 `run_id / plan_step_id / idempotency_key`。
`DelegationService` 再从 parent durable state 读取 goal/node scope。

这样避免 confused-deputy attack：模型不能在参数里伪造另一个 goal ID，让拥有更高资料权限的
Researcher 代为读取。额外字段由 Pydantic `extra=forbid` 拒绝。

Researcher 获得的 Context 仅包含：

- normalized objective；
- parent state 派生的 goal/node scope；
- 私有 allowlist `research.search`；
- DelegationBudget。

它不继承 Lead 的 recent Observations、完整 Context Snapshot、用户回答、Memory partition、写 Tool
或其他 child result。模型调用通过 ContextVar 绑定 child Run，Trace 不会错误记到 parent Run。

## 5. Delegation Contract

### DelegationRequest

Request 是 immutable task envelope，包含：

- `id / parent_run_id / plan_step_id / role`；
- `objective / goal_id / knowledge_node_id`；
- `allowed_tools`；
- `allocated_tokens / max_queries / max_sources / deadline_seconds`；
- stable `fingerprint` 和创建时间。

### DelegationResult

Result 只返回压缩公共结果：

- `summary`；
- included atomic Claims；
- non-unsupported Citations；
- 被 Citation 实际使用的 accepted Evidence；
- `unresolved_questions`；
- allocated/used Token、Query、Source 和 duration；
- parent/child/delegation IDs、status 和 failure code。

不返回 hidden reasoning、private Context、rejected Chunk 或无引用 Claim。Pydantic graph validator 确保
Citation 只能引用 Result 中真实存在的 Claim/Evidence，completed result 的每个 Claim 必须有
Citation。

## 6. child Run 与持久化

每次新委派创建：

```text
learnloop_agent_runs
  parent: graph=daily_learning, engine=dynamic_v2
      └── child: graph=researcher, engine=dynamic_v2, parent_run_id=...

learnloop_delegations
  delegation_id
  parent_run_id / child_run_id
  role / fingerprint / status
  request_json / result_json / used_tokens
```

Delegation 属于执行轨迹，和 Agent Run、Context Snapshot 一样存入 `checkpoints.db`，不进入业务
`learnloop.db`。`request_json` 在 child 开始前写入，`result_json` 在验证和压缩后原子更新。用户删除
Lead Run 时，Runtime 先按 descendant 顺序删除 child，再删除 parent，避免孤儿 Trace。

事件同时写 parent 和 child：

- `delegation_started`；
- `delegation_completed`；
- `delegation_failed`；
- `delegation_cancelled`；
- `delegation_reused`。

parent 事件适合重放决策，child `run_started/run_completed/run_failed/run_cancelled` 适合独立查看执行。

## 7. Duplicate prevention 与串行执行

Fingerprint 输入为：

```text
parent_run_id + role + normalized objective + goal_id + knowledge_node_id
```

它同时受两层保护：

1. per-parent `asyncio.Lock` 保证当前 Runtime 内串行；
2. SQLite `UNIQUE(parent_run_id, fingerprint)` 处理 crash retry 或多 worker race。

已经完成的等价请求返回原 `child_run_id` 和 `reused=true`，不会再次消耗 Research Token。正在执行的
等价请求被拒绝，而不是等待后再次启动。不同 objective 最多创建配置允许的 child 数。

第一版没有并行执行 child。详细实施计划要求先稳定串行语义；只有两个任务都独立、只读、无共享写
入且真实 wall-clock benchmark 显示收益时，后续才允许 bounded parallel fan-out。

## 8. Hierarchical Budget

Subagent 预算不是独立无限额度，而是 parent Budget 的 reservation：

```text
allocated = min(
  per-child max_tokens,
  parent max_total_tokens - parent used/reserved tokens,
  total delegation pool remaining
)
```

创建 child 前若不足 500 Token，委派直接拒绝。Researcher 把 allocation 映射为 Stage 14
`max_context_tokens`，同时受 max Query、max Source 和 Deadline 约束。

child 返回后，Lead `BudgetLedger.reserve_delegation()` 将整笔 allocation 记入
`usage.delegated_tokens` 和 `usage.total_tokens`。这里采用 reservation accounting，而不是仅按估算
usage 收费：实际 Provider usage 延迟或 child failure 都不能让第二个 child oversubscribe parent。
Result 同时记录 estimated used tokens，UI 显示 `used / allocated` 供成本分析。

只有 `delegate.*` namespace 的 Tool result 可以触发 reservation。普通检索文本即使包含
`allocated_tokens` 字段也不能消耗 Lead Budget，避免 untrusted Tool output 制造 budget denial。

## 9. Cancellation 与 Deadline propagation

取消采用 cooperative propagation：

1. `AgentRuntime.cancel_run(parent)` 持久化 parent cancel signal；
2. Runtime 遍历 descendants，对非终态 child 写 cancel signal；
3. DelegationService 在等待 Researcher 时轮询 parent 和 child durable state；
4. 发现取消后 cancel research task，完成 child/delegation terminal projection；
5. Tool 返回 `cancelled` error；Dynamic Kernel 在进入 Replanner 前再次检查 parent cancel；
6. parent 进入 `cancelled`，预算不再增长。

Deadline 使用 event-loop monotonic time，不依赖模型。到期后 child Run 为 `failed /
deadline_exceeded`，Delegation 为 `deadline_exceeded`，ToolExecutor 映射成标准 `timeout` ToolError。

Runtime shutdown 导致 asyncio cancellation 时，Service 也会先把 child 投影为 cancelled，再向上重新
抛出 `CancelledError`，避免重启后留下永久 running 的 child。

## 10. Lead-side result verification

Researcher 已有 Stage 14 Citation Verifier，但 Lead boundary 不直接信任 child 声明。返回前再次检查：

1. Research Trace 必须存在；
2. Trace goal/node 必须与 DelegationRequest 完全一致；
3. Evidence 必须是 `accepted`；
4. Claim 必须 `included_in_answer=true`；
5. Citation 不能是 unsupported，且必须连接 included Claim 与 accepted Evidence；
6. 每个 included Claim 至少有一条有效 Citation；
7. estimated usage 不得超过 allocation。

任一检查失败时 child/Delegation 进入 failed，结果不进入 Lead Context。ToolExecutor 返回带 child ID
的标准错误，现有 `should_replan` 路径会调用 Replanner。Prompt 明确要求降级为 `research.ask` 或
`request_input`，并禁止重复委派同一任务。

## 11. API 与前端

现有 `GET /agent/runs/{run_id}/trace` 新增：

- `delegations`；
- `child_runs`。

新增 `GET /agent/runs/{run_id}/children`，返回直接 child Runs 和 Delegation records。child 本身仍可用
现有 Run/Trace/Event API 查询，因此不产生第二套调试协议。

前端 Agent Trace 页面增加：

- child 页面上的 Parent Run 跳转；
- Lead 页面上的 Delegation Tree；
- role/status/objective；
- Token `used / allocated` 和 Query budget；
- unresolved questions；
- Child Run 跳转；
- Lead dynamic budget 中的 `delegated_tokens`。

UI 不展示 hidden reasoning 和完整私有 Context。

## 12. 配置

```text
LEARNLOOP_AGENT_MAX_SUBAGENTS=3
LEARNLOOP_AGENT_DELEGATION_MAX_TOKENS=6000
LEARNLOOP_AGENT_DELEGATION_MAX_QUERIES=6
LEARNLOOP_AGENT_DELEGATION_MAX_SOURCES=10
LEARNLOOP_AGENT_DELEGATION_DEADLINE_SECONDS=60
```

这些值都有 Pydantic range validation。Stage 14 ResearchBudget 仍在 child 内生效；Delegation limits
只能进一步收紧，不能扩大 Research Tutor 全局上限。

## 13. 测试与评测

Service/SQLite tests 覆盖：

- scope 从 trusted parent state 派生；
- child Run 身份和 parent relation；
- compressed verified result；
- simple task 拒绝；
- parent Token 不足拒绝；
- persistent duplicate reuse；
- parent cancellation；
- Deadline；
- ungrounded child Claim 拒绝；
- ToolInvocation 防伪造；
- child failure → ToolError；
- Runtime descendant cancellation；
- parent Budget reservation。

`evals/datasets/delegation_v1.json` 冻结 simple delegation rate、scope safety、cancel propagation、
duplicate prevention、fallback、task success lift、wall-clock ratio 和 Token overhead ratio：

```bash
python scripts/run_evals.py \
  --dataset backend/evals/datasets/delegation_v1.json \
  --output backend/evals/reports/delegation-latest.json
```

冻结 JSON 是 engineering regression contract，不是生产效果证明。真实“同预算质量提升”和
“wall-clock 改善”必须在相同模型、资料、问题、Context/Token 上限和足够重复次数下做 paired
evaluation。第一版串行 Researcher 不宣称获得 parallel speedup。

## 14. Failure Semantics

- simple objective：Tool validation failure，Lead 使用普通 Tutor/Research；
- scope 缺失或 parent 非 dynamic Lead：拒绝，不创建 child；
- child 数或 Token pool 耗尽：拒绝，不透支 parent；
- duplicate terminal request：复用原 Result；
- duplicate running request：拒绝，不创建第二个 child；
- insufficient Evidence：返回 verified partial result 与 gaps，Lead 可请求资料；
- invalid/missing Trace 或 ungrounded Claim：child failed，结果不进入 Context；
- Deadline：child failed/deadline_exceeded，Tool timeout；
- parent/child cancel：child cancelled，Lead 不进入 Replanner model call；
- Subagent exception：持久化 failed Result，Replanner 降级；
- Runtime shutdown：终止 child task并投影 cancelled。

## 15. 代码阅读顺序

1. `app/agent/delegation/models.py`：通用 Contract 和 result graph invariants；
2. `app/agent/delegation/service.py`：scope、routing、fingerprint、budget、lifecycle、verification；
3. `app/agent/dynamic/tools.py`：ToolInvocation 和 `delegate.research` adapter；
4. `app/agent/dynamic/budget.py`：hierarchical reservation accounting；
5. `app/agent/execution/store.py`：child Run 与 Delegation persistence；
6. `app/agent/execution/runtime.py`：cancel/delete propagation；
7. `app/api/routes/agent_runs.py` 和前端 Agent Trace：Delegation Tree；
8. `tests/test_subagent_delegation.py`：核心安全与恢复门禁；
9. `evals/datasets/delegation_v1.json`：冻结对照指标。

复杂函数与类的注释重点解释 confused deputy、Policy Enforcement Point、Context isolation、stable
fingerprint、hierarchical budget、reservation accounting、cooperative cancellation、child lifecycle 和
Lead-side verification 等高级机制。

## 16. 验收结论与后续边界

Stage 15 的工程能力已经形成：Lead 能把复杂本地研究作为 Tool 委派给隔离 Researcher，父子身份、
scope、预算、取消、结果和失败都可追踪，简单任务不会通过 Harness 创建 child。

当前明确不做：Curriculum/Tutor/Evaluator execution、并行 fan-out、共享 Memory、child 写 Tool、递归
delegation。只有真实失败聚类证明这些能力能在相同安全与 Token 预算内提升质量或 wall-clock，才应
按新的角色专用 Contract、Verifier 和评测集逐个启用。
