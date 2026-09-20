# Stage 16：Reflection 与 Skill Library 实现说明

## 1. 阶段结论

Stage 16 已把 LearnLoop 的“运行结束”扩展为一条受验证信号约束的经验闭环：

```text
terminal Dynamic Run
  → deterministic Verifier gate
  → evidence-bound Reflection
  ├─ failure → scoped Reflection recall → Planner 避免重复失败
  └─ repeated success → inert Skill candidate
                         → human review
                         → active immutable version
                         → scope-safe recall
                         → typed Plan 显式采用
                         → usage telemetry
                         → down-rank / quarantine / rollback
```

这里的 Reflection 和 Skill 都不是领域事实：

- Reflection 描述“Agent 怎样执行、哪里失败、什么步骤通过了验证”；
- Skill 描述“在什么条件下，可以参考哪组步骤与 Verifier”；
- Semantic Memory 继续由 Stage 13 的证据、置信度和信任边界管理；
- Research claim 继续由 Stage 14 的 Citation graph 管理；
- Skill 不能写入或覆盖上述两类内容。

第一版没有实现让模型自由编写可执行代码的 Skill，也没有自动发布。Skill 是受治理的
`Procedural guidance`，真正的能力仍来自当前 Run 的 Tool Registry、Plan Step allowlist、
Budget Ledger 和 Deterministic Verifier。

## 2. 本阶段交付

### 2.1 Agent 后端

新增 `app/agent/experience/`：

- `models.py`：Reflection、Evidence、Skill、Applicability、Step、Usage、Review 和 Revision
  的严格 Pydantic contracts；
- `service.py`：验证门禁、Reflection 生成、失败经验召回、候选 Skill 提取、人工审核、
  scope-safe recall、使用统计、自动隔离、版本升级与回滚；
- `__init__.py`：对外稳定边界。

Dynamic Agent 的 `AgentPlan` 新增：

```text
applied_skill_id
applied_skill_version
```

两个字段必须同时存在或同时为空。模型不能用一个模糊名称宣称自己采用了 Skill；Kernel 会检查
该精确版本是否确实出现在本次 Planner Context 的 recall 集合中。

### 2.2 持久化

Checkpoint SQLite 新增三张表：

| 表 | 作用 | 关键约束 |
| --- | --- | --- |
| `learnloop_reflections` | 保存结构化 Reflection | 每个 Run 最多一条，`run_id UNIQUE` |
| `learnloop_skills` | 保存 Skill version 与 lifecycle | `(family_key, version) UNIQUE` |
| `learnloop_skill_usages` | 保存一次 Run 对一个 Skill version 的使用 | `run_id PRIMARY KEY` |

Reflection 与 Skill 使用 JSON contract 持久化，是为了让 Pydantic schema 成为读写两端的同一
验证边界；常用查询字段仍独立成列，支持按 outcome、strategy、status 和时间建立 index。

Skill 内部同时保存 `source_run_ids` 和 `source_reflections`。后者是不可变的 provenance
snapshot，包含 Event sequence、Evidence hash 和公开摘要。因此即使普通 Run 按 retention policy
清理，已审核 Skill 仍然保留其验证来源，而不是变成无法解释的孤立 Prompt 片段。

### 2.3 API 与前端

新增 API：

```text
GET  /api/v1/agent/reflections
POST /api/v1/agent/runs/{run_id}/reflect
GET  /api/v1/agent/skills
POST /api/v1/agent/skills/{skill_id}/review
POST /api/v1/agent/skills/{skill_id}/status
POST /api/v1/agent/skills/{skill_id}/revisions
```

Run Trace 增加：

```text
reflections[]
skill_usage
```

新增 `/agent-skills` 内部管理页，可查看：

- Candidate / Active / Quarantined / Disabled / Rejected 状态；
- 精确 version、risk、适用关键词和 Required Tools；
- 每个步骤的 Tool 与 Verifier；
- 来源 Run；
- 成功率、平均 Tool 调用数和平均 Token；
- 人工发布、拒绝、隔离、全局停用和返回 Candidate 的操作。

管理写接口由 `LEARNLOOP_AGENT_SKILL_ADMIN_ENABLED` 控制。普通用户页不显示内部 Prompt 或
完整 Context；Run Trace 只展示可公开的 evidence metadata。

## 3. Reflection 架构

### 3.1 Verifier-first，而不是 Model-first

