# LearnLoop v3 执行计划

## 1. 执行原则

- 先写 contract 和 invariant，再接入 Dynamic Agent；
- 每个阶段先 Shadow/dry-run，再成为 enforcement path；
- 所有复杂函数使用中文注释，专业术语保留英文；
- 不持久化 hidden Chain-of-Thought、credential 或未经脱敏的完整 Context；
- Schema、事件、API、前端、评测和文档作为同一功能交付；
- 每组功能通过测试后独立 commit；
- 真实外部服务未接入时，明确使用 Fake/fixture，不伪造生产结果。

## 2. Stage 18 文件级计划

### 新模块

```text
backend/app/agent/policy/
├── models.py       # DataLabel, Grant, Request, Decision, Audit
├── engine.py       # deterministic PDP
├── lineage.py      # monotonic taint propagation
└── service.py      # persistence/query/simulation
```

### 集成点

- `dynamic/models.py`：Tool result 与 Context source 的 label reference；
- `dynamic/tools.py`：ToolExecutor 作为 PEP，执行前调用 PDP；
- `dynamic/kernel.py`：注入 trusted subject/grants，记录 policy event；
- `dynamic/context.py`：拒绝 `secret` source，保留 lineage id；
- `delegation/`：Child Grant attenuation；
- `execution/store.py`：policy audit table；
- `execution/models.py`：`policy_evaluated/policy_denied` events；
- `api/routes/policies.py`：audit、decision、dry-run；
- `frontend/src/app/agent-policy/`：只展示 metadata/reason code。

### 测试

- trust join 单调性；
- secret Context fail closed；
- high-risk Tool 无 Grant deny；
- approval-required 不等同于 allow；
- Child capability attenuation；
- untrusted Tool output 不可变为 system trust；
- retry 使用相同 decision fingerprint；
- audit 不包含 secret value。

## 3. Stage 19 文件级计划

```text
backend/app/infrastructure/llm/gateway/
├── models.py       # Profile, Requirement, Route, Health
├── router.py       # deterministic score/filter
├── circuit.py      # state machine
└── provider.py     # ModelProvider facade
```

### 路由顺序

```text
requirements
→ capability filter
→ Policy/data-residency filter
→ deadline/cost preflight
→ healthy providers
→ deterministic score
→ invoke
→ retryable failure only
→ circuit update
→ compatible fallback
```

### 配置

- 保留现有单 DeepSeek 配置兼容层；
- 新增 versioned route profiles；
- secret API key 仍只进入 Provider，不进入 route record；
- 没有可用 route 时明确失败，不偷偷使用弱能力模型。

### 测试

- exact capability match；
- cost/latency tie-break；
- retryable fallback；
- auth error no fallback；
- circuit open/half-open/close；
- deadline/budget preflight；
- StructuredModel observer 记录 route metadata。

## 4. Stage 20 文件级计划

```text
backend/app/agent/team/
├── models.py       # AgentCard, TeamTask, Part, Artifact
├── registry.py     # trusted role adapters
├── scheduler.py    # DAG + bounded concurrency
├── verifier.py     # artifact boundary
└── service.py      # lifecycle / cancellation / trace
```

### 第一批 Role

| Role | 输入 | 输出 | Tool scope |
| --- | --- | --- | --- |
| Researcher | bounded research question | cited Research Artifact | local retrieval only |
| Evaluator | answer + rubric + evidence refs | score/violations Artifact | no write Tool |

Evaluator 不能用语言模型的自评分覆盖 deterministic Verifier；它只提供候选分析。

### Scheduler

- DAG 必须无环；
- ready Tasks 最多并发 `max_parallel_children`；
- Parent Token/deadline 先 reservation，再启动 Child；
- fingerprint 防止 duplicate work；
- cancellation breadth-first 传播；
- partial failure 按 manifest 决定 fail-fast 或 verified-partial。

## 5. Stage 21 文件级计划

```text
backend/app/agent/reliability/
├── models.py       # Scenario, Trial, Fault, Grade, Report
├── faults.py       # deterministic injectors
├── graders.py      # final-state + trajectory + safety
└── runner.py       # seeded k trials and aggregation
```

### 指标

```text
pass@k             = 1 - Π(1 - success_i)    # observed form: any success
pass^k             = Π success_i             # observed form: all success
recovery_rate      = recovered_faults / injected_faults
redundancy_rate    = duplicate_actions / actions
safety_rate        = safe_trials / trials
cost_p95           = P95(tokens or estimated cost)
worst_slice_score  = min(score by fault/task/role slice)
```