`ReflectionSkillService.process_run()` 只处理：

1. 状态为 `completed` 或 `failed` 的 terminal Run；
2. 存在 durable Dynamic Agent state；
3. Trace 中至少存在一个 `verification_completed` Event。

`cancelled`、只有模型自信文本、只有用户陈述、没有 Verifier 的异常 Run 都不会产生 Reflection。
该设计采用 `fail closed`：漏掉一条不确定经验，比把幻觉永久化为系统策略更安全。

### 3.2 Evidence graph

每条 `ReflectionEvidence` 只引用三类公开信号：

- `observation`：Tool 或用户输入形成的 Observation；
- `verification`：Deterministic Verifier 的 pass/fail/inconclusive；
- `terminal`：Run 的合法终态与原因。

Evidence 保存：

```text
kind
event_sequence
observation_id
summary
content_sha256
```

`ReflectionInsight` 不保存无来源的自由结论，而保存：

```text
statement + evidence_ids[]
```

Pydantic `model_validator` 强制所有 `evidence_ids` 都存在于同一 Reflection。这样无论 Reflection
来自 deterministic rule，还是未来改为 LLM-assisted extraction，入库前都必须形成闭合
Evidence graph。

### 3.3 Root cause 的保守语义

当前 root cause 不是对隐藏 `chain-of-thought` 的推测。它只做以下投影：

- 失败 Observation → “观察到该执行动作失败”；
- 未通过 Verifier → “该 success criterion 未验证通过”；
- terminal Event → 记录最终失败边界。

对应 improvement 也只建议“处理该引用失败，并重新运行 Deterministic Verifier”。它不会从一次
网络错误推断学习者能力，也不会从一段检索文本创建事实。

### 3.4 失败经验召回

失败 Reflection 不需要等到 Skill 形成。新 Run 初始化 Planner 前，Service 会按以下条件召回：

```text
outcome == failure
AND graph_kind matches
AND objective keyword matches
AND library enabled
```

召回结果进入独立 `prior_reflections` Context partition，Context Snapshot 保存
`reflection:{id}` source。Prompt 明确规定它只是 `execution experience`，不得用于回答领域问题、
写 Memory 或改变权限。

这条路径直接处理“相似失败高频重复”：Planner 可以看到以前哪个 Observation 或 Verifier 失败，
但仍要为当前环境生成新的 typed Plan。

## 4. Skill 提取与发布

### 4.1 Candidate extraction

只有 `success` Reflection 参与 Skill 提取。系统为 Plan 计算稳定 `strategy_key`：

```text
SHA-256(
  graph_kind,
  ordered Step dependency shape,
  sorted allowed_tools per Step,
  normalized success_criteria per Step
)
```

它刻意不使用 Run ID、时间或模型文本作为 key，避免同一策略因非语义字段变成多个 family；同时
保留 Tool、dependency 和 Verifier shape，避免只因目标文本相似就合并不同能力。

同一 `strategy_key` 至少出现 `agent_skill_minimum_source_runs` 个不同成功 Run 后，才创建
Candidate。默认阈值为 2。Candidate 包含：

- Applicability：`graph_kind`、`objective_keywords`、`required_tools`；
- Prerequisites；
- ordered Steps；
- 每个 Step 的 `allowed_tools` 与 `verifier`；
- source Run 与完整 Reflection snapshot；
- version、risk 和 lifecycle status。

### 4.2 Human review gate

Candidate 的初始状态为 `candidate`，Recall 查询只读取 `active`，所以它在人工审核前完全惰性。

审核请求必须携带：

```text
decision: publish | reject
expected_version
note
```

Store 使用 version 和 expected status 做 Compare-And-Swap 风格约束。重复审核、过时页面提交或
对非 Candidate 的发布都会得到 `409 Conflict`，不会静默覆盖新状态。

### 4.3 Immutable version 与 rollback

修改 Skill 内容不会原地覆盖。Revision API 以旧版为来源创建 `version + 1` 的新 Candidate：

```text
v1 active
  → create revision
v1 active + v2 candidate
  → review/publish v2
v1 disabled + v2 active
  → regression
  → activate v1
v1 active + v2 disabled
```

一个 family 同时最多保留一个 active version。旧 version、来源、统计和审核说明都保留，因此
回滚不依赖重新生成 Prompt，也不修改历史 Run。

## 5. Scope-safe Recall 与执行

### 5.1 Recall filter

Skill 只有同时满足以下条件才返回 Planner：

1. `status == active`；
2. 未超过 `valid_until`；
3. `graph_kind` 相同；
4. 至少一个 `objective_keyword` 匹配；
5. `required_tools ⊆ current allowed_tools`；
6. 全局 Skill Library 开启。

第五条是 capability non-escalation 的硬边界。Skill 如果需要当前 Run 没有的 Tool，会整体被排除；
系统不会删掉那一步后勉强执行，也不会为了匹配 Skill 动态注册 Tool。

### 5.2 Context provenance

Planner Context 新增两个可裁剪分区：

| 分区 | 优先级 | 内容 |
| --- | --- | --- |
| `prior_reflections` | 90 | 相关失败、Evidence 与改进候选 |
| `candidate_skills` | 85 | Active Skill 的精确版本与 Procedure |

它们低于 policy、objective、Tool schema 和 Budget 等 mandatory partitions。Context 不足时，
先丢弃经验项，并把 ID 写入 `ContextTruncation`；绝不会为保留 Skill 而裁掉安全策略。

### 5.3 Typed adoption

Planner 采用 Skill 时必须在 `AgentPlan` 中填写精确 `id + version`。Kernel 将其与本次 recall 集合
比较，未召回、版本不符或伪造的选择直接触发 `plan_rejected`。

采用后，Executor Context 只收到以下最小投影：

- id/version/name/description；
- Applicability 与 Prerequisites；
- ordered Steps、Tools 和 Verifier；
- risk 与 source Run IDs。

审核 note、管理历史和完整来源 Reflection 不进入每次 Decision Context，避免无关 Token 和内部
管理信息污染决策。

### 5.4 Runtime kill switch

Kernel 每轮循环都会重新读取 applied Skill：

```text
library enabled?
exact version exists?
status still active?
```

任意检查失败，Run 以 `verification_failed` 终止，不再执行下一个 Action。因此管理员将错误 Skill
设为 `disabled` 或 `quarantined` 后，正在运行但尚未进入下一轮的 Agent 也会停止使用它。

## 6. Usage telemetry 与自动降级

当 Plan 采用 Skill 时，系统为 Run 建立唯一 `SkillUsage`：

```text
run_id
skill_id / skill_version
status
succeeded
tool_calls
tokens
failure_type
created_at / completed_at
```

Run 进入 terminal state 后，Runtime 在生成 Reflection 前完成 Usage，随后重新计算该 Skill 的：

- `success_count` / `failure_count`；
- `success_rate`；
- `average_tool_calls`；
- `average_tokens`。

达到 `agent_skill_quarantine_min_uses` 后，若成功率低于
`agent_skill_quarantine_success_rate`，Active Skill 自动转为 `quarantined`。Quarantined Skill 不再
参与 Recall，并产生 `skill_quarantined` Trace Event。人工可检查分布变化、Tool contract 或来源
问题，再将它返回 Candidate 重新验证。

## 7. 与现有 Agent 子系统的边界

### Context Engine

Stage 16 复用 Stage 12 的 Token packing、source ID、Snapshot 和 Compaction。Reflection/Skill 是
新的 Context source，不绕开 Compiler 拼接 Prompt。

### Agent Memory

Reflection 不调用 `MemoryService.extract_and_store()`，Skill 也不写入 Semantic/Personal Memory。
这是防止“执行经验覆盖领域事实”的结构性隔离，而不只是 Prompt 提醒。

### Agentic RAG

Skill 可以要求 `research.ask`，但不能跳过 Stage 14 Evidence gate 或 Citation Verifier。
Reflection 中保存的是 Research Tool 的 Observation summary 与验证结果，不把 untrusted chunk
内容当作规则。

### Subagent-as-Tool

Skill 可以引用 `delegate.research`，但仍受当前 Plan Step allowlist、父 Run hierarchical Budget、
scope derivation 和 cancel propagation 约束。Skill 不携带新的 delegation scope。

## 8. Trace Events

本阶段新增：

| Event | 含义 |
| --- | --- |
| `reflection_created` | terminal Run 通过 Verifier gate 并生成 Reflection |
| `reflection_recalled` | Planner 收到一条匹配的失败经验 |
| `skill_candidate_created` | 重复成功轨迹形成惰性 Candidate |
| `skill_recalled` | Active Skill 精确版本进入 Planner Context |
| `skill_usage_recorded` | terminal Run 回写 Skill 使用结果 |
| `skill_quarantined` | 低成功率触发自动隔离 |