当 trials 不同分布时不使用独立同分布的解析估计，直接报告 observed rate 与样本数。

## 6. 数据与迁移策略

- 业务实体继续使用主 SQLite/Alembic；
- Run 强关联、可按 retention 清理的 Policy/Team/Trial telemetry 放 checkpoint SQLite；
- 新表由对应 Store setup 创建，并提供 index/unique/idempotency constraint；
- 不修改已有 v2 event payload 的含义，只新增 event kind；
- 所有 JSON contract 都有显式 schema/version；
- Artifact 使用 SHA-256，secret 只存 redacted metadata。

## 7. API 与前端策略

内部页面：

- `/agent-policy`：Policy decisions、deny/approval、taint lineage；
- `/model-gateway`：route、health、fallback、cost metadata；
- `/agent-team`：Task DAG、Child lifecycle、Artifact provenance；
- `/agent-reliability`：scenario、fault、`pass@k/pass^k`、worst slices。

所有 mutation endpoint 使用 admin flag、optimistic version 或 idempotency key。普通学习页面只显示
对用户有用的审批和错误，不暴露内部 Prompt、Policy internals 或 provider credential。

## 8. 每阶段 Definition of Done

1. Contract、invariant 和 failure semantics 已编码；
2. happy path、deny/failure、crash/retry、concurrency 有测试；
3. API schema 与前端类型一致；
4. frozen eval 指标全部达标；
5. 文档说明架构、边界、已知限制和运维开关；
6. Ruff/Mypy/Pytest/前端检查通过；
7. Git diff 无未预期文件，按功能 commit；
8. 未满足真实外部/训练门禁的能力明确标为未启用。

## 9. 完成审计表

| Requirement | 权威证据 |
| --- | --- |
| 规划新版本特性 | `v3-agent-gap-analysis.md`、`v3-agent-roadmap.md` |
| 说明具体执行方式 | 本文的文件、顺序、测试、迁移和 DoD |
| 在 v3 分支执行 | `git branch --show-current` 与提交历史 |
| 按功能 commit | roadmap 推荐提交序列与实际 `git log` |
| 实现完整 | Stage 18–21 code/API/UI/eval/docs 与全量验证输出 |

只有上表每项都有当前分支证据后，v3 目标才可标记完成。

## 10. 实际完成审计（2026-09-19）

v3 已在 `v3` 分支完成 Stage 18–21。实际提交按 capability、enforcement、operations、test/eval 和
documentation 分组，而不是把整个版本压成一个不可审查的大提交。

| Stage | 实际提交 | 交付证据 |
| --- | --- | --- |
| 规划 | `f68233a` | gap analysis、roadmap、file-level execution plan |
| Stage 18 | `450b070`、`e90ccca`、`9c7729a`、`7086a56` | Policy contract、Tool/Context PEP、audit/API/UI、安全冻结集 |
| Stage 19 | `49856ab`、`47f7166`、`d1423bc` | capability route、fallback/circuit、persistence/API/UI、fault gates |
| Stage 20 | `b3596bb`、`f349bb8`、`14f99f7` | Team DAG、verified Artifact、operations console、scope/budget/cancel gates |
| Stage 21 | `83fe006`、`fc44bcc`、`d0d304b` | seeded Trial、Fault injection、pass-k、API/UI、冻结评测、架构文档 |
| 质量收口 | `d498078` | 正式 Ruff/Mypy scope 零错误 |

最终验证：

- 后端 Ruff：`app evals tests ../scripts/run_evals.py` 全通过；
- 后端 Mypy：按仓库正式命令检查 `app evals ../scripts/run_evals.py`，191 个 source files 全通过；
- 后端 Pytest：154 passed；
- 前端 ESLint、TypeScript、Vitest、Next.js production build 全通过，12 个 test files / 19 tests；
- Reliability 冻结集 10 项 gate 全通过：Replay/Fault coverage/Recovery/Safety 均为 1.0，
  `pass@k=1.0`、`pass^k=0.5`、worst-slice `0.666667`；
- 工作区未生成或提交本地 Eval Report，报告输出仍遵循 `.gitignore`。

审计结论：v3 的 Trust/Policy、Model Gateway、Agent Team 和 Reliability Lab 已形成连续的 Agent
control plane。默认 Reliability Executor 仍是明确标注的 fixture；remote A2A、真实 Provider chaos、
process sandbox、multimodal 和在线训练不属于本版本完成声明。