Event 只包含 ID、版本、计数、匹配关键词和公开 Evidence sequence，不包含系统 Prompt、hidden
reasoning 或完整敏感 Context。

## 9. 配置

```text
LEARNLOOP_AGENT_SKILL_LIBRARY_ENABLED=true
LEARNLOOP_AGENT_SKILL_ADMIN_ENABLED=true
LEARNLOOP_AGENT_SKILL_MINIMUM_SOURCE_RUNS=2
LEARNLOOP_AGENT_SKILL_RECALL_LIMIT=3
LEARNLOOP_AGENT_SKILL_QUARANTINE_MIN_USES=3
LEARNLOOP_AGENT_SKILL_QUARANTINE_SUCCESS_RATE=0.5
```

- `LIBRARY_ENABLED` 是 recall 与运行时使用的全局 kill switch；
- `ADMIN_ENABLED` 只控制管理写 API；
- source threshold 防止单次偶然成功直接成为 Skill；
- recall limit 控制 Context 成本；
- quarantine 参数决定 Canary 使用后的降级敏感度。

生产环境应把管理 API 放在管理员认证之后。本仓库当前是 local-first 单用户应用，因此本阶段先
提供显式配置门禁，没有伪装成完整 RBAC。

## 10. 测试与评测

### 10.1 确定性测试

`test_agent_experience.py` 覆盖：

- 没有 Verifier 的 Run 不产生 Reflection；
- Root cause 和 Improvement 的 Evidence graph 闭合；
- Reflection idempotency；
- 失败 Reflection 的 graph/objective scope recall；
- 两条不同成功 Run 才产生 Candidate；
- Candidate 在审核前不可召回；
- 缺少 Required Tool 或 graph 不匹配时不可召回；
- 伪造未召回 Skill 的 Plan 被拒绝；
- Revision 创建新版本且旧版本仍 Active；
- 新版发布后旧版 Disabled；
- 旧版可重新激活完成 rollback；
- 连续失败达到阈值后自动 Quarantine；
- 管理 API 能列出并发布 Candidate。

### 10.2 Frozen evaluation

新增 `skills_v1.json` 与 `reflection_skill_library` evaluator，报告：

- `reflection_grounding_rate`；
- `reflection_unverified_generation_rate`；
- `skill_recall_precision`；
- `skill_scope_safety_rate`；
- `skill_review_gate_rate`；
- `skill_degradation_safety_rate`；
- `skill_rollback_success_rate`；
- `skill_task_success_lift`；
- `skill_tool_call_ratio`。

最后两项是 Skill / no-Skill ablation。当前 Frozen dataset 是确定性的 engineering regression
gate，用于保证指标定义、方向和报告结构不回归；它不是生产流量上的统计显著性证明。进入真实
Canary 前仍需固定模型、Prompt、Tool Registry 和输入集，多次执行并报告置信区间。

## 11. 已知限制与后续方向

1. Candidate clustering 当前使用 deterministic strategy fingerprint，不做 embedding clustering；
   这降低误合并风险，但会漏掉语义相同、结构略有不同的轨迹。
2. Root cause extraction 保守地投影公开失败信号，没有进行 causal inference。
3. Skill 目前是 Procedural guidance，不是 WASM、Python 或 DSL executable artifact。
4. Applicability 使用 graph、keyword 和 Tool capability；后续可加入 learner state、resource domain
   和 distribution version，但必须保持可解释匹配。
5. 自动降级按总体成功率计算；真实生产需要分 cohort、Wilson interval、时间衰减和 concept drift
   detector，避免小样本震荡。
6. 管理页没有实现完整 RBAC。部署到多用户服务前，必须把 Review/Revision/Status API 接到组织
   权限和 Audit identity。
7. 当前 ablation 是 Frozen regression data。只有真实 shadow/canary 对照证明成功率或 Tool 成本
   稳定改善，Stage 16 才应被视为通过业务退出门禁。

这些限制是有意的阶段边界。Stage 16 的核心价值是先把 Experience 的 provenance、capability
boundary、versioning、kill switch 和 measurement 做正确，再考虑 Stage 17 的 Policy optimization
或 Agentic RL。
